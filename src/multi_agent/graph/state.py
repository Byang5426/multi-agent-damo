"""LangGraph 工作流状态定义。"""

from typing import Annotated, Any, Optional, TypedDict


class WorkflowState(TypedDict, total=False):
    """LangGraph 工作流中流转的共享状态。"""

    # 输入
    user_input: str
    tenant_id: str
    user_id: str
    request_id: str

    # 路由决策
    route_decision: Optional[dict]  # RouteDecision 的 dict 形式

    # 项目上下文
    project: Optional[dict]  # Project 的 dict 形式
    tasks: list[dict]        # Task 列表的 dict 形式
    current_task_index: int

    # Worker 输出
    worker_output: Optional[dict]  # WorkerOutput 的 dict 形式
    rejection_reason: Optional[str]

    # 结果
    final_response: str
    schedule_id: Optional[str]     # 由 scheduled_handler 设置
    trace_logs: Annotated[list, lambda x, y: x + y]  # 累加器：合并所有节点的追踪记录

    # 错误处理
    error: Optional[str]

    # 内部字段：SSE 事件队列（用于流式输出执行流程）
    _event_queue: Any  # asyncio.Queue，流式模式下注入，非流式时为 None
    _store: Any        # PgStore 实例
