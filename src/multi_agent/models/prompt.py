"""Agent Prompt model for runtime prompt management."""

from datetime import datetime, timezone

from pydantic import BaseModel, Field


class AgentPrompt(BaseModel):
    """A stored prompt template for an Agent role."""

    prompt_id: str = Field(description="唯一标识，如 pm_decompose, pm_review, analyzer 等")
    agent_name: str = Field(description="所属 Agent: pm, analyzer, coder, tester, gateway")
    role: str = Field(default="system", description="消息角色: system, user, assistant")
    version: int = Field(default=1, description="版本号，每次更新自增")
    content: str = Field(description="Prompt 内容")
    description: str = Field(default="", description="用途说明")
    is_active: bool = Field(default=True, description="是否为当前生效版本")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
