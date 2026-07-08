"""LangGraph 工作流状态定义。"""

from typing import Annotated, Optional, TypedDict


class WorkflowState(TypedDict):
    """State that flows through the LangGraph."""

    # Input
    user_input: str
    tenant_id: str
    user_id: str
    request_id: str

    # Routing
    route_decision: Optional[dict]  # RouteDecision as dict

    # Project context
    project: Optional[dict]  # Project as dict
    tasks: list[dict]  # List of Task as dict
    current_task_index: int

    # Worker output
    worker_output: Optional[dict]  # WorkerOutput as dict
    rejection_reason: Optional[str]

    # Results
    final_response: str
    schedule_id: Optional[str]  # Set by scheduled_handler
    trace_logs: Annotated[list, lambda x, y: x + y]  # Accumulate traces

    # Error handling
    error: Optional[str]
