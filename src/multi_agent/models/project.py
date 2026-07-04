"""Project model."""

import enum
from datetime import datetime, timezone

from pydantic import BaseModel, Field


class ProjectStatus(str, enum.Enum):
    """Project lifecycle states."""

    PLANNING = "PLANNING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    PAUSED = "PAUSED"


class Project(BaseModel):
    """Project model - a container for multiple tasks."""

    project_id: str
    title: str
    description: str
    status: ProjectStatus = ProjectStatus.PLANNING
    tenant_id: str = "default"
    created_by: str = "system"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ProjectCreate(BaseModel):
    """Request model for creating a project."""

    title: str
    description: str
