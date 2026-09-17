import os

from .backends.docker import DockerBackend
from .backends.k3s import K3sBackend

_BACKENDS = {
    "docker": DockerBackend,
    "k3s": K3sBackend,
}

_backend = None
_backend_name = None
_node_instantiated = False


# Brief: Selects the infra backend by name. Raises if a node has already
# been instantiated (changing backend mid-topology is not supported) or if
# the name is not a known backend.
# Params:
#   String name: One of the keys in _BACKENDS
# Return:
#   None
def set_backend(name: str) -> None:
    global _backend, _backend_name

    if _node_instantiated:
        raise Exception("Cannot change backend after a node has already been instantiated")

    if name not in _BACKENDS:
        raise ValueError(f"Invalid backend '{name}'. Valid backends: {', '.join(sorted(_BACKENDS))}")

    _backend_name = name
    _backend = _BACKENDS[name]()


# Brief: Returns the current infra backend, selecting the default one
# (docker, or LFT_BACKEND env var) on first use.
# Params:
# Return:
#   InfraBackend instance
def get_backend():
    global _backend
    if _backend is None:
        set_backend(os.environ.get("LFT_BACKEND", "docker"))
    return _backend


# Brief: Marks that a node has been instantiated, locking the backend choice.
# Params:
# Return:
#   None
def mark_node_instantiated() -> None:
    global _node_instantiated
    _node_instantiated = True
