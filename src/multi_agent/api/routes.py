"""多Agent系统 API 路由定义。"""

import asyncio
import json
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from multi_agent.api.schemas import (
    ChatRequest,
    ChatResponse,
    HumanInterventionRequest,
    ProjectResponse,
    PromptUpdateRequest,
    ScheduleCreateRequest,
    ScheduleResponse,
    TaskResponse,
)
from multi_agent.config import settings as app_settings
from multi_agent.graph.workflow import run_workflow, run_workflow_streaming
from multi_agent.models.task import TaskStatus

logger = logging.getLogger(__name__)

router = APIRouter()


# ── 接口定义 ──


@router.post(
    "/chat",
    response_model=ChatResponse,
    summary="统一入口",
    description="Gateway接收所有请求，自动分类路由到即时任务或项目型任务处理链路。",
)
async def chat(request: Request, body: ChatRequest):
    """统一入口：Gateway 接收所有请求并自动路由。

    Gateway 会对请求进行意图分类，路由到：
    - instant_handler：即时任务，由单个 Worker 执行
    - project_handler：项目型任务，由 PM 拆解后多 Worker 协作执行
    """
    store = request.app.state.store

    try:
        result = await run_workflow(
            user_input=body.message,
            tenant_id=body.tenant_id,
            user_id=body.user_id,
            request_id=request.state.request_id,
            store=store,
        )

        # 提取项目ID（如有）
        project_data = result.get("project")
        project_id = project_data.get("project_id") if project_data else None

        return ChatResponse(
            response=result.get("final_response", ""),
            project_id=project_id,
            schedule_id=result.get("schedule_id"),
            tasks=result.get("tasks", []),
            trace_count=len(result.get("trace_logs", [])),
            error=result.get("error"),
        )
    except Exception as e:
        logger.error("聊天接口异常: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="服务内部错误，请稍后重试")


@router.post(
    "/chat/stream",
    summary="流式对话（SSE）",
    description="与 /chat 相同，但以 Server-Sent Events 流式输出执行流程。",
)
async def chat_stream(request: Request, body: ChatRequest):
    """流式对话端点：通过 SSE 实时推送工作流执行事件。"""
    store = request.app.state.store
    event_queue: asyncio.Queue = asyncio.Queue()

    def _json_safe(obj):
        """JSON 序列化辅助：将 datetime 等不可序列化对象转为字符串。"""
        if isinstance(obj, datetime):
            return obj.isoformat()
        return str(obj)

    async def event_generator():
        # 在后台任务中运行工作流
        workflow_task = asyncio.create_task(
            run_workflow_streaming(
                user_input=body.message,
                tenant_id=body.tenant_id,
                user_id=body.user_id,
                request_id=request.state.request_id,
                store=store,
                event_queue=event_queue,
            )
        )

        # 消费事件队列，逐条推送 SSE
        while True:
            event = await event_queue.get()
            if event is None:  # 哨兵值，工作流结束
                break
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

        # 等待工作流完成并推送最终结果
        try:
            result = await workflow_task
            # 清理内部字段
            result.pop("_store", None)
            result.pop("_event_queue", None)
            yield f"data: {json.dumps({'type': 'done', 'result': result}, ensure_ascii=False, default=_json_safe)}\n\n"
        except Exception as e:
            logger.error("流式工作流异常: %s", e, exc_info=True)
            yield f"data: {json.dumps({'type': 'error', 'error': str(e)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # 禁用 nginx 缓冲
        },
    )


@router.post(
    "/test-llm",
    summary="测试 LLM 连接",
    description="向当前配置的 LLM 发送一条简单消息，验证 API Key、Base URL 和模型是否可用。",
)
async def test_llm_connection():
    """测试 LLM 连接：发送一条简单消息验证配置是否正确。"""
    from langchain_core.messages import HumanMessage
    from langchain_openai import ChatOpenAI
    import time

    start = time.time()
    try:
        llm = ChatOpenAI(
            model=app_settings.openai_api_model,
            api_key=app_settings.openai_api_key,
            base_url=app_settings.openai_base_url or None,
            temperature=0,
            max_tokens=32,
        )
        response = await llm.ainvoke([HumanMessage(content="Say 'ok' in one word.")])
        elapsed = round((time.time() - start) * 1000)
        return {
            "success": True,
            "model": app_settings.openai_api_model,
            "base_url": app_settings.openai_base_url or "https://api.openai.com/v1",
            "response": str(response.content)[:100],
            "latency_ms": elapsed,
        }
    except Exception as e:
        elapsed = round((time.time() - start) * 1000)
        return {
            "success": False,
            "model": app_settings.openai_api_model,
            "base_url": app_settings.openai_base_url or "https://api.openai.com/v1",
            "error": str(e)[:200],
            "latency_ms": elapsed,
        }


