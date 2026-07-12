"""Agent 包：提供所有 Worker 和 PM Agent 的注册与访问。"""

from multi_agent.agents.analyzer import AnalyzerWorker
from multi_agent.agents.base_worker import BaseWorker, WorkerOutput
from multi_agent.agents.coder import CoderWorker
from multi_agent.agents.tester import TesterWorker

# 懒加载 Worker 注册表：name -> 类（首次使用时实例化）
_WORKER_CLASSES: dict[str, type[BaseWorker]] = {
    "analyzer": AnalyzerWorker,
    "coder": CoderWorker,
    "tester": TesterWorker,
}

_WORKER_INSTANCES: dict[str, BaseWorker] = {}


def get_worker(name: str) -> BaseWorker:
    """根据名称获取 Worker 实例（懒初始化）。"""
    if name not in _WORKER_CLASSES:
        raise ValueError(
            f"Unknown worker: {name}. Available: {list(_WORKER_CLASSES.keys())}"
        )
    if name not in _WORKER_INSTANCES:
        _WORKER_INSTANCES[name] = _WORKER_CLASSES[name]()
    return _WORKER_INSTANCES[name]


# Worker 注册表：name -> 类（只读引用）
WORKER_REGISTRY: dict[str, type[BaseWorker]] = _WORKER_CLASSES


__all__ = [
    "BaseWorker",
    "WorkerOutput",
    "AnalyzerWorker",
    "CoderWorker",
    "TesterWorker",
    "get_worker",
    "WORKER_REGISTRY",
]
