import logging
import subprocess
import json

from .base import InfraBackend
from ..exceptions import NodeInstantiationFailed
from ..constants import DOCKER_RUN, NETWORK, NAME, PRIVILEGED, DNS, MEMORY, CPUS


# Brief: InfraBackend implementation that runs nodes as Docker containers.
# Every method here executes the exact same command that profissa_lft/node.py
# (and onos_topologies/dash_topology/utils.py, for node_ip/cleanup) ran before
# this backend layer existed.
class DockerBackend(InfraBackend):
    def exec_prefix(self, name: str, interactive: bool = False) -> str:
        if interactive:
            return f"docker exec -i {name}"
        return f"docker exec {name}"

    def exec_argv(self, name: str) -> list:
        return ["docker", "exec", name]

    # Origin: onos_topologies/iperf_experiment/iperf_server.py:26, `docker exec -d`.
    # kubectl exec has no -d, so this stays its own backend method rather than a prefix swap.
    def exec_detached(self, name: str, cmd: str):
        subprocess.run(f"docker exec -d {name} {cmd}", shell=True, check=True)

    # Origin: profissa_lft/node.py, the `docker inspect -f '{{.State.Pid}}'`
    # part of __enableNamespace, run standalone here.
    def node_pid(self, name: str) -> str:
        return subprocess.check_output(f"docker inspect -f '{{{{.State.Pid}}}}' {name}", shell=True, text=True).strip()

    # Origin: profissa_lft/node.py, Node.__isActive. Preserves the pre-existing
    # bug: a stray single quote before the closing double quote.
    def node_exists(self, name: str) -> bool:
        return subprocess.run(f"docker ps | grep {name}'", shell=True, capture_output=True).stdout.decode('utf8') != ''

    # Origin: onos_topologies/dash_topology/utils.py:37 (get_container_ip)
    def node_ip(self, name: str) -> str:
        cmd = f"docker inspect -f '{{{{range .NetworkSettings.Networks}}}}{{{{.IPAddress}}}}{{{{end}}}}' {name}"
        return subprocess.check_output(cmd, shell=True, text=True).strip()

    # Origin: onos_topologies/dash_topology/utils.py:46 and :48
    def cleanup(self):
        subprocess.run('sudo docker rm -f $(sudo docker ps -aq) >/dev/null 2>&1 || true', shell=True)
        subprocess.run('sudo docker network prune -f >/dev/null 2>&1', shell=True)

    # Origin: profissa_lft/node.py, Node.instantiate(). Preserves both paths:
    # the internal command builder and the dockerCommand override.
    def create_node(self, name: str, image="alexandremitsurukaihara/lst2.0:host", dockerCommand='', **opts):
        dns = opts.get('dns', '8.8.8.8')
        memory = opts.get('memory', '')
        cpus = opts.get('cpus', '')
        runCommand = opts.get('runCommand', '')

        command = []

        def addDockerRun():
            command.append(DOCKER_RUN)

        def addRunOptions():
            command.append("-d")

        def addNetwork():
            command.append(NETWORK + "=none")

        def addContainerName():
            command.append(NAME + '=' + name)

        def addPrivileged():
            command.append(PRIVILEGED)

        def addDNS(dns):
            command.append(DNS + '=' + dns)

        def addContainerMemory(memory):
            if memory != '':
                command.append(MEMORY + '=' + memory)

        def addContainerCPUs(cpus):
            if cpus != '':
                command.append(CPUS + '=' + cpus)

        def addContainerImage(image):
            command.append(image)

        def addRunCommand(runCommand):
            command.append(runCommand)

        def buildCommand():
            return " ".join(command)

        if not self.__imageExists(image):
            logging.info(f"Image {image} not found, pulling from remote repository...")
            self.__pullImage(image)

        if dockerCommand == '':
            addDockerRun()
            addRunOptions()
            addNetwork()
            addContainerName()
            addPrivileged()
            addDNS(dns)
            addContainerMemory(memory)
            addContainerCPUs(cpus)
            addContainerImage(image)
            addRunCommand(runCommand)

        try:
            if dockerCommand != '':
                subprocess.run(dockerCommand, shell=True, capture_output=True)
            else:
                subprocess.run(buildCommand(), shell=True, capture_output=True)
        except Exception as ex:
            logging.error(f"Error while criating the container {name}: {str(ex)}")
            raise NodeInstantiationFailed(f"Error while criating the container {name}: {str(ex)}")

    # Origin: profissa_lft/node.py, Node.__imageExists
    def __imageExists(self, image: str) -> bool:
        out = subprocess.run(f"docker inspect --type=image {image}", shell=True, capture_output=True)
        outJson = json.loads(out.stdout.decode('utf-8'))
        if outJson == []: return False
        else: return True

    # Origin: profissa_lft/node.py, Node.__pullImage
    def __pullImage(self, image):
        try:
            subprocess.run(f"docker pull {image}", shell=True)
        except Exception as ex:
            logging.error(f"Error pulling non-existing {image} image: {str(ex)}")
            raise NodeInstantiationFailed(f"Error pulling non-existing {image} image: {str(ex)}")

    # Origin: profissa_lft/node.py, Node.delete
    def delete_node(self, name: str):
        try:
            subprocess.run(f"docker kill {name} && docker rm {name}", shell=True, capture_output=True)
        except Exception as ex:
            logging.error(f"Error while deleting the host {name}: {str(ex)}")
            raise NodeInstantiationFailed(f"Error while deleting the host {name}: {str(ex)}")

    # Origin: profissa_lft/node.py, Node.__enableNamespace
    def enable_namespace(self, name: str):
        try:
            subprocess.run(f"pid=$(docker inspect -f '{{{{.State.Pid}}}}' {name}); mkdir -p /var/run/netns/; ln -sfT /proc/$pid/ns/net /var/run/netns/{name}", shell=True)
        except Exception as ex:
            logging.error(f"Error while deleting the host {name}: {str(ex)}")
            raise Exception(f"Error while deleting the host {name}: {str(ex)}")

    # Origin: profissa_lft/node.py, Node.copyLocalToContainer
    def copy_to_node(self, name: str, src: str, dst: str):
        try:
            subprocess.run(f"docker cp {src} {name}:{dst}", shell=True, capture_output=True)
        except Exception as ex:
            logging.error(f"Error copying file from {src} to {dst}: {str(ex)}")
            raise Exception(f"Error copying file from {src} to {dst}: {str(ex)}")

    # Origin: profissa_lft/node.py, Node.copyContainerToLocal
    def copy_from_node(self, name: str, src: str, dst: str):
        try:
            subprocess.run(f"docker cp {name}:{src} {dst}", shell=True, capture_output=True)
        except Exception as ex:
            logging.error(f"Error copying file from {src} to {dst}: {str(ex)}")
            raise Exception(f"Error copying file from {src} to {dst}: {str(ex)}")
