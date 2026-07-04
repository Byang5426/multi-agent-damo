"""Agents package."""

from multi_agent.agents.analyzer import AnalyzerWorker
from multi_agent.agents.base_worker import BaseWorker, WorkerOutput
from multi_agent.agents.coder import CoderWorker
from multi_agent.agents.tester import TesterWorker

# Lazy worker registry: name -> class (instantiated on first use)
_WORKER_CLASSES: dict[str, type[BaseWorker]] = {
    "analyzer": AnalyzerWorker,
    "coder": CoderWorker,
    "tester": TesterWorker,
}

_WORKER_INSTANCES: dict[str, BaseWorker] = {}


def get_worker(name: str) -> BaseWorker:
    """Get a worker agent by name (lazy initialization)."""
    if name not in _WORKER_CLASSES:
        raise ValueError(
            f"Unknown worker: {name}. Available: {list(_WORKER_CLASSES.keys())}"
        )
    if name not in _WORKER_INSTANCES:
        _WORKER_INSTANCES[name] = _WORKER_CLASSES[name]()
    return _WORKER_INSTANCES[name]


@property
def WORKER_REGISTRY() -> dict[str, type[BaseWorker]]:  # type: ignore[misc]
    return _WORKER_CLASSES


__all__ = [
    "BaseWorker",
    "WorkerOutput",
    "AnalyzerWorker",
    "CoderWorker",
    "TesterWorker",
    "get_worker",
]
