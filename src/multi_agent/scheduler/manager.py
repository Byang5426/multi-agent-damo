"""Schedule manager: asyncio-based polling scheduler.

设计原则：
- 使用 asyncio.Task 轮询 PostgreSQL 中的到期调度任务
- 幂等键（schedule_id + trigger_time）防止重复执行
- 调度触发时调用 run_workflow 执行实际任务
- 启动/停止与 FastAPI 生命周期绑定
"""

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional, Set

from multi_agent.config import settings
from multi_agent.scheduler.models import Schedule, ScheduleStatus
from multi_agent.scheduler.schedule_store import ScheduleStore
from multi_agent.store.pg_store import PgStore

logger = logging.getLogger(__name__)


def _compute_next_run(cron_expr: str, tz_name: str) -> Optional[datetime]:
    """根据 cron 表达式计算下次执行时间。

    使用简单解析：支持标准 5 字段 cron（分 时 日 月 周）。
    此处使用 APScheduler 的 CronTrigger 做精确计算。
    """
    try:
        from apscheduler.triggers.cron import CronTrigger
        from apscheduler.util import astimezone

        parts = cron_expr.strip().split()
        if len(parts) != 5:
            logger.warning("Invalid cron expression: %s", cron_expr)
            return None

        trigger = CronTrigger(
            minute=parts[0],
            hour=parts[1],
            day=parts[2],
            month=parts[3],
            day_of_week=parts[4],
            timezone=astimezone(tz_name),
        )
        now = datetime.now(timezone.utc)
        next_fire = trigger.get_next_fire_time(None, now)
        if next_fire is not None:
            # Ensure UTC timezone
            if next_fire.tzinfo is None:
                next_fire = next_fire.replace(tzinfo=timezone.utc)
            else:
                next_fire = next_fire.astimezone(timezone.utc)
        return next_fire
    except ImportError:
        logger.warning(
            "APScheduler not installed, cron next-run calculation unavailable"
        )
        return None
    except Exception as e:
        logger.warning("Failed to compute next run for '%s': %s", cron_expr, e)
        return None


