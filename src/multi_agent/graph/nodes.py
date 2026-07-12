"""LangGraph 工作流节点函数。"""

import json
import logging
import time
import uuid
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from multi_agent.agents import get_worker
from multi_agent.agents.pm_agent import PMAgent
from multi_agent.config import settings as app_settings
from multi_agent.gateway.router import GatewayRouter, RouteDecision
from multi_agent.graph.state import WorkflowState
from multi_agent.models.project import Project, ProjectStatus
from multi_agent.models.task import Task, TaskStatus
from multi_agent.notify.service import NotificationPayload, get_notification_service
from multi_agent.store.pg_store import PgStore
from multi_agent.trace.langfuse_integration import report_trace_to_langfuse

logger = logging.getLogger(__name__)


# ── Agent 实例（懒初始化，避免重复创建）──

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


def _emit(state: WorkflowState, event_type: str, data: dict) -> None:
    """向事件队列发射一个 SSE 事件（仅在流式模式下生效）。

    Args:
        state: 当前工作流状态。
        event_type: 事件类型，如 'node_start' / 'node_end'。
        data: 事件负载数据。
    """
    queue = state.get("_event_queue")
    if queue is not None:
        queue.put_nowait({"type": event_type, "data": data, "ts": time.time()})


# ── 工作流节点函数 ──


async def gateway_route(state: WorkflowState) -> dict[str, Any]:
    """Gateway 节点：对用户输入进行分类并决定路由。"""
    _emit(state, "node_start", {"node": "gateway", "label": "Gateway 路由分类"})

    router = _get_router()
    decision = await router.route(state["user_input"])
    logger.info(
        "Gateway routed to '%s': %s", decision.route, decision.reason
    )

    _emit(state, "node_end", {
        "node": "gateway",
        "route": decision.route,
        "reason": decision.reason,
        "suggested_worker": decision.suggested_worker,
    })

    return {
        "route_decision": decision.model_dump(),
        "trace_logs": [],
    }


async def instant_handler(state: WorkflowState) -> dict[str, Any]:
    """处理即时（单步）任务：调用对应的 Worker 执行。"""
    decision = RouteDecision(**state["route_decision"])
    # LLM 可能返回字符串 "null" 而非 JSON null，需额外校验
    worker_name = decision.suggested_worker
    if not worker_name or worker_name.lower() in ("null", "none", ""):
        worker_name = "analyzer"

    _emit(state, "node_start", {
        "node": "worker",
        "worker": worker_name,
        "label": f"即时任务 → {worker_name}",
    })

    logger.info("Instant task: routing to worker '%s'", worker_name)
    worker = get_worker(worker_name)
    output, trace = await worker.execute(
        task_id="instant-" + state["request_id"],
        description=state["user_input"],
    )

    _emit(state, "node_end", {
        "node": "worker",
        "worker": worker_name,
        "status": output.status,
        "summary": output.summary,
    })

    # 将追踪记录持久化到数据库（PostgreSQL 本地备份）
    store: PgStore = state.get("_store")  # type: ignore[assignment]
    if store:
        await store.save_trace(trace)
    # 同步上报到 Langfuse（fire-and-forget，失败不影响主流程）
    await report_trace_to_langfuse(trace)

    return {
        "worker_output": output.model_dump(),
        "final_response": output.summary,
        "trace_logs": [trace.model_dump()],
    }


async def blocked_handler(state: WorkflowState) -> dict[str, Any]:
    """处理被拦截的请求（检测到注入攻击）。"""
    decision = RouteDecision(**state["route_decision"])
    _emit(state, "node_end", {"node": "blocked", "reason": decision.reason})
    return {
        "final_response": f"Request blocked: {decision.reason}",
        "error": "blocked",
        "trace_logs": [],
    }


# ── 调度任务解析 Prompt ──

_SCHEDULE_PARSE_PROMPT = (
    "你是一个调度任务解析器。将用户的自然语言请求解析为结构化调度任务。\n\n"
    "输出JSON格式：\n"
    '{"name": "任务名称", "cron_expression": "分 时 日 月 周", '
    '"description": "要执行的任务描述", "timezone": "Asia/Shanghai"}\n\n'
    "常见 cron 表达式参考：\n"
    '- 每天早上9点: "0 9 * * *"\n'
    '- 每周一早上9点: "0 9 * * 1"\n'
    '- 每小时: "0 * * * *"\n'
    '- 每天下午6点: "0 18 * * *"\n\n'
    "规则：\n"
    "- 从用户请求中提取执行频率和时间\n"
    "- 生成合理的 cron 表达式\n"
    "- description 应包含执行所需的所有上下文\n"
)


