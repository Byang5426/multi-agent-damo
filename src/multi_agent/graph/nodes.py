"""LangGraph 工作流节点函数。"""

import logging
import uuid
from typing import Any

from multi_agent.agents import get_worker
from multi_agent.agents.pm_agent import PMAgent
from multi_agent.gateway.router import GatewayRouter, RouteDecision
from multi_agent.graph.state import WorkflowState
from multi_agent.models.project import Project, ProjectStatus
from multi_agent.models.task import Task, TaskStatus
from multi_agent.store.pg_store import PgStore

logger = logging.getLogger(__name__)


# ── Agent instances (lazy-initialized) ──

_router: GatewayRouter | None = None
_pm: PMAgent | None = None


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

    # Persist trace to database
    store: PgStore = state.get("_store")  # type: ignore[assignment]
    if store:
        await store.save_trace(trace)

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


async def scheduled_handler(state: WorkflowState) -> dict[str, Any]:
    """Handle scheduled task requests (not yet supported in MVP)."""
    return {
        "final_response": (
            "定时任务功能尚未开放（MVP 阶段暂不支持）。"
            "请将您的请求作为即时任务或项目型任务重新提交。"
        ),
        "error": "scheduled_not_supported",
        "trace_logs": [],
    }


async def project_init(state: WorkflowState) -> dict[str, Any]:
    """Initialize a project: create project record and let PM decompose."""
    store: PgStore = state.get("_store")  # type: ignore[assignment]

    # Create project
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

    # Persist trace to database
    if store:
        await store.save_trace(trace)

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
        await store.save_trace(trace)

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
        # retry_count 由 handle_failure 统一管理，此处不再递增
        logger.info(
            "PM rejected task '%s': %s", task.task_id, decision.reason
        )

    if store:
        await store.update_task(task)
        await store.save_trace(trace)

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
        if task.status != TaskStatus.TODO:
            task.transition(TaskStatus.TODO, updated_by="pm")
        task.retry_count += 1
        logger.info("PM decided to retry task '%s'", task.task_id)
    elif decision.action == "escalate_to_human":
        if task.status != TaskStatus.HUMAN_PENDING:
            task.transition(TaskStatus.HUMAN_PENDING, updated_by="pm")
        logger.info("PM escalated task '%s' to human", task.task_id)
    else:  # abort
        if task.status not in (TaskStatus.FAILED, TaskStatus.DONE):
            task.transition(TaskStatus.FAILED, updated_by="pm")
        logger.info("PM aborted task '%s'", task.task_id)

    if store:
        await store.update_task(task)
        await store.save_trace(trace)

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
        if failed == 0 and human_pending == 0:
            status = ProjectStatus.COMPLETED
        elif human_pending > 0:
            status = ProjectStatus.PAUSED
        else:
            status = ProjectStatus.FAILED
        await store.update_project_status(project_data["project_id"], status)

    return {"final_response": "\n".join(summary_parts)}


def advance_task_index(state: WorkflowState) -> dict[str, Any]:
    """Advance to the next task in the project."""
    return {"current_task_index": state["current_task_index"] + 1}
