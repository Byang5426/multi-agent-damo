"""工作流图构建与运行入口。

实现两层编排模式：
- Gateway 将请求路由到即时任务或项目型任务
- 项目型任务：PM 拆解 -> Worker 执行 -> PM 审查（循环）
"""

import asyncio
import logging
from typing import Any, Optional

from langgraph.graph import END, StateGraph

from multi_agent.graph.conditions import (
    route_after_failure,
    route_after_gateway,
    route_after_review,
    route_after_worker,
    route_next_task,
)
from multi_agent.graph.nodes import (
    advance_task_index,
    blocked_handler,
    gateway_route,
    handle_failure,
    instant_handler,
    pm_review,
    project_finalize,
    project_init,
    scheduled_handler,
    worker_execute,
)
from multi_agent.graph.state import WorkflowState
from multi_agent.store.pg_store import PgStore

logger = logging.getLogger(__name__)


# ── 构建工作流图 ──


def build_workflow(store: Optional[PgStore] = None) -> StateGraph:
    """构建并编译 LangGraph 工作流图。

    Args:
        store: 可选的持久化存储，传 None 则仅在内存中运行。

    Returns:
        编译完成、可直接调用的 LangGraph。
    """
    graph = StateGraph(WorkflowState)

    # 注册节点
    graph.add_node("gateway_route", gateway_route)
    graph.add_node("instant_handler", instant_handler)
    graph.add_node("blocked_handler", blocked_handler)
    graph.add_node("scheduled_handler", scheduled_handler)
    graph.add_node("project_init", project_init)
    graph.add_node("worker_execute", worker_execute)
    graph.add_node("pm_review", pm_review)
    graph.add_node("handle_failure", handle_failure)
    graph.add_node("project_finalize", project_finalize)
    graph.add_node("advance_task", advance_task_index)

    # 入口点
    graph.set_entry_point("gateway_route")

    # Gateway -> 路由决策
    graph.add_conditional_edges(
        "gateway_route",
        route_after_gateway,
        {
            "instant": "instant_handler",
            "project": "project_init",
            "blocked": "blocked_handler",
            "scheduled": "scheduled_handler",
        },
    )

    # 即时 / 拦截 / 定时 -> 结束
    graph.add_edge("instant_handler", END)
    graph.add_edge("blocked_handler", END)
    graph.add_edge("scheduled_handler", END)

    # 项目流程：初始化 -> Worker 执行
    graph.add_edge("project_init", "worker_execute")

    # Worker -> 审查或失败处理
    graph.add_conditional_edges(
        "worker_execute",
        route_after_worker,
        {
            "review": "pm_review",
            "failure": "handle_failure",
            "next": "advance_task",  # 已完成的直接推进
        },
    )

    # 审查 -> 下一步
    graph.add_conditional_edges(
        "pm_review",
        route_after_review,
        {
            "next": "advance_task",
            "retry": "worker_execute",
            "max_retries": "handle_failure",
        },
    )

    # 失败处理 -> 重试或跳过
    graph.add_conditional_edges(
        "handle_failure",
        route_after_failure,
        {
            "retry": "worker_execute",
            "skip_to_next": "advance_task",
        },
    )

    # 推进任务 -> 继续或收尾
    graph.add_conditional_edges(
        "advance_task",
        route_next_task,
        {
            "continue": "worker_execute",
            "finalize": "project_finalize",
        },
    )

    # 收尾 -> 结束
    graph.add_edge("project_finalize", END)

    return graph


# ── 高层级调用工具函数 ──


async def run_workflow(
    user_input: str,
    tenant_id: str = "default",
    user_id: str = "anonymous",
    request_id: str = "",
    store: Optional[PgStore] = None,
) -> dict[str, Any]:
    """运行用户请求的完整工作流。

    这是 API 层调用的主入口。
    """
    graph = build_workflow(store)
    compiled = graph.compile()

    initial_state: WorkflowState = {
        "user_input": user_input,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "request_id": request_id,
        "route_decision": None,
        "project": None,
        "tasks": [],
        "current_task_index": 0,
        "worker_output": None,
        "rejection_reason": None,
        "final_response": "",
        "schedule_id": None,
        "trace_logs": [],
        "error": None,
        "_store": store,  # type: ignore[dict-item]
    }

    result = await compiled.ainvoke(initial_state)

    # 从响应中移除内部字段
    result.pop("_store", None)
    result.pop("_event_queue", None)

    return result


async def run_workflow_streaming(
    user_input: str,
    tenant_id: str = "default",
    user_id: str = "anonymous",
    request_id: str = "",
    store: Optional[PgStore] = None,
    event_queue: Optional[asyncio.Queue] = None,
) -> dict[str, Any]:
    """运行用户请求的完整工作流（流式模式）。

    与 run_workflow 类似，但将 event_queue 注入状态，
    各节点会通过 _emit() 向队列发射事件，供 SSE 端点消费。
    工作流完成后，向队列发送 None 哨兵值以通知结束。
    """
    graph = build_workflow(store)
    compiled = graph.compile()

    initial_state: WorkflowState = {
        "user_input": user_input,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "request_id": request_id,
        "route_decision": None,
        "project": None,
        "tasks": [],
        "current_task_index": 0,
        "worker_output": None,
        "rejection_reason": None,
        "final_response": "",
        "schedule_id": None,
        "trace_logs": [],
        "error": None,
        "_store": store,
        "_event_queue": event_queue,
    }

    try:
        result = await compiled.ainvoke(initial_state)
    finally:
        # 无论成功还是异常，都发送哨兵值通知 SSE 生成器结束
        if event_queue is not None:
            event_queue.put_nowait(None)

    # 从响应中移除内部字段
    result.pop("_store", None)
    result.pop("_event_queue", None)

    return result
