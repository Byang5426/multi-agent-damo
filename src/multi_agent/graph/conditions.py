"""LangGraph 工作流条件路由函数。"""

from multi_agent.graph.state import WorkflowState
from multi_agent.models.task import TaskStatus


def route_after_gateway(state: WorkflowState) -> str:
    """Gateway 路由后决定使用哪个处理器。"""
    decision = state.get("route_decision", {})
    route = decision.get("route", "instant")
    if route == "blocked":
        return "blocked"
    elif route == "project":
        return "project"
    elif route == "scheduled":
        return "scheduled"  # MVP 阶段暂不支持，返回友好提示
    else:
        return "instant"


def route_after_worker(state: WorkflowState) -> str:
    """Worker 执行后决定下一步操作。"""
    tasks = state["tasks"]
    idx = state["current_task_index"]
    task = tasks[idx]

    if task.get("status") == TaskStatus.FAILED.value:
        return "failure"
    elif task.get("status") == TaskStatus.REVIEW.value:
        return "review"
    else:
        return "next"


def route_after_review(state: WorkflowState) -> str:
    """PM 审查后决定下一步操作。"""
    tasks = state["tasks"]
    idx = state["current_task_index"]
    task = tasks[idx]

    if task.get("status") == TaskStatus.DONE.value:
        return "next"
    elif task.get("status") == TaskStatus.TODO.value:
        # 被拒绝，检查是否超过重试上限
        if task.get("retry_count", 0) >= task.get("max_retries", 3):
            return "max_retries"
        return "retry"
    else:
        return "next"


def route_after_failure(state: WorkflowState) -> str:
    """失败处理后决定下一步操作。"""
    tasks = state["tasks"]
    idx = state["current_task_index"]
    task = tasks[idx]

    if task.get("status") == TaskStatus.TODO.value:
        return "retry"
    elif task.get("status") == TaskStatus.HUMAN_PENDING.value:
        return "skip_to_next"
    else:
        return "skip_to_next"  # FAILED


def route_next_task(state: WorkflowState) -> str:
    """推进到下一个任务或执行项目收尾。"""
    tasks = state["tasks"]
    idx = state["current_task_index"]

    # 检查是否有人工待处理任务——有则跳过剩余任务直接收尾
    for t in tasks:
        if t.get("status") == TaskStatus.HUMAN_PENDING.value:
            return "finalize"

    if idx + 1 < len(tasks):
        return "continue"
    else:
        return "finalize"
