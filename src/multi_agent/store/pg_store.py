"""PostgreSQL persistence layer using asyncpg.

Utilises PostgreSQL-specific features:
- JSONB for acceptance_criteria, artifacts, tool_calls
- GIN indexes for JSONB columns
- Partial indexes for common query patterns
- Connection pooling via asyncpg.create_pool
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional

import asyncpg

from multi_agent.models.message import TraceEntry
from multi_agent.models.project import Project, ProjectStatus
from multi_agent.models.prompt import AgentPrompt
from multi_agent.models.task import AcceptanceCriterion, Artifact, Task, TaskStatus

logger = logging.getLogger(__name__)

# ── DDL: 建表语句 ──

INIT_SQL = """
-- 项目表
CREATE TABLE IF NOT EXISTS projects (
    project_id   TEXT PRIMARY KEY,
    title        TEXT NOT NULL,
    description  TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'PLANNING',
    tenant_id    TEXT NOT NULL DEFAULT 'default',
    created_by   TEXT NOT NULL DEFAULT 'system',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_projects_tenant
    ON projects (tenant_id);
CREATE INDEX IF NOT EXISTS idx_projects_status
    ON projects (status);

-- 任务表
CREATE TABLE IF NOT EXISTS tasks (
    task_id              TEXT PRIMARY KEY,
    project_id           TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    title                TEXT NOT NULL,
    description          TEXT NOT NULL,
    status               TEXT NOT NULL DEFAULT 'TODO',
    assigned_worker      TEXT,
    retry_count          INTEGER NOT NULL DEFAULT 0,
    max_retries          INTEGER NOT NULL DEFAULT 3,
    last_error           TEXT,
    output_summary       TEXT,
    acceptance_criteria  JSONB NOT NULL DEFAULT '[]'::jsonb,
    artifacts            JSONB NOT NULL DEFAULT '[]'::jsonb,
    tenant_id            TEXT NOT NULL DEFAULT 'default',
    created_by           TEXT NOT NULL DEFAULT 'system',
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by           TEXT NOT NULL DEFAULT 'system'
);

CREATE INDEX IF NOT EXISTS idx_tasks_project
    ON tasks (project_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status
    ON tasks (status);
CREATE INDEX IF NOT EXISTS idx_tasks_tenant
    ON tasks (tenant_id);
-- 部分索引：只索引需要人工处理的任务
CREATE INDEX IF NOT EXISTS idx_tasks_human_pending
    ON tasks (project_id) WHERE status = 'HUMAN_PENDING';
-- GIN 索引：加速 artifacts JSONB 查询
CREATE INDEX IF NOT EXISTS idx_tasks_artifacts_gin
    ON tasks USING GIN (artifacts);

-- Trace 日志表
CREATE TABLE IF NOT EXISTS trace_logs (
    span_id            TEXT PRIMARY KEY,
    trace_id           TEXT NOT NULL,
    parent_span_id     TEXT,
    request_id         TEXT,
    tenant_id          TEXT NOT NULL DEFAULT 'default',
    task_id            TEXT,
    agent_name         TEXT NOT NULL,
    tool_calls         JSONB NOT NULL DEFAULT '[]'::jsonb,
    latency_ms         INTEGER,
    prompt_tokens      INTEGER NOT NULL DEFAULT 0,
    completion_tokens  INTEGER NOT NULL DEFAULT 0,
    failure_reason     TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_trace_task
    ON trace_logs (task_id);
CREATE INDEX IF NOT EXISTS idx_trace_trace_id
    ON trace_logs (trace_id);
CREATE INDEX IF NOT EXISTS idx_trace_tenant
    ON trace_logs (tenant_id);
CREATE INDEX IF NOT EXISTS idx_trace_created
    ON trace_logs (created_at DESC);
-- GIN 索引：加速 tool_calls JSONB 查询
CREATE INDEX IF NOT EXISTS idx_trace_tool_calls_gin
    ON trace_logs USING GIN (tool_calls);

-- Agent Prompt 管理表
CREATE TABLE IF NOT EXISTS agent_prompts (
    prompt_id    TEXT NOT NULL,
    agent_name   TEXT NOT NULL,
    role         TEXT NOT NULL DEFAULT 'system',
    version      INTEGER NOT NULL DEFAULT 1,
    content      TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',
    is_active    BOOLEAN NOT NULL DEFAULT true,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (prompt_id, version)
);

CREATE INDEX IF NOT EXISTS idx_prompts_agent
    ON agent_prompts (agent_name);
CREATE INDEX IF NOT EXISTS idx_prompts_active
    ON agent_prompts (prompt_id) WHERE is_active = true;
"""


class PgStore:
    """Async PostgreSQL-based persistence layer using asyncpg."""

    def __init__(
        self,
        dsn: str,
        min_connections: int = 2,
        max_connections: int = 10,
    ):
        self.dsn = dsn
        self.min_connections = min_connections
        self.max_connections = max_connections
        self._pool: Optional[asyncpg.Pool] = None

    async def initialize(self):
        """创建连接池并初始化表结构。"""
        self._pool = await asyncpg.create_pool(
            dsn=self.dsn,
            min_size=self.min_connections,
            max_size=self.max_connections,
        )
        await self._create_tables()
        logger.info("PostgreSQL connection pool created (%d-%d)", self.min_connections, self.max_connections)

    async def close(self):
        """关闭连接池。"""
        if self._pool:
            await self._pool.close()
            self._pool = None

    async def _create_tables(self):
        """执行 DDL 建表。"""
        async with self._pool.acquire() as conn:
            await conn.execute(INIT_SQL)

    # ── Project operations ──

    async def create_project(self, project: Project) -> Project:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO projects (project_id, title, description, status, tenant_id,
                                      created_by, created_at, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                """,
                project.project_id,
                project.title,
                project.description,
                project.status.value,
                project.tenant_id,
                project.created_by,
                project.created_at,
                project.updated_at,
            )
        return project

    async def get_project(self, project_id: str) -> Optional[Project]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM projects WHERE project_id = $1", project_id
            )
        if not row:
            return None
        return self._row_to_project(row)

    async def update_project_status(self, project_id: str, status: ProjectStatus) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE projects SET status = $1, updated_at = $2 WHERE project_id = $3",
                status.value,
                datetime.now(timezone.utc),
                project_id,
            )

    # ── Task operations ──

    async def create_task(self, task: Task) -> Task:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO tasks (task_id, project_id, title, description, status,
                    assigned_worker, retry_count, max_retries, last_error, output_summary,
                    acceptance_criteria, artifacts, tenant_id, created_by, created_at,
                    updated_at, updated_by)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17)
                """,
                task.task_id,
                task.project_id,
                task.title,
                task.description,
                task.status.value,
                task.assigned_worker,
                task.retry_count,
                task.max_retries,
                task.last_error,
                task.output_summary,
                json.dumps([c.model_dump() for c in task.acceptance_criteria]),
                json.dumps([a.model_dump() for a in task.artifacts]),
                task.tenant_id,
                task.created_by,
                task.created_at,
                task.updated_at,
                task.updated_by,
            )
        return task

    async def get_task(self, task_id: str) -> Optional[Task]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM tasks WHERE task_id = $1", task_id
            )
        if not row:
            return None
        return self._row_to_task(row)

    async def get_tasks_by_project(self, project_id: str) -> list[Task]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM tasks WHERE project_id = $1 ORDER BY created_at",
                project_id,
            )
        return [self._row_to_task(r) for r in rows]

    async def update_task(self, task: Task) -> Task:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE tasks SET
                    status = $1, assigned_worker = $2, retry_count = $3,
                    max_retries = $4, last_error = $5, output_summary = $6,
                    acceptance_criteria = $7, artifacts = $8,
                    updated_at = $9, updated_by = $10
                WHERE task_id = $11
                """,
                task.status.value,
                task.assigned_worker,
                task.retry_count,
                task.max_retries,
                task.last_error,
                task.output_summary,
                json.dumps([c.model_dump() for c in task.acceptance_criteria]),
                json.dumps([a.model_dump() for a in task.artifacts]),
                datetime.now(timezone.utc),
                task.updated_by,
                task.task_id,
            )
        return task

    async def add_artifact(self, task_id: str, artifact: Artifact) -> None:
        task = await self.get_task(task_id)
        if task:
            task.artifacts.append(artifact)
            await self.update_task(task)

    # ── Trace operations ──

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

    # ── Prompt operations ──

    async def get_active_prompt(self, prompt_id: str) -> Optional[AgentPrompt]:
        """获取某个 prompt_id 的当前生效版本。"""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM agent_prompts WHERE prompt_id = $1 AND is_active = true",
                prompt_id,
            )
        if not row:
            return None
        return self._row_to_prompt(row)

    async def get_prompt_by_agent(
        self, agent_name: str, role: str = "system"
    ) -> Optional[AgentPrompt]:
        """获取某个 Agent 的当前生效 Prompt。"""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM agent_prompts
                WHERE agent_name = $1 AND role = $2 AND is_active = true
                ORDER BY version DESC LIMIT 1
                """,
                agent_name,
                role,
            )
        if not row:
            return None
        return self._row_to_prompt(row)

    async def list_prompts(self, agent_name: Optional[str] = None) -> list[AgentPrompt]:
        """列出所有 Prompt（可按 agent 过滤）。"""
        async with self._pool.acquire() as conn:
            if agent_name:
                rows = await conn.fetch(
                    """
                    SELECT * FROM agent_prompts
                    WHERE agent_name = $1
                    ORDER BY agent_name, prompt_id, version DESC
                    """,
                    agent_name,
                )
            else:
                rows = await conn.fetch(
                    "SELECT * FROM agent_prompts ORDER BY agent_name, prompt_id, version DESC"
                )
        return [self._row_to_prompt(r) for r in rows]

    async def create_prompt(self, prompt: AgentPrompt) -> AgentPrompt:
        """创建新版本的 Prompt。"""
        async with self._pool.acquire() as conn:
            # 将同 prompt_id 的旧版本设为非活跃
            await conn.execute(
                "UPDATE agent_prompts SET is_active = false WHERE prompt_id = $1",
                prompt.prompt_id,
            )
            await conn.execute(
                """
                INSERT INTO agent_prompts
                    (prompt_id, agent_name, role, version, content, description, is_active,
                     created_at, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                """,
                prompt.prompt_id,
                prompt.agent_name,
                prompt.role,
                prompt.version,
                prompt.content,
                prompt.description,
                prompt.is_active,
                prompt.created_at,
                prompt.updated_at,
            )
        return prompt

    async def update_prompt_content(
        self, prompt_id: str, content: str, description: str = ""
    ) -> Optional[AgentPrompt]:
        """更新当前生效版本的 Prompt 内容（自动创建新版本）。"""
        current = await self.get_active_prompt(prompt_id)
        if not current:
            return None
        new_prompt = AgentPrompt(
            prompt_id=current.prompt_id,
            agent_name=current.agent_name,
            role=current.role,
            version=current.version + 1,
            content=content,
            description=description or current.description,
            is_active=True,
        )
        return await self.create_prompt(new_prompt)

    async def seed_prompts(self, defaults: dict[str, str]) -> int:
        """种子填充默认 Prompt，仅当 prompt_id 不存在时插入。

        Args:
            defaults: {prompt_id: content} 映射

        Returns:
            实际插入的数量
        """
        inserted = 0
        for prompt_id, content in defaults.items():
            existing = await self.get_active_prompt(prompt_id)
            if existing is None:
                # 推导 agent_name
                agent_name = prompt_id.split("_")[0] if "_" in prompt_id else prompt_id
                prompt = AgentPrompt(
                    prompt_id=prompt_id,
                    agent_name=agent_name,
                    role="system",
                    version=1,
                    content=content,
                    description=f"Default {prompt_id} prompt",
                    is_active=True,
                )
                await self.create_prompt(prompt)
                inserted += 1
                logger.info("Seeded prompt: %s", prompt_id)
        return inserted

    @staticmethod
    def _row_to_prompt(row: asyncpg.Record) -> AgentPrompt:
        return AgentPrompt(
            prompt_id=row["prompt_id"],
            agent_name=row["agent_name"],
            role=row["role"],
            version=row["version"],
            content=row["content"],
            description=row["description"],
            is_active=row["is_active"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    # ── Row mappers ──

    @staticmethod
    def _row_to_project(row: asyncpg.Record) -> Project:
        return Project(
            project_id=row["project_id"],
            title=row["title"],
            description=row["description"],
            status=ProjectStatus(row["status"]),
            tenant_id=row["tenant_id"],
            created_by=row["created_by"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _row_to_task(row: asyncpg.Record) -> Task:
        # JSONB 字段：asyncpg 自动返回 Python dict/list
        criteria_raw = row["acceptance_criteria"]
        artifacts_raw = row["artifacts"]

        if isinstance(criteria_raw, str):
            criteria_raw = json.loads(criteria_raw)
        if isinstance(artifacts_raw, str):
            artifacts_raw = json.loads(artifacts_raw)

        return Task(
            task_id=row["task_id"],
            project_id=row["project_id"],
            title=row["title"],
            description=row["description"],
            status=TaskStatus(row["status"]),
            assigned_worker=row["assigned_worker"],
            retry_count=row["retry_count"],
            max_retries=row["max_retries"],
            last_error=row["last_error"],
            output_summary=row["output_summary"],
            acceptance_criteria=[AcceptanceCriterion(**c) for c in (criteria_raw or [])],
            artifacts=[Artifact(**a) for a in (artifacts_raw or [])],
            tenant_id=row["tenant_id"],
            created_by=row["created_by"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            updated_by=row["updated_by"],
        )

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
