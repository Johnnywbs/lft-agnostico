import json
import logging
import re
import shlex
import subprocess
import time

from .base import InfraBackend
from ..exceptions import NodeInstantiationFailed

NAMESPACE = "lft"
# 60s wasn't enough for a cold pull of a large image (ONOS is 500MB+) that
# containerd hasn't cached yet - a first run timed out mid-"Pulling image".
# Pre-importing expected images (see import_k3s_images.sh) avoids the pull
# entirely, but this needs to stay generous as a fallback for anything not
# pre-imported.
WAIT_TIMEOUT_S = 300


class DockerRunParseError(Exception):
    pass


# Brief: Parses a `docker run ...` command line, as produced by the twelve
# call sites across onos_topologies/ and profissa_lft/, into a dict of
# pod-relevant fields. Everything before the image is a docker flag (raises
# on anything unrecognized); everything from the image onward is passed
# through as args, whatever it looks like -- those are the target program's
# own CLI flags, not docker's.
def parse_docker_run(command: str) -> dict:
    tokens = shlex.split(command)
    if len(tokens) < 2 or tokens[0] != "docker" or tokens[1] != "run":
        raise DockerRunParseError(f"Not a docker run command: {command}")

    parsed = {
        "detach": False,
        "interactive_tty": False,
        "rm": False,
        "name": None,
        "network": None,
        "privileged": False,
        "cap_add": [],
        "sysctls": {},
        "ports": [],      # list of (host_port, container_port) strings
        "volumes": [],    # list of (host_path, container_path, read_only)
        "env": {},
        "dns": None,
        "memory": None,
        "cpus": None,
        "entrypoint": None,
        "image": None,
        "args": [],
    }

    def take_value(i: int, flag: str):
        tok = tokens[i]
        if "=" in tok:
            return tok.split("=", 1)[1], i + 1
        if i + 1 >= len(tokens):
            raise DockerRunParseError(f"Missing value for {flag} in: {command}")
        return tokens[i + 1], i + 2

    i = 2
    n = len(tokens)
    while i < n:
        if parsed["image"] is not None:
            parsed["args"].append(tokens[i])
            i += 1
            continue

        tok = tokens[i]

        if re.fullmatch(r"-[dit]+", tok):
            if "d" in tok:
                parsed["detach"] = True
            if "i" in tok or "t" in tok:
                parsed["interactive_tty"] = True
            i += 1
        elif tok == "--rm":
            parsed["rm"] = True
            i += 1
        elif tok == "--privileged":
            parsed["privileged"] = True
            i += 1
        elif tok == "--name" or tok.startswith("--name="):
            parsed["name"], i = take_value(i, "--name")
        elif tok == "--network" or tok.startswith("--network="):
            parsed["network"], i = take_value(i, "--network")
        elif tok == "--cap-add" or tok.startswith("--cap-add="):
            value, i = take_value(i, "--cap-add")
            parsed["cap_add"].append(value)
        elif tok == "--sysctl" or tok.startswith("--sysctl="):
            value, i = take_value(i, "--sysctl")
            key, _, val = value.partition("=")
            parsed["sysctls"][key] = val
        elif tok == "-p" or tok.startswith("-p="):
            value, i = take_value(i, "-p")
            host_port, _, container_port = value.partition(":")
            parsed["ports"].append((host_port, container_port or host_port))
        elif tok == "-v" or tok.startswith("-v="):
            value, i = take_value(i, "-v")
            parts = value.split(":")
            if len(parts) >= 3:
                parsed["volumes"].append((parts[0], parts[1], parts[2] == "ro"))
            elif len(parts) == 2:
                parsed["volumes"].append((parts[0], parts[1], False))
            else:
                raise DockerRunParseError(f"Invalid -v value in: {command}")
        elif tok == "-e" or tok.startswith("-e="):
            value, i = take_value(i, "-e")
            key, _, val = value.partition("=")
            parsed["env"][key] = val
        elif tok == "--dns" or tok.startswith("--dns="):
            parsed["dns"], i = take_value(i, "--dns")
        elif tok == "--memory" or tok.startswith("--memory="):
            parsed["memory"], i = take_value(i, "--memory")
        elif tok == "--cpus" or tok.startswith("--cpus="):
            parsed["cpus"], i = take_value(i, "--cpus")
        elif tok == "--entrypoint" or tok.startswith("--entrypoint="):
            parsed["entrypoint"], i = take_value(i, "--entrypoint")
        elif tok.startswith("-"):
            raise DockerRunParseError(f"Unknown docker run flag {tok!r} in: {command}")
        else:
            parsed["image"] = tok
            i += 1

    if parsed["image"] is None:
        raise DockerRunParseError(f"No image found in: {command}")

    return parsed


