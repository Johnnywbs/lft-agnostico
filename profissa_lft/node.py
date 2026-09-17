# Copyright (C) 2022 Alexandre Mitsuru Kaihara
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.


import logging
import subprocess
import hashlib
from configparser import ConfigParser
import json
from .exceptions import *
from .constants import *
from . import env


# Just to enable the declaration of Type in methods
class Node:
    pass


# Brief: This is a super class that define methods common for network nodes
class Node:
    # Brief: Constructor of Node super class
    # Params:
    #   String containerName: Name of the container
    # Return:
    #   None
    def __init__(self, nodeName: str) -> None:
        self.__createTmpFolder()
        self.__nodeName = nodeName
        self.memory = ''
        self.cpu = ''
        self.__backend = env.get_backend()

    def __createTmpFolder(self) -> None:
        subprocess.run("mkdir -p /tmp/lft/", shell=True)

    # Brief: Returns the infra backend (Docker, K3s, ...) this node was created with
    # Params:
    # Return:
    #   InfraBackend instance
    def getBackend(self):
        return self.__backend

    # OBS: Create nodes with short name lenght due to a restriction on a iproute2 to define and create interfaces.
    # Brief: Instantiate the container
    # Params:
    #   String dockerImage: Name of the container of a local image or in a Docker Hub repository
    #   String DockerCommand: String to be used to instantiate the container instead of the standard command
    #   String memory: It is the amount of memory to be allocated to the container (e.g. "512m", which is 512 MB)
    #   String cpus: It is the amount of cpu dedicated to the container, can be a fractional value such as "0.5"
    # Return:
    #   None
    def instantiate(self, dockerImage="alexandremitsurukaihara/lst2.0:host", dockerCommand='', dns='8.8.8.8', memory='', cpus='', runCommand='') -> None:
        self.__backend.create_node(
            self.getNodeName(),
            image=dockerImage,
            dockerCommand=dockerCommand,
            dns=dns,
            memory=memory,
            cpus=cpus,
            runCommand=runCommand,
        )
        self.__backend.enable_namespace(self.getNodeName())
        env.mark_node_instantiated()

    # Brief: Instantiate the container
    # Params:
    #   String dockerImage: Name of the container of a local image or in a Docker Hub repository
    #   String DockerCommand: String to be used to instantiate the container instead of the standard command
    # Return:
    #   None
    def delete(self) -> None:
        self.__backend.delete_node(self.getNodeName())

    # Brief: Set Ip to an interface (the ip must be set only after connecting it to a container)
    # Params:
    #   String ip: IP address to be set to peerName interface
    #   int mask: Integer that represents the network mask
    #   String node: Reference to the node it is connected to this container to discover the intereface to set the ip to
    # Return:
    #   None
    def setIp(self, ip: str, mask: int, interfaceName: str) -> None:
        if not self.__interfaceExists(interfaceName):
            logging.error(f"Network interface {interfaceName} does not exist")
            raise Exception(f"Network interface {interfaceName} does not exist")

        self.__setIp(ip, mask, interfaceName)

    # Brief: Creates Linux virtual interfaces and connects peers to the nodes, in case of one of the nodes is a switch, it also creates a port in bridge
    # Params:
    #   Node node: Reference of another node to connect to
    # Return:
    #   None
    def connect(self, node: Node, interfaceName: str, peerInterfaceName: str) -> None:
        if self.__interfaceExists(interfaceName) or self.__interfaceExists(peerInterfaceName):
            logging.error(f"Cannot connect to {node.getNodeName()}, {interfaceName} or {peerInterfaceName} already exists")
            raise Exception(f"Cannot connect to {node.getNodeName()}, {interfaceName} or {peerInterfaceName} already exists")

        self.__create(interfaceName, peerInterfaceName)
        self.__setInterface(self.getNodeName(), interfaceName)
        self.__setInterface(node.getNodeName(), peerInterfaceName)

        if hasattr(self, '_Switch__createPort'):
            self._Switch__createPort(self.getNodeName(), interfaceName)
        if hasattr(node, '_Switch__createPort'):
            node._Switch__createPort(node.getNodeName(), peerInterfaceName)
    
    def connectToInternet(self, hostIP: str, hostMask: int, interfaceName: str, hostInterfaceName: str) -> None:
        self.__create(interfaceName, hostInterfaceName)
        self.__setInterface(self.getNodeName(), interfaceName)
        if hasattr(self, '_Switch__createPort'):
            self._Switch__createPort(self.getNodeName(), interfaceName)
        
        subprocess.run(f"ip link set {hostInterfaceName} up", shell=True)
        subprocess.run(f"ip addr add {hostIP}/{hostMask} dev {hostInterfaceName}", shell=True)

        # Enable forwading packets from host to interface
        hostGatewayInterfaceName = subprocess.run(f"route | grep \'^default\' | grep -o \'[^ ]*$\'", shell=True, capture_output=True).stdout.decode('utf8').replace("\n", '')
        subprocess.run(f"iptables -t nat -I POSTROUTING -o {hostGatewayInterfaceName} -j MASQUERADE", shell=True)
        subprocess.run(f"iptables -t nat -I POSTROUTING -o {hostInterfaceName} -j MASQUERADE", shell=True)
        subprocess.run(f"iptables -A FORWARD -i {hostInterfaceName} -o {hostGatewayInterfaceName} -j ACCEPT", shell=True)
        subprocess.run(f"iptables -A FORWARD -i {hostGatewayInterfaceName} -o {hostInterfaceName} -j ACCEPT", shell=True)
        subprocess.run(f"firewall-cmd --zone=trusted --add-interface={hostInterfaceName}", shell=True)
            
    def connectToInternetWithoutNAT(self, hostIP: str, hostMask: int, interfaceName: str, hostInterfaceName: str) -> None:
        self.__create(interfaceName, hostInterfaceName)
        self.__setInterface(self.getNodeName(), interfaceName)
        if self.__class__.__name__ == 'Switch':
            self._Switch__createPort(self.getNodeName(), interfaceName)
        
        subprocess.run(f"ip link set {hostInterfaceName} up", shell=True)
        subprocess.run(f"ip addr add {hostIP}/{hostMask} dev {hostInterfaceName}", shell=True)
        subprocess.run(f"firewall-cmd --zone=trusted --add-interface={hostInterfaceName}", shell=True)

    def enableForwarding(self, interfaceName: str, otherInterfaceName: str) -> None:
        self.run(f"iptables -t nat -I POSTROUTING -o {otherInterfaceName} -j MASQUERADE")
        self.run(f"iptables -t nat -I POSTROUTING -o {interfaceName} -j MASQUERADE")
        self.run(f"iptables -A FORWARD -i {interfaceName} -o {otherInterfaceName} -j ACCEPT")
        self.run(f"iptables -A FORWARD -i {otherInterfaceName} -o {interfaceName} -j ACCEPT")

    # Brief: Returns the value of the container name
    # Params:
    # Return:
    #   Returns the name of the node
    def getNodeName(self) -> str:
        return self.__nodeName

    # Brief: Add a route in routing table of container
    # Params:
    #   String ip: IP address of the route
    #   String mask: Network mask for the IP address route
    #   String interfaceName: Name of the interface to forward to
    # Return:
    #   None
    def addRoute(self, ip: str, mask: int,  interfaceName: str):
        if not self.__interfaceExists(interfaceName):
            logging.error(f"Network interface {interfaceName} does not exist")
            raise Exception(f"Network interface {interfaceName} does not exist")
        try:
            subprocess.run(f"{self.__backend.exec_prefix(self.getNodeName())} ip route add {ip}/{mask} dev {interfaceName}", shell=True)
        except Exception as ex:
            logging.error(f"Error adding route {ip}/{mask} via {interfaceName} in {self.getNodeName()}: {str(ex)}")
            raise Exception(f"Error adding route {ip}/{mask} via {interfaceName} in {self.getNodeName()}: {str(ex)}")

    # Brief: Add a route in routing table of host
    # Params:
    #   String ip: IP address of the route
    #   String mask: Network mask for the IP address route
    #   String interfaceName: Name of the interface to forward to
    # Return:
    #   None
    def addRouteOnHost(self, ip: str, mask: int, interfaceName: str, gateway="0.0.0.0") -> None:
        try:
            subprocess.run(f"ip route add {ip}/{mask} via {gateway} dev {interfaceName}", shell=True)
        except Exception as ex:
            logging.error(f"Error adding route {ip}/{mask} via {interfaceName} in {self.getNodeName()}: {str(ex)}")
            raise Exception(f"Error adding route {ip}/{mask} via {interfaceName} in {self.getNodeName()}: {str(ex)}")

    # Brief: Set the default route in the node, manipulating its network
    # namespace directly from the host (like __setIp) instead of running "ip"
    # inside the container. Some images (e.g. neubot/dash's minimal server
    # image) ship no shell/ip binary at all, so a docker-exec-based "ip route"
    # would fail with "command not found" regardless of capabilities.
    # Params:
    #   String gatewayIp: Gateway to route through
    #   String interfaceName: This node's interface facing the gateway
    # Return:
    #   None
    def setDefaultRoute(self, gatewayIp: str, interfaceName: str) -> None:
        try:
            subprocess.run(
                f"ip -n {self.getNodeName()} route add default via {gatewayIp} dev {interfaceName} onlink",
                shell=True, check=True, capture_output=True, text=True,
            )
        except subprocess.CalledProcessError as ex:
            logging.error(f"Error setting default route via {gatewayIp} on {self.getNodeName()}: {ex.stderr.strip()}")
            raise Exception(f"Error setting default route via {gatewayIp} on {self.getNodeName()}: {ex.stderr.strip()}")

    # Brief: Send a best-effort ping from this node's network namespace using
    # the host's own ping binary (via "ip netns exec"), instead of running
    # ping inside the container - some images (e.g. neubot/dash's minimal
    # server image) have no shell or ping binary at all. A ping going
    # unanswered (e.g. priming ARP for a non-attributed IP) is expected and
    # is not an error; this only raises if the underlying mechanism itself
    # fails (e.g. the namespace doesn't exist).
    # Params:
    #   String targetIp: IP address to ping
    #   int timeoutSeconds: Per-request timeout
    # Return:
    #   bool: True if a reply was received, False otherwise
    def pingFromHost(self, targetIp: str, timeoutSeconds: int = 2) -> bool:
        result = subprocess.run(
            f"ip netns exec {self.getNodeName()} ping -c 1 -W {timeoutSeconds} {targetIp}",
            shell=True, capture_output=True, text=True,
        )
        if result.returncode not in (0, 1):
            # 0 = reply received, 1 = no reply (both fine here) - anything
            # else means the mechanism itself broke (bad namespace, etc.)
            logging.error(f"Error pinging {targetIp} from {self.getNodeName()}: {result.stderr.strip()}")
            raise Exception(f"Error pinging {targetIp} from {self.getNodeName()}: {result.stderr.strip()}")
        return result.returncode == 0

    # Brief: Set Ip to an interface (the ip must be set only after connecting it to a container, because)
    # Params:
    #   String destinationIp: The destination IP address of the gateway in format "XXX.XXX.XXX.XXX"
    #   String node: Reference to node that will serve as the first hop to forward the packets to the gateway, this reference node must be already connected to it
    # Return:
    #   None
    def setDefaultGateway(self, destinationIp: str, interfaceName: str) -> None:
        if not self.__interfaceExists(interfaceName):
            logging.error(f"Network interface {interfaceName} does not exist")
            raise Exception(f"Network interface {interfaceName} does not exist")
        
        self.addRoute(destinationIp, 32, interfaceName)
        try:
            subprocess.run(f"{self.__backend.exec_prefix(self.getNodeName())} route add default gw {destinationIp} dev {interfaceName}", shell=True)
        except Exception as ex:
            logging.error(f"Error while setting gateway {destinationIp} on device {interfaceName} in {self.getNodeName()}: {str(ex)}")
            raise Exception(f"Error while setting gateway {destinationIp} on device {interfaceName} in {self.getNodeName()}: {str(ex)}")

    # Brief: Runs a command inside the container
    # Params:
    #   String command: String containing the command to run inside the container
    #   bool background: If True, don't wait for the command to finish (for a
    #     process meant to keep running until stopped separately, e.g. srsenb).
    #     Errors from a backgrounded command are NOT detected - by design,
    #     there's nothing to wait for.
    # Return:
    #   subprocess.CompletedProcess (background=False) with .stdout/.stderr as
    #   strings, or subprocess.Popen (background=True) if you need to track
    #   the process itself.
    def run(self, command: str, background: bool = False):
        fullCommand = command.replace('\"', 'DOUBLEQUOTESDELIMITER')
        fullCommand = f"{self.__backend.exec_prefix(self.getNodeName())} bash -c \"" + fullCommand + f"\""
        fullCommand = fullCommand.replace('DOUBLEQUOTESDELIMITER','\\"')

        if background:
            try:
                return subprocess.Popen(fullCommand, shell=True, stdout=subprocess.PIPE, text=True)
            except Exception as ex:
                logging.error(f"Error executing command {command} in {self.getNodeName()}: {str(ex)}")
                raise Exception(f"Error executing command {command} in {self.getNodeName()}: {str(ex)}")

        result = subprocess.run(fullCommand, shell=True, capture_output=True, text=True)
        if result.returncode != 0:
            msg = f"Command failed in {self.getNodeName()} (exit {result.returncode}): {command}\n{result.stderr.strip()}"
            logging.error(msg)
            raise Exception(msg)
        return result

    # Brief: Runs multiple commands inside the container
    # Params:
    #   List<String> commands: Runs multiple comands inside the container
    # Return:
    #   Returns a list with the variable that contains stdout and stderr (more information in subprocess documentation)
    def runs(self, commands: list) -> list:
        return [self.run(command) for command in commands]     

    # Brief: Copy local file into container
    # Params:
    #   String path: Absolute or relative path to the file to be copied from local (path+filename)
    #   String destPath: Absolute path to copy the file to the container (path+filename)
    # Return:
    def copyLocalToContainer(self, path: str, destPath: str) -> None:
        self.__backend.copy_to_node(self.getNodeName(), path, destPath)

    # Brief: Copy local file into container
    # Params:
    #   String path: Absolute path to the file to be copied from container (path+filename)
    #   String destPath: Absolute or relative path to copy to local (path+filename)
    # Return:
    def copyContainerToLocal(self, path: str, destPath: str) -> None:
        self.__backend.copy_from_node(self.getNodeName(), path, destPath)

    def __interfaceExists(self, interfaceName: str) -> bool:
        out = subprocess.run(f"{self.__backend.exec_prefix(self.getNodeName())} ip link | grep {interfaceName}", shell=True, capture_output=True)
        if out.stdout.decode("utf8") != '':
            return True
        return False

    # Brief: Returns the name of the interface to be created on this node
    # Params:
    #   Node node: Reference of another node to connect to
    # Return:
    #   Name of the interface with pattern veth + this node name + other node name
    def __getThisInterfaceName(self, node: Node) -> str:
        return self.getNodeName()+node.getNodeName()

    # Brief: Set Ip to an interface
    # Params:
    #   String ip: IP address to be set to peerName interface
    #   int mask: Integer that represents the network mask
    #   String interfaceName: Name of the interface to set the ip
    # Return:
    #   None
    def __setIp(self, ip: str, mask: int, interfaceName: str) -> None:
        try:
            subprocess.run(f"ip -n {self.getNodeName()} addr add {ip}/{mask} dev {interfaceName}", shell=True)
        except Exception as ex:
            logging.error(f"Error while setting IP {ip}/{mask} to virtual interface {interfaceName}: {str(ex)}")
            raise Exception(f"Error while setting IP {ip}/{mask} to virtual interface {interfaceName}: {str(ex)}")

    # Brief: Returns the name of the interface to be created on other node
    # Params:
    #   Node node: Reference of another node to connect to
    # Return:
    #   Name of the interface with pattern veth + other node name + this node name
    def __getOtherInterfaceName(self, node: Node) -> str:
        return node.getNodeName()+self.getNodeName()

    # Brief: Creates the virtual interfaces and set them up (names cant be the same as some existing one in host's namespace)
    # Params:
    #   String peer1Name: Name of the interface to connect to the first peer 
    #   String peer2Name: Name of the interface to connect to the second peer 
    # Return:
    #   None
    def __create(self, peer1Name: str, peer2Name: str) -> None:
        try:
            subprocess.run(f"ip link add {peer1Name} type veth peer name {peer2Name}", shell=True)
        except Exception as ex:
            logging.error(f"Error while creating virtual interfaces {peer1Name} and {peer2Name}: {str(ex)}")
            raise Exception(f"Error while creating virtual interfaces {peer1Name} and {peer2Name}: {str(ex)}")

    # Brief: Set the interface to node
    # Params:
    #   String nodeName: Name of the node network namespace
    #   String peerName: Name of the interface to set to node
    # Return:
    #   None
    def __setInterface(self, nodeName: str, peerName: str) -> None:
        try:
            subprocess.run(f"ip link set {peerName} netns {nodeName}", shell=True)
            subprocess.run(f"ip -n {nodeName} link set {peerName} up", shell=True)
        except Exception as ex:
            logging.error(f"Error while setting virtual interfaces {peerName} to {nodeName}: {str(ex)}")
            raise Exception(f"Error while setting virtual interfaces {peerName} to {nodeName}: {str(ex)}")

    # Brief: Get all interfaces names
    # Params:
    # Return:
    #   Return a list with the name of all interfaces
    def __getAllInterfaces(self) -> list:
        output = subprocess.run(f"{self.__backend.exec_prefix(self.getNodeName())} ifconfig -a | sed 's/[ \t].*//;/^$/d'", shell=True, capture_output=True)
        interfaces=output.stdout.decode('utf8').replace(":", '').split('\n')
        return list(filter(None, interfaces)) # Remove empty strings

    # Brief: Verifies if the container is active
    # Params:
    # Return:
    #   Return true if it is active or false otherwise
    def __isActive(self) -> bool:
        return self.__backend.node_exists(self.getNodeName())

    def setMtuSize(self, interfaceName: str, mtu: int) -> None:
        self.run(f"ifconfig {interfaceName} mtu {str(mtu)}")

    # Brief: It retrieves the config file inside the container and reads it, and then returns a ConfigParser instance
    # Params:
    #   String containerPath: Path inside the container of the config file including filename
    # Return:
    #   Returns a ConfigParser instance with the config file read
    def readConfigFile(self, containerPath: str) -> None:
        randomTmpName = self.getHashFromString(containerPath) 
        self.copyContainerToLocal(containerPath, f"/tmp/lft/{randomTmpName}")
        
        config = ConfigParser()
        config.read(f"/tmp/lft/{randomTmpName}")
        return config

    def saveConfig(self, config: ConfigParser, containerPath: str) -> None:
        randomTmpName = self.getHashFromString(containerPath)
        with open(f"/tmp/lft/{randomTmpName}", "w") as f:
            config.write(f)
        self.copyLocalToContainer(f"/tmp/lft/{randomTmpName}", containerPath)
        
    def getHashFromString(self, anyStr: str) -> str:
        h = hashlib.md5()
        h.update(anyStr.encode('utf-8'))
        return h.hexdigest()
    
    def setHost(self, ip: str) -> None:
        result = self.run(f"hostname")
        hostname = result.stdout.strip()
        self.run(f"HOSTNAME=$(hostname) && echo \"{ip} {hostname}\" >> /etc/hosts")

    def acceptPacketsFromInterface(self, interfaceName: str):
        self.run(f"iptables -A INPUT -i {interfaceName} -j ACCEPT")

    # Brief: Shapes the Linux network interface throughput, latency and burst using Traffic Control (TC)
    # Params:
    #   String interfaceName: Name of the interface to set the traffic control
    #   int throughput: Value of the throughput in bits
    #    
    # Return:
    #   Returns a ConfigParser instance with the config file read
    def setInterfaceProperties(self, interfaceName: str, throughput: str, delay: str, jitter: str) -> None:
        self.run(f"tc qdisc add dev {interfaceName} root netem delay {delay} {jitter} rate {throughput}")