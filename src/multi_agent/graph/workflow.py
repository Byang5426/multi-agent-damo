"""LangGraph state graph: core workflow orchestration.

Implements the two-layer orchestration pattern:
- Gateway routes to instant or project handler
- Project handler: PM decompose -> Worker execute -> PM review (loop)
"""

import logging
from typing import Annotated, Any, Optional, TypedDict

from langgraph.graph import END, StateGraph

from multi_agent.agents import get_worker
from multi_agent.agents.pm_agent import PMAgent
from multi_agent.gateway.router import GatewayRouter, RouteDecision
from multi_agent.models.project import Project, ProjectStatus
from multi_agent.models.task import Task, TaskStatus
from multi_agent.store.pg_store import PgStore

logger = logging.getLogger(__name__)


# ── Graph State ──


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
    trace_logs: Annotated[list, lambda x, y: x + y]  # Accumulate traces

    # Error handling
    error: Optional[str]


# ── Agent instances (lazy-initialized) ──

_router: Optional[GatewayRouter] = None
_pm: Optional[PMAgent] = None


def _get_router() -> GatewayRouter:
    global _router
    if _router is None:
        _router = GatewayRouter()
    return _router


def _get_pm() -> PMAgent:
    global _pm
    if _pm is None:
        _pm = PMAgent()
    return _pm


# ── Node functions ──


async def gateway_route(state: WorkflowState) -> dict[str, Any]:
    """Gateway node: classify user input and decide routing."""
    router = _get_router()
    decision = await router.route(state["user_input"])
    logger.info(
        "Gateway routed to '%s': %s", decision.route, decision.reason
    )
    return {
        "route_decision": decision.model_dump(),
        "trace_logs": [],
    }


async def instant_handler(state: WorkflowState) -> dict[str, Any]:
    """Handle instant (single-step) tasks by calling the appropriate worker."""
    decision = RouteDecision(**state["route_decision"])
    worker_name = decision.suggested_worker or "analyzer"

    logger.info("Instant task: routing to worker '%s'", worker_name)
    worker = get_worker(worker_name)
    output, trace = await worker.execute(
        task_id="instant-" + state["request_id"],
        description=state["user_input"],
    )

    return {
        "worker_output": output.model_dump(),
        "final_response": output.summary,
        "trace_logs": [trace.model_dump()],
    }


async def blocked_handler(state: WorkflowState) -> dict[str, Any]:
    """Handle blocked requests (injection detected)."""
    decision = RouteDecision(**state["route_decision"])
    return {
        "final_response": f"Request blocked: {decision.reason}",
        "error": "blocked",
        "trace_logs": [],
    }


async def project_init(state: WorkflowState) -> dict[str, Any]:
    """Initialize a project: create project record and let PM decompose."""
    store: PgStore = state.get("_store")  # type: ignore[assignment]

    # Create project
    import uuid

    project_id = f"PRJ-{uuid.uuid4().hex[:8]}"
    project = Project(
        project_id=project_id,
        title="New Project",
        description=state["user_input"],
        status=ProjectStatus.IN_PROGRESS,
        tenant_id=state["tenant_id"],
        created_by=state["user_id"],
    )

    if store:
        await store.create_project(project)

    # PM decomposes the project into tasks
    pm = _get_pm()
    tasks, trace = await pm.decompose(state["user_input"], project_id)

    if not tasks:
        return {
            "project": project.model_dump(),
            "tasks": [],
            "final_response": "PM could not decompose this project. Please try rephrasing.",
            "error": "decomposition_failed",
            "trace_logs": [trace.model_dump()],
        }

    # Persist tasks
    if store:
        for task in tasks:
            await store.create_task(task)

    logger.info(
        "PM decomposed project '%s' into %d tasks", project_id, len(tasks)
    )

    return {
        "project": project.model_dump(),
        "tasks": [t.model_dump() for t in tasks],
        "current_task_index": 0,
        "trace_logs": [trace.model_dump()],
    }


async def worker_execute(state: WorkflowState) -> dict[str, Any]:
    """Execute the current task with the assigned worker."""
    tasks = state["tasks"]
    idx = state["current_task_index"]
    task_data = tasks[idx]
    task = Task(**task_data)

    # Mark task as DOING
    task.transition(TaskStatus.DOING, updated_by="system")
    store: PgStore = state.get("_store")  # type: ignore[assignment]
    if store:
        await store.update_task(task)

    # Get context from previous completed tasks
    context = {}
    if idx > 0:
        prev_artifacts = []
        for t in tasks[:idx]:
            if t.get("status") == TaskStatus.DONE.value:
                for a in t.get("artifacts", []):
                    prev_artifacts.append(a.get("content", ""))
        if prev_artifacts:
            context["previous_artifacts"] = "\n---\n".join(prev_artifacts[:3])

    # Execute worker
    worker_name = task.assigned_worker or "analyzer"
    worker = get_worker(worker_name)
    output, trace = await worker.execute(
        task_id=task.task_id,
        description=task.description,
        context=context,
        rejection_reason=state.get("rejection_reason"),
    )

    logger.info(
        "Worker '%s' completed task '%s': %s",
        worker_name,
        task.task_id,
        output.status,
    )

    # Update task with output
    task.output_summary = output.summary
    if output.status == "success":
        task.transition(TaskStatus.REVIEW, updated_by=worker_name)
    else:
        task.last_error = output.error
        task.transition(TaskStatus.FAILED, updated_by=worker_name)

    # Save artifacts
    task.artifacts.extend(output.artifacts)
    if store:
        await store.update_task(task)

    # Update the in-state task list
    updated_tasks = list(tasks)
    updated_tasks[idx] = task.model_dump()

    return {
        "tasks": updated_tasks,
        "worker_output": output.model_dump(),
        "rejection_reason": None,
        "trace_logs": [trace.model_dump()],
    }


