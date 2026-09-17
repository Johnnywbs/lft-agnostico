# Lightweight Fog Testbed (LFT) – iPerf Experiment Branch

## Description

This branch extends the **Lightweight Fog Testbed (LFT)** into a research environment for **Intent-Based Networking (IBN)**. It features a **Deployer** that receives, processes and applies **Nile intents** within a virtualized topology built with **ONOS** and **Open vSwitch (OVS)**.

The platform supports a  **iperf3-based experimental track** to compare five distinct routing paradigms under network stress (**degradation** or **link failure**):

- **CDN-QoE**: Algorithm for optimal path and server selection, considering real-time RTT and throughput.
- **LLM**: Routing using **Llama 3.1** (via **Ollama**) for decision-making.
- **Threshold**: (not defined yet).
- **Reactive Forwarding (fwd)**: Standard SDN shortest-path routing based on hop count.
- **OSPF (Legacy)**: Traditional link-state protocol running on **ONOS v1.6**.

---

## 1. Requirements

You need:

- (Recommended) Ubuntu Desktop 24.04 LTS
- Docker (with permission to run `sudo docker …`)
- Python 3 and `pip3`
- Git
- `tmux` for persistent runs over SSH
- (Optional) K3s, single-node — only if you want to run with `LFT_BACKEND=k3s`.
  **Install it with these components disabled:**
  ```
  curl -sfL https://get.k3s.io | INSTALL_K3S_EXEC="--disable traefik --disable servicelb --disable metrics-server" sh -
  ```
  LFT doesn't use any of the three (no Ingress, no `LoadBalancer` Services, no
  metrics). `metrics-server` in particular tends to end up unable to reach
  the node's kubelet on some machines, which leaves it permanently `0/1`
  Ready - and a broken `metrics-server` makes `kubectl delete namespace
  --wait` hang forever (its "GroupVersion discovery" check never succeeds),
  which is exactly what `sudo lft utils clean` and every experiment's
  cleanup step run. Confirmed on a clean install: with these three disabled,
  only `coredns` and `local-path-provisioner` come up, and namespace
  deletion completes normally. If K3s is already installed without these
  flags, see Troubleshooting item 6.

---

## 2. Installation & Image Build

On recent Ubuntu, the system Python blocks a plain `pip install` (PEP 668).
Instead of `--break-system-packages`, install into a venv - no `sudo`
needed for either step, since it's a normal venv, not a system-wide install:

```
git clone https://github.com/alexandrekaihara/lft
cd lft
chmod +x dependencies.sh
sudo ./dependencies.sh
python3 -m venv .venv
.venv/bin/pip install -e .
```

`lft` needs to run as root regardless (it manipulates network namespaces
directly), so run it straight from the venv (`sudo .venv/bin/lft ...`) -
see section 3 for actual commands, after building the images below.

### 2.1 Build Docker Images

