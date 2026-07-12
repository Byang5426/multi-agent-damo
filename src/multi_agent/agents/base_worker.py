"""基础 Worker 代理：定义通用接口、LLM 调用和输出规范。"""

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
    """任何 Worker 代理的结构化输出模型。"""

    status: str = Field(description="执行状态: success 或 error")
    summary: str = Field(description="结果的一句话摘要")
    artifacts: list[Artifact] = Field(default_factory=list)
    error: Optional[str] = Field(default=None, description="失败时的错误信息")


class BaseWorker(ABC):
    """所有 Worker 代理的基类。"""

    name: str = "base_worker"
    system_prompt: str = "You are a helpful assistant."
    prompt_id: str = ""  # 数据库 Prompt 标识符，设置后在运行时从 DB 加载

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
        """执行任务，返回结构化输出和追踪记录。

        Args:
            task_id: 任务唯一标识。
            description: PM 分配的任务描述。
            context: 额外上下文（前序 Worker 的产物等）。
            rejection_reason: 如果是重试，上次被拒绝的原因。
        """
        trace_id = str(uuid.uuid4())
        span_id = str(uuid.uuid4())
        start = time.time()

        try:
            # 组装用户消息（包含任务描述、上下文和拒绝原因）
            user_msg = self._build_user_message(description, context, rejection_reason)
            # 如果设置了 prompt_id 则从数据库加载，否则使用类属性默认值
            sys_prompt = await load_prompt(self.prompt_id, self.system_prompt) if self.prompt_id else self.system_prompt
            messages = [
                SystemMessage(content=sys_prompt),
                HumanMessage(content=user_msg),
            ]

            response = await self._llm.ainvoke(messages)
            elapsed = int((time.time() - start) * 1000)

            # 将 LLM 响应解析为结构化输出
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
        """将 LLM 原始响应解析为结构化的 WorkerOutput。"""
        ...