@router.get(
    "/projects/{project_id}",
    response_model=ProjectResponse,
    summary="查询项目",
    description="获取项目详情及所有子任务状态。",
)
async def get_project(request: Request, project_id: str):
    """查询项目详情，包含该项目下所有子任务。"""
    store = request.app.state.store

    project = await store.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    tasks = await store.get_tasks_by_project(project_id)
    task_responses = [
        TaskResponse(
            task_id=t.task_id,
            title=t.title,
            status=t.status.value,
            assigned_worker=t.assigned_worker,
            retry_count=t.retry_count,
            output_summary=t.output_summary,
            last_error=t.last_error,
            artifacts_count=len(t.artifacts),
        )
        for t in tasks
    ]

    return ProjectResponse(
        project_id=project.project_id,
        title=project.title,
        description=project.description,
        status=project.status.value,
        tasks=task_responses,
    )


@router.get(
    "/tasks/{task_id}",
    response_model=TaskResponse,
    summary="查询任务",
    description="获取单个任务的详细信息。",
)
async def get_task(request: Request, task_id: str):
    """查询单个任务的详细信息。"""
    store = request.app.state.store

    task = await store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    return TaskResponse(
        task_id=task.task_id,
        title=task.title,
        status=task.status.value,
        assigned_worker=task.assigned_worker,
        retry_count=task.retry_count,
        output_summary=task.output_summary,
        last_error=task.last_error,
        artifacts_count=len(task.artifacts),
    )


