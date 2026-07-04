"""Project 领域数据访问层。"""

import logging
from datetime import datetime, timezone
from typing import Optional

import asyncpg

from multi_agent.models.project import Project, ProjectStatus

logger = logging.getLogger(__name__)


class ProjectStore:
    """Project CRUD operations."""

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

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
