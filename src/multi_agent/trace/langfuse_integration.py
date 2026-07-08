"""Langfuse Trace 集成模块。

设计原则：
- PostgreSQL trace_logs 表始终作为本地备份保留
- Langfuse 作为可选的可视化追踪后端
- Langfuse 上报失败绝不阻塞主工作流（fire-and-forget）
- 未配置 Langfuse 时，模块静默降级为空操作
"""

import asyncio
import logging
from typing import Optional

from multi_agent.config import settings
from multi_agent.models.message import TraceEntry

logger = logging.getLogger(__name__)


class LangfuseReporter:
    """Langfuse Trace 上报器。

    封装 Langfuse 客户端初始化和 Trace/Span 上报逻辑。
    当 Langfuse 未配置时，所有方法为空操作（no-op）。
    """

    def __init__(self) -> None:
        self._client = None
        self._enabled = False

        if settings.is_langfuse_enabled:
            try:
                from langfuse import Langfuse

                self._client = Langfuse(
                    public_key=settings.langfuse_public_key,
                    secret_key=settings.langfuse_secret_key,
                    host=settings.langfuse_host,
                )
                self._enabled = True
                logger.info(
                    "Langfuse reporter initialized (host=%s)", settings.langfuse_host
                )
            except Exception as e:
                logger.warning(
                    "Langfuse initialization failed, traces will only be stored "
                    "in PostgreSQL: %s",
                    e,
                )

    @property
    def enabled(self) -> bool:
        """Langfuse 是否已启用。"""
        return self._enabled

    def report_trace(self, entry: TraceEntry) -> None:
        """将 TraceEntry 上报到 Langfuse（同步方法，需在 executor 中调用）。

        Args:
            entry: 要上报的 Trace 条目。
        """
        if not self._enabled or self._client is None:
            return

        try:
            trace = self._client.trace(
                id=entry.trace_id,
                name=f"agent-{entry.agent_name}",
                metadata={
                    "span_id": entry.span_id,
                    "parent_span_id": entry.parent_span_id or "",
                    "request_id": entry.request_id or "",
                    "tenant_id": entry.tenant_id,
                    "task_id": entry.task_id or "",
                },
            )

            span_attrs: dict = {
                "agent_name": entry.agent_name,
            }
            if entry.latency_ms is not None:
                span_attrs["latency_ms"] = entry.latency_ms
            if entry.prompt_tokens:
                span_attrs["prompt_tokens"] = entry.prompt_tokens
            if entry.completion_tokens:
                span_attrs["completion_tokens"] = entry.completion_tokens
            if entry.failure_reason:
                span_attrs["failure_reason"] = entry.failure_reason
            if entry.tool_calls:
                span_attrs["tool_calls_count"] = len(entry.tool_calls)

            trace.span(
                id=entry.span_id,
                name=entry.agent_name,
                **span_attrs,
            )

            self._client.flush()
            logger.debug(
                "Langfuse trace reported: trace_id=%s, span_id=%s",
                entry.trace_id,
                entry.span_id,
            )
        except Exception as e:
            logger.warning("Langfuse trace report failed: %s", e)

    def shutdown(self) -> None:
        """关闭 Langfuse 客户端，flush 剩余数据。"""
        if self._client is not None:
            try:
                self._client.flush()
                logger.info("Langfuse client shut down")
            except Exception as e:
                logger.warning("Langfuse shutdown error: %s", e)


# ── 全局单例 ──

_reporter: Optional[LangfuseReporter] = None


def get_langfuse_reporter() -> LangfuseReporter:
    """获取全局 LangfuseReporter 单例（延迟初始化）。"""
    global _reporter
    if _reporter is None:
        _reporter = LangfuseReporter()
    return _reporter


async def report_trace_to_langfuse(entry: TraceEntry) -> None:
    """异步上报 Trace 到 Langfuse（fire-and-forget）。

    此函数始终可安全调用。若 Langfuse 未启用，立即返回。
    上报失败不会抛出异常，仅记录 warning 日志。

    Args:
        entry: 要上报的 Trace 条目。
    """
    reporter = get_langfuse_reporter()
    if not reporter.enabled:
        return

    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, reporter.report_trace, entry)
    except Exception as e:
        logger.warning("Async Langfuse report failed: %s", e)