async def scheduled_handler(state: WorkflowState) -> dict[str, Any]:
    """处理定时任务请求：注册新的调度任务。

    将用户的自然语言调度请求解析为结构化调度任务，并注册到调度器。
    """
    _emit(state, "node_start", {"node": "scheduler", "label": "解析定时任务"})

    store: PgStore = state.get("_store")  # type: ignore[assignment]
    user_input = state["user_input"]
    tenant_id = state.get("tenant_id", "default")
    user_id = state.get("user_id", "anonymous")

    try:
        llm = ChatOpenAI(
            model=app_settings.gateway_model,
            api_key=app_settings.openai_api_key,
            base_url=app_settings.openai_base_url or None,
            temperature=0,
        )

        response = await llm.ainvoke(
            [
                SystemMessage(content=_SCHEDULE_PARSE_PROMPT),
                HumanMessage(content=user_input),
            ],
            response_format={"type": "json_object"},
        )

        data = json.loads(response.content)

        # 通过 ScheduleManager 创建调度任务
        from multi_agent.scheduler.manager import ScheduleManager

        schedule_mgr = ScheduleManager(store)
        schedule = await schedule_mgr.create_schedule(
            name=data.get("name", "Scheduled Task"),
            description=data.get("description", user_input),
            cron_expression=data.get("cron_expression", "0 9 * * *"),
            timezone=data.get("timezone", "Asia/Shanghai"),
            tenant_id=tenant_id,
            created_by=user_id,
        )

        _emit(state, "node_end", {
            "node": "scheduler",
            "schedule_id": schedule.schedule_id,
            "name": schedule.name,
        })

        return {
            "final_response": (
                f"定时任务已创建成功！\n"
                f"  调度ID: {schedule.schedule_id}\n"
                f"  名称: {schedule.name}\n"
                f"  执行频率: {schedule.cron_expression}\n"
                f"  下次执行: {schedule.next_run_at or '待计算'}\n\n"
                f"可通过 GET /api/v1/schedules 查看所有调度任务。"
            ),
            "schedule_id": schedule.schedule_id,
            "trace_logs": [],
        }

    except Exception as e:
        logger.error("Scheduled handler failed: %s", e, exc_info=True)
        return {
            "final_response": (
                f"定时任务创建失败: {e}\n"
                "请检查输入格式后重试。"
            ),
            "error": f"schedule_creation_failed: {e}",
            "trace_logs": [],
        }


async def project_init(state: WorkflowState) -> dict[str, Any]:
    """初始化项目：创建项目记录并由 PM 拆解任务。"""
    _emit(state, "node_start", {"node": "project_init", "label": "PM 项目拆解"})

    store: PgStore = state.get("_store")  # type: ignore[assignment]

    # 创建项目记录
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

    # PM 将项目拆解为多个子任务
    pm = _get_pm()
    tasks, trace = await pm.decompose(state["user_input"], project_id)

    # 将追踪记录持久化到数据库（PostgreSQL 本地备份）
    if store:
        await store.save_trace(trace)
    # 同步上报到 Langfuse
    await report_trace_to_langfuse(trace)

    if not tasks:
        return {
            "project": project.model_dump(),
            "tasks": [],
            "final_response": "PM could not decompose this project. Please try rephrasing.",
            "error": "decomposition_failed",
            "trace_logs": [trace.model_dump()],
        }

    # 将任务持久化到数据库
    if store:
        for task in tasks:
            await store.create_task(task)

    _emit(state, "node_end", {
        "node": "project_init",
        "project_id": project_id,
        "task_count": len(tasks) if tasks else 0,
    })

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
    """使用分配的 Worker 执行当前任务。"""
    tasks = state["tasks"]
    idx = state["current_task_index"]
    task_data = tasks[idx]
    task = Task(**task_data)

    worker_name = task.assigned_worker or "analyzer"
    _emit(state, "node_start", {
        "node": "worker",
        "worker": worker_name,
        "task_title": task.title,
        "task_index": idx + 1,
        "task_total": len(tasks),
        "label": f"Worker [{worker_name}] 执行任务 ({idx + 1}/{len(tasks)})",
    })

    # 将任务状态标记为 DOING
    task.transition(TaskStatus.DOING, updated_by="system")
    store: PgStore = state.get("_store")  # type: ignore[assignment]
    if store:
        await store.update_task(task)

    # 收集前序已完成任务的产物作为上下文
    context = {}
    if idx > 0:
        prev_artifacts = []
        for t in tasks[:idx]:
            if t.get("status") == TaskStatus.DONE.value:
                for a in t.get("artifacts", []):
                    prev_artifacts.append(a.get("content", ""))
        if prev_artifacts:
            context["previous_artifacts"] = "\n---\n".join(prev_artifacts[:3])

    # 执行 Worker
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

    _emit(state, "node_end", {
        "node": "worker",
        "worker": worker_name,
        "status": output.status,
        "summary": output.summary,
    })

    # 更新任务的输出和状态
    task.output_summary = output.summary
    if output.status == "success":
        task.transition(TaskStatus.REVIEW, updated_by=worker_name)
    else:
        task.last_error = output.error
        task.transition(TaskStatus.FAILED, updated_by=worker_name)

    # 保存产物
    task.artifacts.extend(output.artifacts)
    if store:
        await store.update_task(task)
        await store.save_trace(trace)
    # 同步上报到 Langfuse
    await report_trace_to_langfuse(trace)

    # 更新状态中的任务列表
    updated_tasks = list(tasks)
    updated_tasks[idx] = task.model_dump()

    return {
        "tasks": updated_tasks,
        "worker_output": output.model_dump(),
        "rejection_reason": None,
        "trace_logs": [trace.model_dump()],
    }