class ScheduleManager:
    """定时调度管理器。

    管理调度任务的生命周期，轮询到期任务并触发执行。
    """

    def __init__(self, store: PgStore) -> None:
        self._store = store
        self._schedule_store: Optional[ScheduleStore] = None
        self._poll_task: Optional[asyncio.Task] = None
        self._running = False
        # 幂等键集合：防止同一 schedule_id + trigger_time 重复执行
        self._executed_keys: Set[str] = set()
        self._max_idempotency_cache = 1000

    async def start(self) -> None:
        """启动调度器。"""
        if not settings.scheduler_enabled:
            logger.info("Scheduler disabled by configuration")
            return

        if self._store._pool is None:
            logger.warning("Scheduler not started: database pool not initialized")
            return

        self._schedule_store = ScheduleStore(self._store._pool)
        self._running = True
        self._poll_task = asyncio.create_task(self._poll_loop())
        logger.info(
            "Schedule manager started (poll_interval=%ds)",
            settings.scheduler_poll_interval,
        )

    async def stop(self) -> None:
        """停止调度器。"""
        self._running = False
        if self._poll_task is not None:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None
        logger.info("Schedule manager stopped")

    async def create_schedule(
        self,
        name: str,
        description: str,
        cron_expression: str,
        timezone: str = "Asia/Shanghai",
        tenant_id: str = "default",
        created_by: str = "system",
    ) -> Schedule:
        """创建新的调度任务。"""
        if self._schedule_store is None:
            raise RuntimeError("Schedule manager not started")

        schedule_id = f"SCH-{uuid.uuid4().hex[:8]}"
        next_run = _compute_next_run(cron_expression, timezone)

        schedule = Schedule(
            schedule_id=schedule_id,
            name=name,
            description=description,
            cron_expression=cron_expression,
            timezone=timezone,
            status=ScheduleStatus.ACTIVE,
            tenant_id=tenant_id,
            created_by=created_by,
            next_run_at=next_run,
        )

        await self._schedule_store.create_schedule(schedule)
        logger.info(
            "Schedule created: %s (cron=%s, next_run=%s)",
            schedule_id,
            cron_expression,
            next_run,
        )
        return schedule

    async def pause_schedule(self, schedule_id: str) -> Optional[Schedule]:
        """暂停调度任务。"""
        if self._schedule_store is None:
            raise RuntimeError("Schedule manager not started")

        schedule = await self._schedule_store.get_schedule(schedule_id)
        if schedule is None:
            return None

        schedule.status = ScheduleStatus.PAUSED
        await self._schedule_store.update_schedule(schedule)
        return schedule

    async def resume_schedule(self, schedule_id: str) -> Optional[Schedule]:
        """恢复调度任务。"""
        if self._schedule_store is None:
            raise RuntimeError("Schedule manager not started")

        schedule = await self._schedule_store.get_schedule(schedule_id)
        if schedule is None:
            return None

        schedule.status = ScheduleStatus.ACTIVE
        schedule.next_run_at = _compute_next_run(
            schedule.cron_expression, schedule.timezone
        )
        await self._schedule_store.update_schedule(schedule)
        return schedule

    async def delete_schedule(self, schedule_id: str) -> bool:
        """删除调度任务。"""
        if self._schedule_store is None:
            raise RuntimeError("Schedule manager not started")
        return await self._schedule_store.delete_schedule(schedule_id)

    async def list_schedules(self) -> list[Schedule]:
        """列出所有调度任务。"""
        if self._schedule_store is None:
            raise RuntimeError("Schedule manager not started")
        return await self._schedule_store.list_schedules()

    async def get_schedule(self, schedule_id: str) -> Optional[Schedule]:
        """查询单个调度任务。"""
        if self._schedule_store is None:
            raise RuntimeError("Schedule manager not started")
        return await self._schedule_store.get_schedule(schedule_id)

    async def _poll_loop(self) -> None:
        """轮询主循环：检查到期任务并触发执行。"""
        while self._running:
            try:
                await self._check_and_execute_due_schedules()
            except Exception as e:
                logger.error("Scheduler poll loop error: %s", e, exc_info=True)

            try:
                await asyncio.sleep(settings.scheduler_poll_interval)
            except asyncio.CancelledError:
                break

    async def _check_and_execute_due_schedules(self) -> None:
        """检查并执行到期的调度任务。"""
        if self._schedule_store is None:
            return

        now = datetime.now(timezone.utc)
        due_schedules = await self._schedule_store.get_due_schedules(now)

        for schedule in due_schedules:
            # 幂等键检查
            idem_key = f"{schedule.schedule_id}:{now.isoformat(timespec='minutes')}"
            if idem_key in self._executed_keys:
                logger.debug("Skipping already executed schedule: %s", idem_key)
                continue

            # 标记已执行（幂等）
            self._executed_keys.add(idem_key)
            # 防止缓存无限增长
            if len(self._executed_keys) > self._max_idempotency_cache:
                # 保留最近的一半
                to_remove = list(self._executed_keys)[: len(self._executed_keys) // 2]
                for k in to_remove:
                    self._executed_keys.discard(k)

            # 异步触发执行（不阻塞轮询）
            asyncio.create_task(self._execute_schedule(schedule, now))

    async def _execute_schedule(self, schedule: Schedule, trigger_time: datetime) -> None:
        """执行单个调度任务。"""
        logger.info(
            "Executing schedule '%s' (trigger_time=%s): %s",
            schedule.schedule_id,
            trigger_time,
            schedule.description[:100],
        )

        try:
            # 导入 run_workflow（延迟导入避免循环依赖）
            from multi_agent.graph.workflow import run_workflow

            result = await run_workflow(
                user_input=schedule.description,
                tenant_id=schedule.tenant_id,
                user_id=f"scheduler:{schedule.schedule_id}",
                request_id=f"sched-{uuid.uuid4().hex[:8]}",
                store=self._store,
            )

            # 检查执行结果
            error = result.get("error")
            if error:
                await self._schedule_store.mark_run_failure(
                    schedule.schedule_id, trigger_time, str(error)
                )
                logger.warning(
                    "Schedule '%s' execution failed: %s",
                    schedule.schedule_id,
                    error,
                )
            else:
                await self._schedule_store.mark_run_success(
                    schedule.schedule_id, trigger_time
                )
                logger.info(
                    "Schedule '%s' executed successfully", schedule.schedule_id
                )

            # 更新下次执行时间
            schedule.next_run_at = _compute_next_run(
                schedule.cron_expression, schedule.timezone
            )
            await self._schedule_store.update_schedule(schedule)

        except Exception as e:
            logger.error(
                "Schedule '%s' execution error: %s",
                schedule.schedule_id,
                e,
                exc_info=True,
            )
            try:
                await self._schedule_store.mark_run_failure(
                    schedule.schedule_id, trigger_time, str(e)
                )
            except Exception as db_err:
                logger.error("Failed to mark schedule failure: %s", db_err)
