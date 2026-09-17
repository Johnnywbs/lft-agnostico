from abc import ABC, abstractmethod


# Brief: Abstract interface for an infrastructure backend (Docker, K3s, ...).
# Encapsulates the container lifecycle operations that today are hardcoded
# to Docker across profissa_lft, so that other substrates can be plugged in.
class InfraBackend(ABC):
    @abstractmethod
    def exec_prefix(self, name: str, interactive: bool = False) -> str:
        pass

    @abstractmethod
    def exec_argv(self, name: str) -> list:
        pass

    @abstractmethod
    def exec_detached(self, name: str, cmd: str):
        pass

    @abstractmethod
    def node_pid(self, name: str) -> str:
        pass

    @abstractmethod
    def node_exists(self, name: str) -> bool:
        pass

    @abstractmethod
    def node_ip(self, name: str) -> str:
        pass

    @abstractmethod
    def cleanup(self):
        pass

    @abstractmethod
    def create_node(self, name: str, image: str, dockerCommand='', **opts):
        pass

    @abstractmethod
    def delete_node(self, name: str):
        pass

    @abstractmethod
    def enable_namespace(self, name: str):
        pass

    @abstractmethod
    def copy_to_node(self, name: str, src: str, dst: str):
        pass

    @abstractmethod
    def copy_from_node(self, name: str, src: str, dst: str):
        pass
