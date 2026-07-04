"""Task 领域数据访问层。"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional

import asyncpg

from multi_agent.models.task import AcceptanceCriterion, Artifact, Task, TaskStatus

logger = logging.getLogger(__name__)


class TaskStore:
    """Task CRUD operations."""

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

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
