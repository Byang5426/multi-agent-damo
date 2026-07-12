"""数据模型包 - 导出核心类型。

TODO: 以下模型已定义但尚未被业务代码引用，后续迭代中如不需要可移除：
- Message (models/message.py): Agent 直接使用 LangChain 消息类型
- ProjectCreate (models/project.py): 未被 API 或内部代码引用
- TaskCreate / TaskUpdate (models/task.py): 未被 API 或内部代码引用
- TaskStatus.BLOCKED: 有状态转换定义但无工作流节点触达
"""

from multi_agent.models.message import Message, MessageRole, TraceEntry
from multi_agent.models.project import Project, ProjectCreate, ProjectStatus
from multi_agent.models.task import (
    AcceptanceCriterion,
    Artifact,
    Task,
    TaskCreate,
    TaskStatus,
    TaskUpdate,
)

__all__ = [
    "Task",
    "TaskStatus",
    "TaskCreate",
    "TaskUpdate",
    "AcceptanceCriterion",
    "Artifact",
    "Project",
    "ProjectStatus",
    "ProjectCreate",
    "Message",
    "MessageRole",
    "TraceEntry",
]
