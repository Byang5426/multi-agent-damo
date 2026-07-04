"""Base worker agent with common interface and output schema."""

import logging
import time
import uuid
from abc import ABC, abstractmethod
from typing import Any, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from multi_agent.config import settings
from multi_agent.models.message import TraceEntry
from multi_agent.models.task import Artifact
from multi_agent.prompt_loader import load_prompt

logger = logging.getLogger(__name__)


class WorkerOutput(BaseModel):
    """Standardized output from any Worker agent."""

    status: str = Field(description="success or error")
    summary: str = Field(description="Brief summary of the result")
    artifacts: list[Artifact] = Field(default_factory=list)
    error: Optional[str] = Field(default=None, description="Error message if failed")


class BaseWorker(ABC):
    """Base class for all Worker agents."""

    name: str = "base_worker"
    system_prompt: str = "You are a helpful assistant."
    prompt_id: str = ""  # DB prompt identifier; if set, loaded at runtime

    def __init__(self):
        self._llm = ChatOpenAI(
            model=settings.openai_api_model,
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url or None,
            temperature=0.3,
        )

    async def execute(
        self,
        task_id: str,
        description: str,
        context: Optional[dict[str, Any]] = None,
        rejection_reason: Optional[str] = None,
    ) -> tuple[WorkerOutput, TraceEntry]:
        """Execute a task and return structured output with trace.

        Args:
            task_id: The task identifier.
            description: Task description from PM.
            context: Additional context (previous artifacts, etc.).
            rejection_reason: If this is a retry, the reason for rejection.
        """
        trace_id = str(uuid.uuid4())
        span_id = str(uuid.uuid4())
        start = time.time()

        try:
            # Build the prompt with optional rejection context
            user_msg = self._build_user_message(description, context, rejection_reason)
            # Load prompt from DB if prompt_id is set, else use class attribute
            sys_prompt = await load_prompt(self.prompt_id, self.system_prompt) if self.prompt_id else self.system_prompt
            messages = [
                SystemMessage(content=sys_prompt),
                HumanMessage(content=user_msg),
            ]

            response = await self._llm.ainvoke(messages)
            elapsed = int((time.time() - start) * 1000)

            # Parse the response into structured output
            output = self._parse_output(response.content)

            trace = TraceEntry(
                trace_id=trace_id,
                span_id=span_id,
                task_id=task_id,
                agent_name=self.name,
                latency_ms=elapsed,
                prompt_tokens=response.usage_metadata.get("input_tokens", 0)
                if hasattr(response, "usage_metadata") and response.usage_metadata
                else 0,
                completion_tokens=response.usage_metadata.get("output_tokens", 0)
                if hasattr(response, "usage_metadata") and response.usage_metadata
                else 0,
            )
            return output, trace

        except Exception as e:
            elapsed = int((time.time() - start) * 1000)
            logger.error("Worker %s failed on task %s: %s", self.name, task_id, e)
            output = WorkerOutput(
                status="error",
                summary=f"Worker execution failed: {e}",
                error=str(e),
            )
            trace = TraceEntry(
                trace_id=trace_id,
                span_id=span_id,
                task_id=task_id,
                agent_name=self.name,
                latency_ms=elapsed,
                failure_reason=str(e),
            )
            return output, trace

    def _build_user_message(
        self,
        description: str,
        context: Optional[dict[str, Any]],
        rejection_reason: Optional[str],
    ) -> str:
        parts = [f"## 任务\n{description}"]

        if context:
            ctx_text = context.get("previous_artifacts", "")
            if ctx_text:
                parts.append(f"## 前序Worker的产物（作为上下文参考）\n{ctx_text}")

        if rejection_reason:
            parts.append(
                f"## 上次尝试被拒绝\n"
                f"拒绝原因：{rejection_reason}\n"
                f"请针对以上问题改进你的输出。"
            )

        parts.append(
            "## 输出格式\n"
            "请以JSON格式回复：\n"
            '{"status": "success|error", "summary": "一句话摘要", "artifacts": '
            '[{"artifact_type": "...", "content": "..."}], "error": null}'
        )
        return "\n\n".join(parts)

    @abstractmethod
    def _parse_output(self, raw_content: str) -> WorkerOutput:
        """Parse LLM response into structured WorkerOutput."""
        ...