async def pm_review(state: WorkflowState) -> dict[str, Any]:
    """PM 审查当前任务的输出。"""
    tasks = state["tasks"]
    idx = state["current_task_index"]
    task = Task(**tasks[idx])

    _emit(state, "node_start", {
        "node": "pm_review",
        "label": f"PM 审查: {task.title}",
        "task_title": task.title,
    })

    # 仅当任务处于 REVIEW 状态时才审查
    if task.status != TaskStatus.REVIEW:
        # 非 REVIEW 状态的任务跳过审查（已失败等）
        return {"trace_logs": []}

    # 收集产物内容用于审查
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
        # 被拒绝——回退到 TODO 状态等待重试
        task.transition(TaskStatus.TODO, updated_by="pm")
        # retry_count 由 handle_failure 统一管理，此处不再递增
        logger.info(
            "PM rejected task '%s': %s", task.task_id, decision.reason
        )

    _emit(state, "node_end", {
        "node": "pm_review",
        "approved": decision.approved,
        "reason": decision.reason,
    })

    if store:
        await store.update_task(task)
        await store.save_trace(trace)
    # 同步上报到 Langfuse
    await report_trace_to_langfuse(trace)

    # 更新状态中的任务列表
    updated_tasks = list(tasks)
    updated_tasks[idx] = task.model_dump()

    return {
        "tasks": updated_tasks,
        "trace_logs": [trace.model_dump()],
    }


async def handle_failure(state: WorkflowState) -> dict[str, Any]:
    """处理任务失败：决定重试、转交人工或终止。"""
    _emit(state, "node_start", {"node": "failure", "label": "任务失败处理"})
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
    # 同步上报到 Langfuse
    await report_trace_to_langfuse(trace)

    # 当 PM 决策为 escalate_to_human 时，触发人工介入通知
    if decision.action == "escalate_to_human":
        try:
            project_data = state.get("project") or {}
            notify_svc = get_notification_service()
            payload = NotificationPayload(
                task_id=task.task_id,
                project_title=project_data.get("title", ""),
                failure_reason=task.last_error or decision.reason,
                output_summary=task.output_summary or "",
                tenant_id=state.get("tenant_id", "default"),
                assigned_worker=task.assigned_worker or "",
                retry_count=task.retry_count,
                max_retries=task.max_retries,
            )
            await notify_svc.notify(payload)
        except Exception as notify_err:
            logger.error("Notification failed (non-blocking): %s", notify_err)

    _emit(state, "node_end", {
        "node": "failure",
        "action": decision.action,
        "reason": decision.reason,
    })

    updated_tasks = list(tasks)
    updated_tasks[idx] = task.model_dump()

    return {
        "tasks": updated_tasks,
        "trace_logs": [trace.model_dump()],
    }


async def project_finalize(state: WorkflowState) -> dict[str, Any]:
    """项目收尾：汇总执行结果。"""
    tasks = state["tasks"]
    project_data = state.get("project")

    # 汇总统计
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

    # 更新项目状态
    store: PgStore = state.get("_store")  # type: ignore[assignment]
    if store and project_data:
        if failed == 0 and human_pending == 0:
            status = ProjectStatus.COMPLETED
        elif human_pending > 0:
            status = ProjectStatus.PAUSED
        else:
            status = ProjectStatus.FAILED
        await store.update_project_status(project_data["project_id"], status)

    final_text = "\n".join(summary_parts)
    _emit(state, "node_end", {
        "node": "finalize",
        "summary": final_text,
        "done": done,
        "failed": failed,
        "human_pending": human_pending,
    })

    return {"final_response": final_text}


def advance_task_index(state: WorkflowState) -> dict[str, Any]:
    """推进到项目中的下一个任务。"""
    next_idx = state["current_task_index"] + 1
    total = len(state.get("tasks", []))
    _emit(state, "node_start", {
        "node": "advance",
        "label": f"推进到下一个任务 ({next_idx + 1}/{total})",
        "next_index": next_idx,
    })
    return {"current_task_index": next_idx}