# Brief: Builds the Pod manifest (as a plain dict, ready for json.dumps) for
# the parsed docker-run fields.
def build_pod_manifest(name: str, parsed: dict, node_name: str) -> dict:
    is_switch = "openvswitch" in (parsed["image"] or "")

    cap_add = sorted(set(parsed["cap_add"]) | {"NET_ADMIN", "NET_RAW"})

    volume_mounts = []
    volumes = []
    for idx, (host_path, container_path, read_only) in enumerate(parsed["volumes"]):
        vol_name = f"vol{idx}"
        volumes.append({
            "name": vol_name,
            "hostPath": {"path": host_path, "type": "DirectoryOrCreate"},
        })
        volume_mounts.append({
            "name": vol_name,
            "mountPath": container_path,
            "readOnly": read_only,
        })

    if is_switch:
        volumes.append({"name": "ovsdb", "emptyDir": {}})
        volume_mounts.append({"name": "ovsdb", "mountPath": "/etc/openvswitch"})

    container = {
        "name": name,
        "image": parsed["image"],
        "imagePullPolicy": "IfNotPresent",
        "securityContext": {
            "privileged": parsed["privileged"],
            "capabilities": {"add": cap_add},
        },
    }

    if parsed["entrypoint"]:
        container["command"] = [parsed["entrypoint"]]
    if parsed["args"]:
        container["args"] = list(parsed["args"])
    if parsed["env"]:
        container["env"] = [{"name": k, "value": v} for k, v in parsed["env"].items()]
    if parsed["ports"]:
        container["ports"] = [
            {"containerPort": int(cp), "hostPort": int(hp)}
            for hp, cp in parsed["ports"]
        ]
    limits = {}
    if parsed["memory"]:
        limits["memory"] = parsed["memory"]
    if parsed["cpus"]:
        limits["cpu"] = parsed["cpus"]
    if limits:
        container["resources"] = {"limits": limits}
    if volume_mounts:
        container["volumeMounts"] = volume_mounts

    spec = {
        "restartPolicy": "OnFailure",
        "nodeName": node_name,
        "terminationGracePeriodSeconds": 0,
        "containers": [container],
    }
    if volumes:
        spec["volumes"] = volumes
    if parsed["sysctls"]:
        spec["securityContext"] = {
            "sysctls": [{"name": k, "value": v} for k, v in parsed["sysctls"].items()]
        }
    if parsed["dns"]:
        spec["dnsPolicy"] = "None"
        spec["dnsConfig"] = {"nameservers": [parsed["dns"]]}

    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {"name": name, "namespace": NAMESPACE},
        "spec": spec,
    }


