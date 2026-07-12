"""项目经理（PM Agent）：负责任务拆解、验收评审和失败处理。"""

import json
import logging
import time
import uuid

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from multi_agent.config import settings
from multi_agent.defaults.prompts import (
    PM_DECOMPOSE_PROMPT,
    PM_FAILURE_PROMPT,
    PM_REVIEW_PROMPT,
)
from multi_agent.models.message import TraceEntry
from multi_agent.models.task import AcceptanceCriterion, Task
from multi_agent.prompt_loader import load_prompt

logger = logging.getLogger(__name__)


class DecomposedTask(BaseModel):
    """ PM 拆解产出的单个任务模型。"""

    title: str
    description: str
    assigned_worker: str = Field(description="analyzer, coder, or tester")
    acceptance_criteria: list[AcceptanceCriterion] = Field(default_factory=list)


class DecompositionResult(BaseModel):
    """PM 任务拆解的完整结果。"""

    project_title: str
    tasks: list[DecomposedTask]


class ReviewDecision(BaseModel):
    """PM 对 Worker 输出的验收决策。"""

    approved: bool
    reason: str
    unmet_criteria: list[str] = Field(default_factory=list)


class FailureDecision(BaseModel):
    """PM 对任务失败的处理决策。"""

    action: str = Field(description="retry, escalate_to_human, or abort")
    reason: str


class PMAgent:
    """项目经理代理：负责任务拆解、验收评审和失败处理。"""

    def __init__(self):
        self._llm = ChatOpenAI(
            model=settings.openai_api_model,
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url or None,
            temperature=0.2,
        )

    async def decompose(
        self, project_description: str, project_id: str
    ) -> tuple[list[Task], TraceEntry]:
        """将项目拆解为多个子任务。

        Returns:
            (任务列表, 追踪记录) 元组。
        """
        trace_id = str(uuid.uuid4())
        span_id = str(uuid.uuid4())
        start = time.time()

        try:
            decompose_prompt = await load_prompt("pm_decompose", PM_DECOMPOSE_PROMPT)
            response = await self._llm.ainvoke(
                [
                    SystemMessage(content=decompose_prompt),
                    HumanMessage(content=f"请拆解以下项目：\n\n{project_description}"),
                ],
                response_format={"type": "json_object"},
            )

            elapsed = int((time.time() - start) * 1000)
            data = DecompositionResult.model_validate_json(response.content)

            tasks = []
            for i, dt in enumerate(data.tasks):
                task = Task(
                    task_id=f"{project_id}-T{i + 1:03d}",
                    project_id=project_id,
                    title=dt.title,
                    description=dt.description,
                    assigned_worker=dt.assigned_worker,
                    acceptance_criteria=dt.acceptance_criteria,
                )
                tasks.append(task)

            trace = TraceEntry(
                trace_id=trace_id,
                span_id=span_id,
                task_id=project_id,
                agent_name="pm",
                latency_ms=elapsed,
                prompt_tokens=response.usage_metadata.get("input_tokens", 0)
                if hasattr(response, "usage_metadata") and response.usage_metadata
                else 0,
                completion_tokens=response.usage_metadata.get("output_tokens", 0)
                if hasattr(response, "usage_metadata") and response.usage_metadata
                else 0,
            )
            return tasks, trace

        except Exception as e:
            elapsed = int((time.time() - start) * 1000)
            logger.error("PM decomposition failed: %s", e)
            trace = TraceEntry(
                trace_id=trace_id,
                span_id=span_id,
                task_id=project_id,
                agent_name="pm",
                latency_ms=elapsed,
                failure_reason=str(e),
            )
            # 拆解失败时返回空任务列表，由调用方处理
            return [], trace

    async def review(
        self,
        task: Task,
        worker_summary: str,
        artifact_contents: list[str],
    ) -> tuple[ReviewDecision, TraceEntry]:
        """根据验收标准审查 Worker 的输出。

        Returns:
            (验收决策, 追踪记录) 元组。
        """
        trace_id = str(uuid.uuid4())
        span_id = str(uuid.uuid4())
        start = time.time()

        criteria_text = json.dumps(
            [c.model_dump() for c in task.acceptance_criteria], indent=2
        )
        artifacts_text = "\n---\n".join(
            artifact_contents[:3] if artifact_contents else ["(no artifacts)"]
        )

        review_input = (
            f"## Task: {task.title}\n\n"
            f"## Acceptance Criteria\n{criteria_text}\n\n"
            f"## Worker Summary\n{worker_summary}\n\n"
            f"## Artifacts (excerpt)\n{artifacts_text[:2000]}"
        )

        try:
            review_prompt = await load_prompt("pm_review", PM_REVIEW_PROMPT)
            response = await self._llm.ainvoke(
                [
                    SystemMessage(content=review_prompt),
                    HumanMessage(content=review_input),
                ],
                response_format={"type": "json_object"},
            )

            elapsed = int((time.time() - start) * 1000)
            decision = ReviewDecision.model_validate_json(response.content)

            trace = TraceEntry(
                trace_id=trace_id,
                span_id=span_id,
                task_id=task.task_id,
                agent_name="pm",
                latency_ms=elapsed,
            )
            return decision, trace

        except Exception as e:
            elapsed = int((time.time() - start) * 1000)
            logger.error("PM review failed: %s", e)
            # 审查过程出错时默认拒绝
            decision = ReviewDecision(
                approved=False,
                reason=f"Review process error: {e}",
                unmet_criteria=["Unable to evaluate"],
            )
            trace = TraceEntry(
                trace_id=trace_id,
                span_id=span_id,
                task_id=task.task_id,
                agent_name="pm",
                latency_ms=elapsed,
                failure_reason=str(e),
            )
            return decision, trace

    async def handle_failure(
        self,
        task: Task,
        error: str,
    ) -> tuple[FailureDecision, TraceEntry]:
        """决定如何处理任务失败。

        Returns:
            (失败处理决策, 追踪记录) 元组。
        """
        trace_id = str(uuid.uuid4())
        span_id = str(uuid.uuid4())
        start = time.time()

        failure_context = (
            f"## Task: {task.title}\n"
            f"## Retry count: {task.retry_count} / {task.max_retries}\n"
            f"## Error: {error}\n"
            f"## Last error: {task.last_error or 'None'}"
        )

        try:
            failure_prompt = await load_prompt("pm_failure", PM_FAILURE_PROMPT)
            response = await self._llm.ainvoke(
                [
                    SystemMessage(content=failure_prompt),
                    HumanMessage(content=failure_context),
                ],
                response_format={"type": "json_object"},
            )

            elapsed = int((time.time() - start) * 1000)
            decision = FailureDecision.model_validate_json(response.content)

            trace = TraceEntry(
                trace_id=trace_id,
                span_id=span_id,
                task_id=task.task_id,
                agent_name="pm",
                latency_ms=elapsed,
            )
            return decision, trace

        except Exception as e:
            elapsed = int((time.time() - start) * 1000)
            logger.error("PM failure handling failed: %s", e)
            # PM 评估失败时默认转交人工
            decision = FailureDecision(
                action="escalate_to_human",
                reason=f"PM could not evaluate failure: {e}",
            )
            trace = TraceEntry(
                trace_id=trace_id,
                span_id=span_id,
                task_id=task.task_id,
                agent_name="pm",
                latency_ms=elapsed,
                failure_reason=str(e),
            )
            return decision, trace