async def pm_review(state: WorkflowState) -> dict[str, Any]:
    """PM reviews the current task's output."""
    tasks = state["tasks"]
    idx = state["current_task_index"]
    task = Task(**tasks[idx])

    # Only review if task is in REVIEW state
    if task.status != TaskStatus.REVIEW:
        # Skip review for non-review tasks (already failed, etc.)
        return {"trace_logs": []}

    # Gather artifact contents for review
    artifact_contents = [a.content for a in task.artifacts if a.content]

    pm = _get_pm()
    decision, trace = await pm.review(
        task=task,
        worker_summary=task.output_summary or "",
        artifact_contents=artifact_contents,
    )

    store: PgStore = state.get("_store")  # type: ignore[assignment]

    if decision.approved:
        task.transition(TaskStatus.DONE, updated_by="pm")
        logger.info("PM approved task '%s'", task.task_id)
    else:
        # Rejected - go back to TODO for retry
        task.transition(TaskStatus.TODO, updated_by="pm")
        task.retry_count += 1
        logger.info(
            "PM rejected task '%s': %s", task.task_id, decision.reason
        )

    if store:
        await store.update_task(task)

    # Update the in-state task list
    updated_tasks = list(tasks)
    updated_tasks[idx] = task.model_dump()

    return {
        "tasks": updated_tasks,
        "trace_logs": [trace.model_dump()],
    }


async def handle_failure(state: WorkflowState) -> dict[str, Any]:
    """Handle task failure: retry, escalate, or abort."""
    tasks = state["tasks"]
    idx = state["current_task_index"]
    task = Task(**tasks[idx])

    pm = _get_pm()
    decision, trace = await pm.handle_failure(
        task=task,
        error=task.last_error or "Unknown error",
    )

    store: PgStore = state.get("_store")  # type: ignore[assignment]

    if decision.action == "retry":
        task.transition(TaskStatus.TODO, updated_by="pm")
        task.retry_count += 1
        logger.info("PM decided to retry task '%s'", task.task_id)
    elif decision.action == "escalate_to_human":
        task.transition(TaskStatus.HUMAN_PENDING, updated_by="pm")
        logger.info("PM escalated task '%s' to human", task.task_id)
    else:
        task.transition(TaskStatus.FAILED, updated_by="pm")
        logger.info("PM aborted task '%s'", task.task_id)

    if store:
        await store.update_task(task)

    updated_tasks = list(tasks)
    updated_tasks[idx] = task.model_dump()

    return {
        "tasks": updated_tasks,
        "trace_logs": [trace.model_dump()],
    }


async def project_finalize(state: WorkflowState) -> dict[str, Any]:
    """Finalize the project: summarize results."""
    tasks = state["tasks"]
    project_data = state.get("project")

    # Build summary
    done = sum(1 for t in tasks if t.get("status") == TaskStatus.DONE.value)
    failed = sum(1 for t in tasks if t.get("status") == TaskStatus.FAILED.value)
    human_pending = sum(
        1 for t in tasks if t.get("status") == TaskStatus.HUMAN_PENDING.value
    )

    summary_parts = [
        f"Project '{project_data.get('title', 'Unknown')}' completed.",
        f"Tasks: {done} done, {failed} failed, {human_pending} pending human review.",
    ]

    if done > 0:
        summary_parts.append("\nCompleted task summaries:")
        for t in tasks:
            if t.get("status") == TaskStatus.DONE.value:
                summary_parts.append(f"  - {t['title']}: {t.get('output_summary', 'N/A')}")

    # Update project status
    store: PgStore = state.get("_store")  # type: ignore[assignment]
    if store and project_data:
        status = (
            ProjectStatus.COMPLETED
            if failed == 0 and human_pending == 0
            else ProjectStatus.FAILED
        )
        await store.update_project_status(project_data["project_id"], status)

    return {"final_response": "\n".join(summary_parts)}


# ── Conditional edge functions ──


def route_after_gateway(state: WorkflowState) -> str:
    """Decide which handler to use after gateway routing."""
    decision = state.get("route_decision", {})
    route = decision.get("route", "instant")
    if route == "blocked":
        return "blocked"
    elif route == "project":
        return "project"
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


def advance_task_index(state: WorkflowState) -> dict[str, Any]:
    """Advance to the next task in the project."""
    return {"current_task_index": state["current_task_index"] + 1}


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
        },
    )

    # Instant / Blocked -> END
    graph.add_edge("instant_handler", END)
    graph.add_edge("blocked_handler", END)

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
        "trace_logs": [],
        "error": None,
        "_store": store,  # type: ignore[dict-item]
    }

    result = await compiled.ainvoke(initial_state)

    # Remove internal fields from response
    result.pop("_store", None)

    return result
