"""项目数据模型。"""

import enum
from datetime import datetime, timezone

from pydantic import BaseModel, Field


class ProjectStatus(str, enum.Enum):
    """项目生命周期状态。"""

    PLANNING = "PLANNING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    PAUSED = "PAUSED"


class Project(BaseModel):
    """项目模型：作为多个任务的容器。"""

    project_id: str
    title: str
    description: str
    status: ProjectStatus = ProjectStatus.PLANNING
    tenant_id: str = "default"
    created_by: str = "system"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ProjectCreate(BaseModel):
    """创建项目的请求模型。"""

    title: str
    description: str
