"""LangGraph 工作流条件路由函数。"""

from multi_agent.graph.state import WorkflowState
from multi_agent.models.task import TaskStatus


def route_after_gateway(state: WorkflowState) -> str:
    """Decide which handler to use after gateway routing."""
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
    """Decide next step after worker execution."""
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
    """Decide next step after PM review."""
    tasks = state["tasks"]
    idx = state["current_task_index"]
    task = tasks[idx]

    if task.get("status") == TaskStatus.DONE.value:
        return "next"
    elif task.get("status") == TaskStatus.TODO.value:
        # Rejected, check retry limit
        if task.get("retry_count", 0) >= task.get("max_retries", 3):
            return "max_retries"
        return "retry"
    else:
        return "next"


def route_after_failure(state: WorkflowState) -> str:
    """Decide next step after failure handling."""
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
    """Move to next task or finalize."""
    tasks = state["tasks"]
    idx = state["current_task_index"]

    # Check for human pending - skip remaining if any task needs human
    for t in tasks:
        if t.get("status") == TaskStatus.HUMAN_PENDING.value:
            return "finalize"

    if idx + 1 < len(tasks):
        return "continue"
    else:
        return "finalize"
