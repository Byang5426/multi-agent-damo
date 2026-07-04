"""Task model with state machine."""

import enum
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field


class TaskStatus(str, enum.Enum):
    """Task state machine states."""

    TODO = "TODO"
    DOING = "DOING"
    BLOCKED = "BLOCKED"
    REVIEW = "REVIEW"
    DONE = "DONE"
    FAILED = "FAILED"
    HUMAN_PENDING = "HUMAN_PENDING"


class AcceptanceCriterion(BaseModel):
    """Structured acceptance criterion for a task."""

    type: str  # output_exists, output_contains, no_error, human_confirm
    description: str
    key: Optional[str] = None  # For output_contains type


class Artifact(BaseModel):
    """Output artifact produced by a Worker."""

    artifact_type: str  # code, doc, test_report, analysis
    content: str  # The actual content
    url: Optional[str] = None  # Optional external storage reference


class Task(BaseModel):
    """Core task model with state machine support."""

    task_id: str
    project_id: str
    title: str
    description: str
    status: TaskStatus = TaskStatus.TODO
    assigned_worker: Optional[str] = None  # Worker agent name
    retry_count: int = 0
    max_retries: int = 3
    last_error: Optional[str] = None
    output_summary: Optional[str] = None
    acceptance_criteria: list[AcceptanceCriterion] = Field(default_factory=list)
    artifacts: list[Artifact] = Field(default_factory=list)
    tenant_id: str = "default"
    created_by: str = "system"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_by: str = "system"

    # Valid state transitions
    VALID_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
        TaskStatus.TODO: {TaskStatus.DOING, TaskStatus.FAILED},
        TaskStatus.DOING: {
            TaskStatus.REVIEW,
            TaskStatus.BLOCKED,
            TaskStatus.FAILED,
            TaskStatus.TODO,  # retry
        },
        TaskStatus.BLOCKED: {TaskStatus.TODO, TaskStatus.FAILED, TaskStatus.HUMAN_PENDING},
        TaskStatus.REVIEW: {
            TaskStatus.DONE,
            TaskStatus.TODO,  # rejected, retry
            TaskStatus.FAILED,
        },
        TaskStatus.DONE: set(),  # terminal
        TaskStatus.FAILED: {TaskStatus.TODO, TaskStatus.HUMAN_PENDING},
        TaskStatus.HUMAN_PENDING: {TaskStatus.TODO, TaskStatus.DONE},
    }

    def can_transition(self, new_status: TaskStatus) -> bool:
        """Check if a state transition is valid."""
        return new_status in self.VALID_TRANSITIONS.get(self.status, set())

    def transition(self, new_status: TaskStatus, updated_by: str = "system") -> None:
        """Perform a state transition. Raises ValueError if invalid."""
        if not self.can_transition(new_status):
            raise ValueError(
                f"Invalid transition: {self.status.value} -> {new_status.value}"
            )
        self.status = new_status
        self.updated_at = datetime.now(timezone.utc)
        self.updated_by = updated_by


class TaskCreate(BaseModel):
    """Request model for creating a task."""

    title: str
    description: str
    assigned_worker: Optional[str] = None
    max_retries: int = 3
    acceptance_criteria: list[AcceptanceCriterion] = Field(default_factory=list)


class TaskUpdate(BaseModel):
    """Request model for updating a task (e.g., human intervention)."""

    status: Optional[TaskStatus] = None
    output_summary: Optional[str] = None
    resolution: Optional[str] = None
    comment: Optional[str] = None
