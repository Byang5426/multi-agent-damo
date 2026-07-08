"""定时任务数据模型。"""

import enum
from datetime import datetime, timezone

from pydantic import BaseModel, Field


class ScheduleStatus(str, enum.Enum):
    """调度任务状态。"""

    ACTIVE = "ACTIVE"          # 活跃，等待触发
    PAUSED = "PAUSED"          # 已暂停
    COMPLETED = "COMPLETED"    # 已完成（一次性任务执行完毕）
    FAILED = "FAILED"          # 调度执行失败


class Schedule(BaseModel):
    """定时调度任务模型。"""

    schedule_id: str = Field(description="调度任务唯一 ID")
    name: str = Field(description="调度任务名称")
    description: str = Field(description="任务描述（将作为 user_input 传递给工作流）")
    cron_expression: str = Field(description="Cron 表达式，如 '0 9 * * 1'")
    timezone: str = Field(default="Asia/Shanghai", description="时区")
    status: ScheduleStatus = Field(default=ScheduleStatus.ACTIVE)
    tenant_id: str = Field(default="default")
    created_by: str = Field(default="system")
    last_run_at: datetime | None = Field(default=None)
    next_run_at: datetime | None = Field(default=None)
    run_count: int = Field(default=0)
    last_error: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
