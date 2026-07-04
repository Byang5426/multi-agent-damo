"""多Agent系统 API 路由定义。"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request

from multi_agent.api.schemas import (
    ChatRequest,
    ChatResponse,
    HumanInterventionRequest,
    ProjectResponse,
    PromptUpdateRequest,
    TaskResponse,
)
from multi_agent.graph.workflow import run_workflow
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
            tasks=result.get("tasks", []),
            trace_count=len(result.get("trace_logs", [])),
            error=result.get("error"),
        )
    except Exception as e:
        logger.error("聊天接口异常: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="服务内部错误，请稍后重试")


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