**Run this before creating any topology.** `quagga`, `lft-iperf` and
`neubot/dash-client` don't exist on any public registry, so if you skip this
step LFT won't raise a clear error — `docker pull`/`docker run` fail
silently and the node is just never created, which usually only surfaces
later as a confusing error somewhere unrelated (e.g. `.connect()` on a node
that doesn't exist). This is original LFT behavior, not specific to any one
backend.

```
# ONOS 2.5 (Compatible with link-latency app)
sudo docker pull onosproject/onos:2.5.0

# ONOS 1.6 (Compatible with OSPF)
sudo docker pull onosproject/onos:1.6

# OpenSwitch
cd docker/openswitch && sudo docker build -t alexandremitsurukaihara/lst2.0:openvswitch . && cd ../..

# Quagga Router
cd docker/quagga && sudo docker build -t quagga . && cd ../..

# Iperf client/server
cd docker/iperf && sudo docker build -t lft-iperf . && cd ../..

# DASH client (não existe imagem publicada em nenhum registry; o upstream
# só builda o dash-server. O código do cliente está no mesmo repositório,
# em cmd/dash-client)
cd docker/dash_client && sudo docker build -t neubot/dash-client:latest . && cd ../..

```

### 2.2 Importar imagens para o K3s (opcional)

Necessário apenas para usar `LFT_BACKEND=k3s`. O containerd do K3s tem
armazenamento separado do Docker, então as imagens construídas localmente
(`quagga`, `lft-iperf` e `neubot/dash-client`) precisam ser copiadas:

```
./import_k3s_images.sh
```

As demais imagens (neubot/dash, onosproject/onos, etc.) são baixadas
automaticamente do registry, como no Docker — o script também aproveita e
copia as imagens do ONOS do Docker local se elas já estiverem lá, para evitar
que o primeiro pod demore mais que o timeout esperando um download a frio de
uma imagem grande.

**Reinstalou o K3s (ex.: `k3s-uninstall.sh` seguido de uma instalação
nova)?** Rode este script de novo. O uninstall apaga `/var/lib/rancher/k3s`
inteiro, cache de imagens do containerd incluso - sem isso, o primeiro pod
(ex. o ONOS, 500MB+) faz um pull a frio do zero. Ainda funciona (o timeout do
LFT pra isso é de 300s), só fica mais lento sem necessidade.

### 2.3 Choosing a backend

LFT can run on Docker or on a single-node K3s cluster. Select it with the
`LFT_BACKEND` environment variable (defaults to `docker`):

```
export LFT_BACKEND=docker   # or: export LFT_BACKEND=k3s
```

## 3. CLI

After installing, use `sudo lft` to manage topologies interactively.
(`lft` here is `.venv/bin/lft` from step 2 - replace it with that path, or
your own venv's path, wherever `sudo lft` appears below.)

**Load a topology from config and open the REPL:**
```
sudo lft topology create --path onos_topologies/iperf_experiment/diamond_topology/constants.py
```

**Start an empty topology manually:**
```
sudo lft topology create --manual
```

**REPL commands:**
```
create host <name> <ip>       add a host
create switch <name>          add a switch
connect <name1> <name2>       link two nodes
traffic <ping|iperf> <n1> <n2>
ls [hosts|switches]
quit
```

**Other commands:**
```
sudo lft experiment                        list available experiments
sudo lft experiment <name>                 run an experiment
sudo lft utils clean                       remove all LFT nodes (Docker containers or K3s pods, per LFT_BACKEND)
```

---

## 4. First Run (legacy)

Experiments can also be run directly, without the CLI:

```
cd onos_topologies/iperf_experiment/diamond_topology
python3 main.py
```

### 4.1 Customize topology

To customize the topology, edit the data structures in
`diamond_topology/constants.py` (or `rnp_topology/constants.py` for the RNP
experiment).

## 5. Results

The experiment runs in a loop of **6 snapshots** (default: **10 minutes each**).

### Snapshot Schedule

- **Snapshots 1, 3, 4, and 6**: baseline / normal network state
- **Snapshots 2 and 5**: the selected **hindering** is applied to a link


### Output Directory

Results are stored in:

```text
onos_topologies/results/iperf/run_YYYY-MM-DD_HH-MM-SS/
```
### Generated Files

At the end of the run, the script automatically generates unified CSV files:

- `iperf_flow_all.csv`: throughput, jitter, and packet loss data
- `ping_flow_all.csv`: high-resolution RTT logs with timestamps

- `ovs_ports_all.csv`: per-port traffic, drops and errors
- `ovs_flows_all.csv`: flow rules, actions and counters

## 6. Troubleshooting
If you face any issue while running any LFT scrips:
1. Check if all dependencies are installed
2. Check if you are using the correct version of Ubuntu Desktop
3. Check for leftover nodes from a previous run:
   - Docker: ```docker ps -a```. Remove them with ```docker system prune``` or forcefully with ```docker rm -f containerName```, or just run ```sudo lft utils clean```.
   - K3s: ```sudo k3s kubectl get pods -A```. Remove them with ```sudo lft utils clean``` (deletes and recreates the `lft` namespace).
4. Verify if the docker image that you are trying to instantiate with LFT exists on your local machine ```docker images``` or exists on [Docker Hub|https://hub.docker.com/]. For K3s, check ```sudo k3s ctr images list``` instead - see 2.2 to import local images into it.
5. Check if the image was built correctly. See docker folder for more information.
6. **`sudo lft` hangs forever with no output right after `namespace "lft"
   deleted`** (K3s backend, on `cleanup()` at the start or end of any run):
   the namespace deletion controller does a live API discovery pass before
   it will finish, and that check can fail forever if `metrics-server` is
   unhealthy (`kubectl describe namespace lft` shows a
   `NamespaceDeletionDiscoveryFailure` condition mentioning
   `metrics.k8s.io`) - unrelated to whether LFT itself left anything behind.
   Fix at the source: reinstall K3s with `metrics-server` disabled (see
   section 1). If you don't want to reinstall right now, unstick the
   current hang manually:
   ```
   kubectl get namespace lft -o json | jq '.spec.finalizers = []' | \
     kubectl replace --raw /api/v1/namespaces/lft/finalize -f -
   ```
   This only clears the immediate hang - it comes back on the next
   `cleanup()` as long as `metrics-server` stays broken.

7. **ONOS shows no devices / switches stuck retrying the controller with "No
   route to host"** (check `docker logs <switch>` or
   `docker exec <switch> tail /var/log/openvswitch/ovs-vswitchd.log`) on a
   machine that has both K3s and Docker installed: K3s loads the
   `br_netfilter` kernel module, which makes ALL bridged traffic on the host
   pass through iptables - including Docker's own `docker0` bridge,
   unrelated to K3s. Docker's per-container anti-spoofing rules then see the
   packet arriving on the container's veth instead of `docker0` and drop it,
   breaking container-to-container connectivity. This only shows up because
   the K3s *service* is running in the background (even with zero pods of
   its own) - it doesn't require an actual K3s-backed run to happen at the
   same time. The `LFT_BACKEND=docker` path already works around it: every
   run resets `net.bridge.bridge-nf-call-iptables` (and the ip6tables/
   arptables equivalents) to `0` before creating any node - confirmed with a
   full run (ping, iperf and ONOS's own flow tables) after this fix. This is
   safe specifically because Docker-backed and K3s-backed runs are never
   used at the same time: K3s puts those sysctls back to `1` itself on its
   own next start (that's what its `kube-proxy` needs), so nothing needs to
   restore them for K3s - just don't run `LFT_BACKEND=k3s` concurrently with
   a Docker-backed run on the same machine.
