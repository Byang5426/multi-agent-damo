"""任务模型：包含状态机支持的任务数据结构。"""

import enum
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field


class TaskStatus(str, enum.Enum):
    """任务状态机的状态枚举。"""

    TODO = "TODO"
    DOING = "DOING"
    BLOCKED = "BLOCKED"
    REVIEW = "REVIEW"
    DONE = "DONE"
    FAILED = "FAILED"
    HUMAN_PENDING = "HUMAN_PENDING"


class AcceptanceCriterion(BaseModel):
    """结构化的验收标准。"""

    type: str  # 类型: output_exists, output_contains, no_error, human_confirm
    description: str
    key: Optional[str] = None  # 用于 output_contains 类型的关键词


class Artifact(BaseModel):
    """Worker 产出的产物。"""

    artifact_type: str  # 类型: code, doc, test_report, analysis
    content: str        # 产物内容
    url: Optional[str] = None  # 可选的外部存储引用


class Task(BaseModel):
    """核心任务模型，支持状态机转换。"""

    task_id: str
    project_id: str
    title: str
    description: str
    status: TaskStatus = TaskStatus.TODO
    assigned_worker: Optional[str] = None  # 分配的 Worker 代理名称
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

    # 合法的状态转换表
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
        TaskStatus.DONE: set(),  # 终态
        TaskStatus.FAILED: {TaskStatus.TODO, TaskStatus.HUMAN_PENDING},
        TaskStatus.HUMAN_PENDING: {TaskStatus.TODO, TaskStatus.DONE},
    }

    def can_transition(self, new_status: TaskStatus) -> bool:
        """检查状态转换是否合法。"""
        return new_status in self.VALID_TRANSITIONS.get(self.status, set())

    def transition(self, new_status: TaskStatus, updated_by: str = "system") -> None:
        """执行状态转换，非法转换时抛出 ValueError。"""
        if not self.can_transition(new_status):
            raise ValueError(
                f"Invalid transition: {self.status.value} -> {new_status.value}"
            )
        self.status = new_status
        self.updated_at = datetime.now(timezone.utc)
        self.updated_by = updated_by


class TaskCreate(BaseModel):
    """创建任务的请求模型。"""

    title: str
    description: str
    assigned_worker: Optional[str] = None
    max_retries: int = 3
    acceptance_criteria: list[AcceptanceCriterion] = Field(default_factory=list)


class TaskUpdate(BaseModel):
    """更新任务的请求模型（如人工介入）。"""

    status: Optional[TaskStatus] = None
    output_summary: Optional[str] = None
    resolution: Optional[str] = None
    comment: Optional[str] = None