@router.patch(
    "/tasks/{task_id}",
    summary="人工介入",
    description="处理 HUMAN_PENDING 状态的任务，支持重试或标记完成。",
)
async def human_intervention(
    request: Request,
    task_id: str,
    body: HumanInterventionRequest,
):
    """人工介入接口：处理 HUMAN_PENDING 状态的任务。

    允许人工操作员：
    - 设置状态为 TODO，触发任务重试
    - 设置状态为 DONE，直接标记任务为人工完成
    """
    store = request.app.state.store

    task = await store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    if task.status != TaskStatus.HUMAN_PENDING:
        raise HTTPException(
            status_code=400,
            detail=f"任务不在 HUMAN_PENDING 状态（当前状态: {task.status.value}）",
        )

    target_status = TaskStatus(body.status)
    if target_status not in (TaskStatus.TODO, TaskStatus.DONE):
        raise HTTPException(
            status_code=400,
            detail="只能转为 TODO（重试）或 DONE（完成）",
        )

    try:
        task.transition(target_status, updated_by=f"human:{body.resolution}")
        if body.comment:
            task.output_summary = (
                (task.output_summary or "") + f"\n[人工介入]: {body.comment}"
            )
        await store.update_task(task)

        return {
            "task_id": task_id,
            "status": task.status.value,
            "message": f"任务已处理: {body.resolution}",
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get(
    "/projects/{project_id}/trace",
    summary="调用链路",
    description="获取项目中所有Agent调用的Trace日志。",
)
async def get_project_trace(request: Request, project_id: str):
    """获取项目中所有任务的 Trace 调用链路日志。"""
    store = request.app.state.store

    project = await store.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    traces = await store.get_traces_by_project(project_id)
    return {
        "project_id": project_id,
        "trace_count": len(traces),
        "traces": [t.model_dump() for t in traces],
    }


# ── Prompt 管理 ──


@router.get(
    "/prompts",
    summary="查询 Prompt 列表",
    description="获取所有 Agent Prompt，可按 agent_name 过滤。",
)
async def list_prompts(request: Request, agent_name: Optional[str] = None):
    """列出所有 Prompt 或按 Agent 过滤。"""
    store = request.app.state.store
    prompts = await store.list_prompts(agent_name)
    return {
        "count": len(prompts),
        "prompts": [p.model_dump() for p in prompts],
    }


@router.get(
    "/prompts/{prompt_id}",
    summary="查询单个 Prompt",
    description="获取指定 prompt_id 的当前生效版本。",
)
async def get_prompt(request: Request, prompt_id: str):
    """获取单个 Prompt 的当前生效版本。"""
    store = request.app.state.store
    prompt = await store.get_active_prompt(prompt_id)
    if not prompt:
        raise HTTPException(status_code=404, detail="Prompt 不存在")
    return prompt.model_dump()


@router.put(
    "/prompts/{prompt_id}",
    summary="更新 Prompt",
    description="更新指定 Prompt 的内容，自动创建新版本。",
)
async def update_prompt(request: Request, prompt_id: str, body: PromptUpdateRequest):
    """更新 Prompt 内容，自动创建新版本。"""
    store = request.app.state.store
    updated = await store.update_prompt_content(prompt_id, body.content, body.description)
    if not updated:
        raise HTTPException(status_code=404, detail="Prompt 不存在")
    return {
        "message": f"Prompt '{prompt_id}' 已更新到版本 v{updated.version}",
        "prompt": updated.model_dump(),
    }


# ── 仪表盘与配置 ──


@router.get(
    "/stats",
    summary="仪表盘统计",
    description="返回项目总数、任务总数、按状态分组任务数、待人工处理数。",
)
async def get_stats(request: Request):
    """仪表盘统计数据。"""
    store = request.app.state.store
    return await store.get_stats()


@router.get(
    "/config",
    summary="读取运行配置",
    description="返回当前运行时配置（屏蔽敏感字段）。",
)
async def get_config(request: Request):
    """读取运行配置。"""
    store = request.app.state.store
    return await store.get_config()


# ── 定时任务管理 ──


@router.get(
    "/schedules",
    response_model=list[ScheduleResponse],
    summary="查询调度任务列表",
    description="获取所有定时调度任务。",
)
async def list_schedules(request: Request):
    """列出所有调度任务。"""
    schedule_store = request.app.state.store.schedule_store
    schedules = await schedule_store.list_schedules()
    return [
        ScheduleResponse(
            schedule_id=s.schedule_id,
            name=s.name,
            description=s.description,
            cron_expression=s.cron_expression,
            timezone=s.timezone,
            status=s.status.value,
            tenant_id=s.tenant_id,
            last_run_at=s.last_run_at.isoformat() if s.last_run_at else None,
            next_run_at=s.next_run_at.isoformat() if s.next_run_at else None,
            run_count=s.run_count,
            last_error=s.last_error,
        )
        for s in schedules
    ]


@router.post(
    "/schedules",
    response_model=ScheduleResponse,
    summary="创建调度任务",
    description="手动创建定时调度任务。",
)
async def create_schedule(request: Request, body: ScheduleCreateRequest):
    """创建新的调度任务。"""
    schedule_store = request.app.state.store.schedule_store
    from multi_agent.scheduler.manager import ScheduleManager

    mgr = ScheduleManager(request.app.state.store)
    schedule = await mgr.create_schedule(
        name=body.name,
        description=body.description,
        cron_expression=body.cron_expression,
        timezone=body.timezone or "Asia/Shanghai",
        tenant_id=body.tenant_id or "default",
        created_by=body.created_by or "api",
    )
    return ScheduleResponse(
        schedule_id=schedule.schedule_id,
        name=schedule.name,
        description=schedule.description,
        cron_expression=schedule.cron_expression,
        timezone=schedule.timezone,
        status=schedule.status.value,
        tenant_id=schedule.tenant_id,
        last_run_at=schedule.last_run_at.isoformat() if schedule.last_run_at else None,
        next_run_at=schedule.next_run_at.isoformat() if schedule.next_run_at else None,
        run_count=schedule.run_count,
        last_error=schedule.last_error,
    )


@router.get(
    "/schedules/{schedule_id}",
    response_model=ScheduleResponse,
    summary="查询调度任务",
    description="获取单个调度任务详情。",
)
async def get_schedule(request: Request, schedule_id: str):
    """查询单个调度任务。"""
    schedule_store = request.app.state.store.schedule_store
    schedule = await schedule_store.get_schedule(schedule_id)
    if not schedule:
        raise HTTPException(status_code=404, detail="调度任务不存在")
    return ScheduleResponse(
        schedule_id=schedule.schedule_id,
        name=schedule.name,
        description=schedule.description,
        cron_expression=schedule.cron_expression,
        timezone=schedule.timezone,
        status=schedule.status.value,
        tenant_id=schedule.tenant_id,
        last_run_at=schedule.last_run_at.isoformat() if schedule.last_run_at else None,
        next_run_at=schedule.next_run_at.isoformat() if schedule.next_run_at else None,
        run_count=schedule.run_count,
        last_error=schedule.last_error,
    )


@router.post(
    "/schedules/{schedule_id}/pause",
    summary="暂停调度任务",
)
async def pause_schedule(request: Request, schedule_id: str):
    """暂停调度任务。"""
    from multi_agent.scheduler.manager import ScheduleManager

    mgr = ScheduleManager(request.app.state.store)
    schedule = await mgr.pause_schedule(schedule_id)
    if not schedule:
        raise HTTPException(status_code=404, detail="调度任务不存在")
    return {"message": f"调度任务 '{schedule_id}' 已暂停", "status": schedule.status.value}


@router.post(
    "/schedules/{schedule_id}/resume",
    summary="恢复调度任务",
)
async def resume_schedule(request: Request, schedule_id: str):
    """恢复调度任务。"""
    from multi_agent.scheduler.manager import ScheduleManager

    mgr = ScheduleManager(request.app.state.store)
    schedule = await mgr.resume_schedule(schedule_id)
    if not schedule:
        raise HTTPException(status_code=404, detail="调度任务不存在")
    return {"message": f"调度任务 '{schedule_id}' 已恢复", "status": schedule.status.value}


@router.delete(
    "/schedules/{schedule_id}",
    summary="删除调度任务",
)
async def delete_schedule(request: Request, schedule_id: str):
    """删除调度任务。"""
    from multi_agent.scheduler.manager import ScheduleManager

    mgr = ScheduleManager(request.app.state.store)
    deleted = await mgr.delete_schedule(schedule_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="调度任务不存在")
    return {"message": f"调度任务 '{schedule_id}' 已删除"}
