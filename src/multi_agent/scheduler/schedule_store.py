"""Schedule 领域数据访问层。"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional

import asyncpg

from multi_agent.scheduler.models import Schedule, ScheduleStatus

logger = logging.getLogger(__name__)


class ScheduleStore:
    """调度任务的 CRUD 操作（基于 asyncpg）。"""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def create_schedule(self, schedule: Schedule) -> Schedule:
        """创建调度任务。"""
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO schedules (schedule_id, name, description, cron_expression,
                    timezone, status, tenant_id, created_by, next_run_at,
                    created_at, updated_at)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
                """,
                schedule.schedule_id,
                schedule.name,
                schedule.description,
                schedule.cron_expression,
                schedule.timezone,
                schedule.status.value,
                schedule.tenant_id,
                schedule.created_by,
                schedule.next_run_at,
                schedule.created_at,
                schedule.updated_at,
            )
        return schedule

    async def get_schedule(self, schedule_id: str) -> Optional[Schedule]:
        """查询单个调度任务。"""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM schedules WHERE schedule_id = $1",
                schedule_id,
            )
        if row is None:
            return None
        return self._row_to_schedule(row)

    async def list_schedules(
        self, status: Optional[ScheduleStatus] = None
    ) -> list[Schedule]:
        """列出调度任务。"""
        async with self._pool.acquire() as conn:
            if status:
                rows = await conn.fetch(
                    "SELECT * FROM schedules WHERE status = $1 ORDER BY created_at DESC",
                    status.value,
                )
            else:
                rows = await conn.fetch(
                    "SELECT * FROM schedules ORDER BY created_at DESC"
                )
        return [self._row_to_schedule(r) for r in rows]

    async def update_schedule(self, schedule: Schedule) -> None:
        """更新调度任务。"""
        schedule.updated_at = datetime.now(timezone.utc)
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE schedules SET
                    name=$2, description=$3, cron_expression=$4, timezone=$5,
                    status=$6, last_run_at=$7, next_run_at=$8,
                    run_count=$9, last_error=$10, updated_at=$11
                WHERE schedule_id=$1
                """,
                schedule.schedule_id,
                schedule.name,
                schedule.description,
                schedule.cron_expression,
                schedule.timezone,
                schedule.status.value,
                schedule.last_run_at,
                schedule.next_run_at,
                schedule.run_count,
                schedule.last_error,
                schedule.updated_at,
            )

    async def delete_schedule(self, schedule_id: str) -> bool:
        """删除调度任务。"""
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM schedules WHERE schedule_id = $1",
                schedule_id,
            )
        return result != "DELETE 0"

    async def get_due_schedules(self, now: datetime) -> list[Schedule]:
        """查询所有到期应执行的调度任务。"""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM schedules
                WHERE status = $1 AND next_run_at IS NOT NULL AND next_run_at <= $2
                ORDER BY next_run_at
                """,
                ScheduleStatus.ACTIVE.value,
                now,
            )
        return [self._row_to_schedule(r) for r in rows]

    async def mark_run_success(
        self, schedule_id: str, run_time: datetime
    ) -> None:
        """标记调度任务执行成功。"""
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE schedules SET
                    last_run_at=$2, run_count=run_count+1,
                    last_error=NULL, updated_at=$2
                WHERE schedule_id=$1
                """,
                schedule_id,
                run_time,
            )

    async def mark_run_failure(
        self, schedule_id: str, run_time: datetime, error: str
    ) -> None:
        """标记调度任务执行失败。"""
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE schedules SET
                    last_run_at=$2, run_count=run_count+1,
                    last_error=$3, updated_at=$2
                WHERE schedule_id=$1
                """,
                schedule_id,
                run_time,
                error[:500],  # 截断过长的错误信息
            )

    @staticmethod
    def _row_to_schedule(row: asyncpg.Record) -> Schedule:
        return Schedule(
            schedule_id=row["schedule_id"],
            name=row["name"],
            description=row["description"],
            cron_expression=row["cron_expression"],
            timezone=row["timezone"],
            status=ScheduleStatus(row["status"]),
            tenant_id=row["tenant_id"],
            created_by=row["created_by"],
            last_run_at=row["last_run_at"],
            next_run_at=row["next_run_at"],
            run_count=row["run_count"],
            last_error=row["last_error"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
