"""消息与追踪模型：Agent 间通信和链路追踪的数据结构。"""

import enum
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field


class MessageRole(str, enum.Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class Message(BaseModel):
    """Agent 对话中的单条消息。"""

    role: MessageRole
    content: str
    name: Optional[str] = None  # 产生此消息的 Agent 名称
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TraceEntry(BaseModel):
    """单条追踪日志记录，用于可观测性。"""

    trace_id: str
    span_id: str
    parent_span_id: Optional[str] = None
    request_id: Optional[str] = None
    tenant_id: str = "default"
    task_id: Optional[str] = None
    agent_name: str
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    latency_ms: Optional[int] = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    failure_reason: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
