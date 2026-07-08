"""LangGraph 工作流图构建与运行入口。

Implements the two-layer orchestration pattern:
- Gateway routes to instant or project handler
- Project handler: PM decompose -> Worker execute -> PM review (loop)
"""

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


# ── Build the graph ──


def build_workflow(store: Optional[PgStore] = None) -> StateGraph:
    """Build and compile the LangGraph workflow.

    Args:
        store: Optional PgStore for persistence. If None, runs in-memory only.

    Returns:
        Compiled LangGraph ready to invoke.
    """
    graph = StateGraph(WorkflowState)

    # Add nodes
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

    # Entry point
    graph.set_entry_point("gateway_route")

    # Gateway -> route decision
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

    # Instant / Blocked / Scheduled -> END
    graph.add_edge("instant_handler", END)
    graph.add_edge("blocked_handler", END)
    graph.add_edge("scheduled_handler", END)

    # Project flow: init -> worker_execute
    graph.add_edge("project_init", "worker_execute")

    # Worker -> review or failure
    graph.add_conditional_edges(
        "worker_execute",
        route_after_worker,
        {
            "review": "pm_review",
            "failure": "handle_failure",
            "next": "advance_task",  # Skip review if already done
        },
    )

    # Review -> next step
    graph.add_conditional_edges(
        "pm_review",
        route_after_review,
        {
            "next": "advance_task",
            "retry": "worker_execute",
            "max_retries": "handle_failure",
        },
    )

    # Failure handling -> retry or skip
    graph.add_conditional_edges(
        "handle_failure",
        route_after_failure,
        {
            "retry": "worker_execute",
            "skip_to_next": "advance_task",
        },
    )

    # Advance task -> next or finalize
    graph.add_conditional_edges(
        "advance_task",
        route_next_task,
        {
            "continue": "worker_execute",
            "finalize": "project_finalize",
        },
    )

    # Finalize -> END
    graph.add_edge("project_finalize", END)

    return graph


# ── High-level invoke helper ──


async def run_workflow(
    user_input: str,
    tenant_id: str = "default",
    user_id: str = "anonymous",
    request_id: str = "",
    store: Optional[PgStore] = None,
) -> dict[str, Any]:
    """Run the full workflow for a user request.

    This is the main entry point called by the API layer.
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

    # Remove internal fields from response
    result.pop("_store", None)

    return result
