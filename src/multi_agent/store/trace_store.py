"""Trace 领域数据访问层。"""

import json
import logging

import asyncpg

from multi_agent.models.message import TraceEntry

logger = logging.getLogger(__name__)


class TraceStore:
    """Trace 日志的 CRUD 操作。"""

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def save_trace(self, entry: TraceEntry) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO trace_logs (span_id, trace_id, parent_span_id, request_id,
                    tenant_id, task_id, agent_name, tool_calls, latency_ms,
                    prompt_tokens, completion_tokens, failure_reason, created_at)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
                """,
                entry.span_id,
                entry.trace_id,
                entry.parent_span_id,
                entry.request_id,
                entry.tenant_id,
                entry.task_id,
                entry.agent_name,
                json.dumps(entry.tool_calls),
                entry.latency_ms,
                entry.prompt_tokens,
                entry.completion_tokens,
                entry.failure_reason,
                entry.created_at,
            )

    async def get_traces_by_project(self, project_id: str) -> list[TraceEntry]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM trace_logs
                WHERE task_id IN (SELECT task_id FROM tasks WHERE project_id = $1)
                ORDER BY created_at
                """,
                project_id,
            )
        return [self._row_to_trace(r) for r in rows]

    @staticmethod
    def _row_to_trace(row: asyncpg.Record) -> TraceEntry:
        tool_calls_raw = row["tool_calls"]
        if isinstance(tool_calls_raw, str):
            tool_calls_raw = json.loads(tool_calls_raw)

        return TraceEntry(
            span_id=row["span_id"],
            trace_id=row["trace_id"],
            parent_span_id=row["parent_span_id"],
            request_id=row["request_id"],
            tenant_id=row["tenant_id"],
            task_id=row["task_id"],
            agent_name=row["agent_name"],
            tool_calls=tool_calls_raw or [],
            latency_ms=row["latency_ms"],
            prompt_tokens=row["prompt_tokens"],
            completion_tokens=row["completion_tokens"],
            failure_reason=row["failure_reason"],
            created_at=row["created_at"],
        )
