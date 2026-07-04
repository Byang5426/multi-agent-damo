"""多Agent系统 API 请求/响应数据模型。"""

from typing import Optional

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """聊天接口请求体。"""

    message: str = Field(..., description="用户消息/任务请求")
    tenant_id: str = Field(default="default", description="租户ID")
    user_id: str = Field(default="anonymous", description="用户ID")


class ChatResponse(BaseModel):
    """聊天接口响应体。"""

    response: str = Field(description="任务执行结果摘要")
    project_id: Optional[str] = Field(default=None, description="项目ID（仅项目型任务）")
    tasks: list[dict] = Field(default_factory=list, description="任务列表")
    trace_count: int = Field(default=0, description="Trace日志条数")
    error: Optional[str] = Field(default=None, description="错误信息")


class TaskResponse(BaseModel):
    """任务查询响应体。"""

    task_id: str = Field(description="任务ID")
    title: str = Field(description="任务标题")
    status: str = Field(description="任务状态")
    assigned_worker: Optional[str] = Field(default=None, description="分配的Worker名称")
    retry_count: int = Field(default=0, description="已重试次数")
    output_summary: Optional[str] = Field(default=None, description="执行结果摘要")
    last_error: Optional[str] = Field(default=None, description="最近一次错误")
    artifacts_count: int = Field(default=0, description="产物数量")


class ProjectResponse(BaseModel):
    """项目查询响应体。"""

    project_id: str = Field(description="项目ID")
    title: str = Field(description="项目标题")
    description: str = Field(description="项目描述")
    status: str = Field(description="项目状态")
    tasks: list[TaskResponse] = Field(default_factory=list, description="子任务列表")


class HumanInterventionRequest(BaseModel):
    """人工介入请求体。"""

    status: str = Field(
        ..., description="目标状态：'TODO'（重试）或 'DONE'（标记完成）"
    )
    resolution: str = Field(default="human_fix", description="解决方式")
    comment: str = Field(default="", description="人工备注")


class PromptUpdateRequest(BaseModel):
    """Prompt 更新请求体。"""

    content: str = Field(..., description="新的 Prompt 内容")
    description: str = Field(default="", description="用途说明")


class PromptResponse(BaseModel):
    """Prompt 响应体。"""

    prompt_id: str
    agent_name: str
    role: str
    version: int
    content: str
    description: str
    is_active: bool