# Brief: InfraBackend implementation that runs nodes as Pods on a single-node
# K3s cluster. Every network operation (ip link, tc, ovs-vsctl, ...) still
# runs unmodified elsewhere in the codebase, reached through exec_prefix /
# exec_argv; this backend only owns pod lifecycle and namespace plumbing.
class K3sBackend(InfraBackend):
    def exec_prefix(self, name: str, interactive: bool = False) -> str:
        if interactive:
            return f"kubectl exec -i -n {NAMESPACE} {name} --"
        return f"kubectl exec -n {NAMESPACE} {name} --"

    def exec_argv(self, name: str) -> list:
        return ["kubectl", "exec", "-n", NAMESPACE, name, "--"]

    # kubectl exec has no -d: a plain background `&` dies with the exec
    # session's SIGHUP, so the child is detached from the session with
    # setsid/nohup instead, matching docker's -d guarantee.
    def exec_detached(self, name: str, cmd: str):
        subprocess.run(
            f"kubectl exec -n {NAMESPACE} {name} -- sh -c 'setsid nohup {cmd} >/dev/null 2>&1 </dev/null &'",
            shell=True, check=True,
        )

    # crictl inspectp, not inspect: the pod sandbox (not the app container)
    # holds the network namespace.
    def node_pid(self, name: str) -> str:
        pod_id = subprocess.check_output(
            f"crictl pods --name '^{name}$' -q --state Ready", shell=True, text=True,
        ).strip()
        info = subprocess.check_output(f"crictl inspectp {pod_id}", shell=True, text=True)
        return str(json.loads(info)["info"]["pid"])

    def node_ip(self, name: str) -> str:
        cmd = f"kubectl get pod -n {NAMESPACE} {name} -o jsonpath='{{.status.podIP}}'"
        return subprocess.check_output(cmd, shell=True, text=True).strip()

    # Same boolean semantics as DockerBackend.node_exists: non-empty stdout
    # means the pod is there, an error goes to stderr and leaves stdout empty.
    def node_exists(self, name: str) -> bool:
        return subprocess.run(
            f"kubectl get pod -n {NAMESPACE} {name}", shell=True, capture_output=True,
        ).stdout.decode("utf8") != ""

    # Same mechanism as DockerBackend.enable_namespace (mkdir + ln -sfT);
    # only where the pid comes from changes.
    def enable_namespace(self, name: str):
        try:
            pid = self.node_pid(name)
            subprocess.run(
                f"mkdir -p /var/run/netns/; ln -sfT /proc/{pid}/ns/net /var/run/netns/{name}",
                shell=True,
            )
        except Exception as ex:
            logging.error(f"Error while enabling namespace for {name}: {str(ex)}")
            raise Exception(f"Error while enabling namespace for {name}: {str(ex)}")

    def cleanup(self):
        subprocess.run(f"kubectl delete namespace {NAMESPACE} --ignore-not-found --wait", shell=True)
        subprocess.run(f"kubectl create namespace {NAMESPACE}", shell=True)

    # --ignore-not-found already makes kubectl itself tolerate a missing pod;
    # no check=True on top, so a residual non-zero exit doesn't raise either.
    def delete_node(self, name: str):
        subprocess.run(f"kubectl delete pod -n {NAMESPACE} {name} --ignore-not-found", shell=True)

    def copy_to_node(self, name: str, src: str, dst: str):
        subprocess.run(f"kubectl cp {src} {NAMESPACE}/{name}:{dst}", shell=True, capture_output=True)

    def copy_from_node(self, name: str, src: str, dst: str):
        subprocess.run(f"kubectl cp {NAMESPACE}/{name}:{src} {dst}", shell=True, capture_output=True)

    def _single_node_name(self) -> str:
        return subprocess.check_output(
            "kubectl get nodes -o jsonpath='{.items[0].metadata.name}'", shell=True, text=True,
        ).strip()

    def _wait_running(self, name: str, timeout_s: int = WAIT_TIMEOUT_S):
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            phase = subprocess.run(
                f"kubectl get pod -n {NAMESPACE} {name} -o jsonpath='{{.status.phase}}'",
                shell=True, capture_output=True, text=True,
            ).stdout.strip()
            if phase == "Running":
                return
            time.sleep(1)

        describe = subprocess.run(
            f"kubectl describe pod -n {NAMESPACE} {name}", shell=True, capture_output=True, text=True,
        )
        raise NodeInstantiationFailed(
            f"Timed out waiting for pod {name} to become Running after {timeout_s}s.\n"
            f"kubectl describe pod -n {NAMESPACE} {name}:\n{describe.stdout}{describe.stderr}"
        )

    # Every pod is born with Flannel's eth0 default route, which collides with
    # LFT's own setDefaultGateway. Swap it for routes scoped to the cluster
    # pod/service CIDRs, discovering the current gateway at runtime.
    #
    # Runs via nsenter on the pod's own PID (the pod sandbox, from node_pid),
    # not `kubectl exec ... -- ip`: pod images aren't guaranteed to bundle
    # iproute2 (e.g. onosproject/onos has no `ip` binary at all), but the
    # host always does. This also sidesteps a pipe-masking trap: piping a
    # failing `ip` through `awk` makes the pipeline's exit status awk's (0),
    # not ip's, so a missing binary would otherwise go unnoticed here and
    # only surface later as an empty gateway.
    #
    # `route replace`, not `route add`, for the two additions: Flannel already
    # installs a route to its own pod CIDR (10.42.0.0/16) alongside the
    # default route, so `add` fails with "RTNETLINK answers: File exists" on
    # every pod. `replace` is idempotent whether or not that route pre-exists.
    def _fix_default_route(self, name: str):
        pid = self.node_pid(name)
        gw = subprocess.check_output(
            f"nsenter -t {pid} -n ip route show default | awk '{{print $3}}'",
            shell=True, text=True,
        ).strip()
        subprocess.run(
            f"nsenter -t {pid} -n sh -c "
            f"'ip route del default && "
            f"ip route replace 10.42.0.0/16 via {gw} dev eth0 && "
            f"ip route replace 10.43.0.0/16 via {gw} dev eth0'",
            shell=True, check=True,
        )

    # Preserves both instantiate() paths, like DockerBackend.create_node:
    # a ready-made `docker run ...` line (parsed here) or the internal
    # builder path (dockerCommand == ''), driven by image/dns/memory/cpus/
    # runCommand. Returns only once the pod is Running -- the same guarantee
    # `docker run -d` gives synchronously.
    def create_node(self, name: str, image="alexandremitsurukaihara/lst2.0:host", dockerCommand='', **opts):
        if dockerCommand:
            parsed = parse_docker_run(dockerCommand)
        else:
            run_command = opts.get('runCommand', '')
            parsed = {
                "detach": True, "interactive_tty": False, "rm": False,
                "name": name, "network": "none", "privileged": True,
                "cap_add": [], "sysctls": {}, "ports": [], "volumes": [], "env": {},
                "dns": opts.get('dns', '8.8.8.8'),
                "memory": opts.get('memory') or None,
                "cpus": opts.get('cpus') or None,
                "entrypoint": None,
                "image": image,
                "args": shlex.split(run_command) if run_command else [],
            }

        node_name = self._single_node_name()
        manifest = build_pod_manifest(name, parsed, node_name)

        try:
            subprocess.run(
                ["kubectl", "apply", "-f", "-"],
                input=json.dumps(manifest), text=True, check=True, capture_output=True,
            )
        except Exception as ex:
            logging.error(f"Error while creating the pod {name}: {str(ex)}")
            raise NodeInstantiationFailed(f"Error while creating the pod {name}: {str(ex)}")

        self._wait_running(name)
        self._fix_default_route(name)
