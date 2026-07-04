"""Message and trace models for agent communication and tracing."""

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
    """A message in the agent conversation."""

    role: MessageRole
    content: str
    name: Optional[str] = None  # Agent name that produced this message
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TraceEntry(BaseModel):
    """A single trace log entry for observability."""

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
