"""PostgreSQL 持久化层门面（Facade）。

通过组合各领域子 Store 提供统一的数据访问接口。
"""

import logging
from typing import Optional

import asyncpg

from multi_agent.models.message import TraceEntry
from multi_agent.models.project import Project, ProjectStatus
from multi_agent.models.prompt import AgentPrompt
from multi_agent.models.task import Artifact, Task
from multi_agent.store.ddl import INIT_SQL
from multi_agent.store.project_store import ProjectStore
from multi_agent.store.prompt_store import PromptStore
from multi_agent.store.task_store import TaskStore
from multi_agent.store.trace_store import TraceStore
from multi_agent.scheduler.schedule_store import ScheduleStore

logger = logging.getLogger(__name__)


class PgStore:
    """Async PostgreSQL-based persistence layer using asyncpg.

    Facade that delegates to domain-specific sub-stores:
    - ProjectStore: Project CRUD
    - TaskStore: Task CRUD
    - TraceStore: Trace CRUD
    - PromptStore: Agent Prompt CRUD
    - ScheduleStore: Schedule CRUD
    """

    def __init__(
        self,
        dsn: str,
        min_connections: int = 2,
        max_connections: int = 10,
    ):
        self.dsn = dsn
        self.min_connections = min_connections
        self.max_connections = max_connections
        self._pool: Optional[asyncpg.Pool] = None

        # Domain sub-stores (initialized after pool creation)
        self._project_store: Optional[ProjectStore] = None
        self._task_store: Optional[TaskStore] = None
        self._trace_store: Optional[TraceStore] = None
        self._prompt_store: Optional[PromptStore] = None
        self._schedule_store: Optional[ScheduleStore] = None

    async def initialize(self):
        """创建连接池并初始化表结构。"""
        self._pool = await asyncpg.create_pool(
            dsn=self.dsn,
            min_size=self.min_connections,
            max_size=self.max_connections,
        )
        await self._create_tables()

        # Initialize domain sub-stores
        self._project_store = ProjectStore(self._pool)
        self._task_store = TaskStore(self._pool)
        self._trace_store = TraceStore(self._pool)
        self._prompt_store = PromptStore(self._pool)
        self._schedule_store = ScheduleStore(self._pool)

        logger.info("PostgreSQL connection pool created (%d-%d)", self.min_connections, self.max_connections)

    async def close(self):
        """关闭连接池。"""
        if self._pool:
            await self._pool.close()
            self._pool = None

    async def _create_tables(self):
        """执行 DDL 建表。"""
        async with self._pool.acquire() as conn:
            await conn.execute(INIT_SQL)

    # ── Project operations (delegated) ──

    async def create_project(self, project: Project) -> Project:
        return await self._project_store.create_project(project)

    async def get_project(self, project_id: str) -> Optional[Project]:
        return await self._project_store.get_project(project_id)

    async def update_project_status(self, project_id: str, status: ProjectStatus) -> None:
        await self._project_store.update_project_status(project_id, status)

    # ── Task operations (delegated) ──

    async def create_task(self, task: Task) -> Task:
        return await self._task_store.create_task(task)

    async def get_task(self, task_id: str) -> Optional[Task]:
        return await self._task_store.get_task(task_id)

    async def get_tasks_by_project(self, project_id: str) -> list[Task]:
        return await self._task_store.get_tasks_by_project(project_id)

    async def update_task(self, task: Task) -> Task:
        return await self._task_store.update_task(task)

    async def add_artifact(self, task_id: str, artifact: Artifact) -> None:
        await self._task_store.add_artifact(task_id, artifact)

    # ── Trace operations (delegated) ──

    async def save_trace(self, entry: TraceEntry) -> None:
        await self._trace_store.save_trace(entry)

    async def get_traces_by_project(self, project_id: str) -> list[TraceEntry]:
        return await self._trace_store.get_traces_by_project(project_id)

    # ── Prompt operations (delegated) ──

    async def get_active_prompt(self, prompt_id: str) -> Optional[AgentPrompt]:
        return await self._prompt_store.get_active_prompt(prompt_id)

    async def get_prompt_by_agent(
        self, agent_name: str, role: str = "system"
    ) -> Optional[AgentPrompt]:
        return await self._prompt_store.get_prompt_by_agent(agent_name, role)

    async def list_prompts(self, agent_name: Optional[str] = None) -> list[AgentPrompt]:
        return await self._prompt_store.list_prompts(agent_name)

    async def create_prompt(self, prompt: AgentPrompt) -> AgentPrompt:
        return await self._prompt_store.create_prompt(prompt)

    async def update_prompt_content(
        self, prompt_id: str, content: str, description: str = ""
    ) -> Optional[AgentPrompt]:
        return await self._prompt_store.update_prompt_content(prompt_id, content, description)

    async def seed_prompts(self, defaults: dict[str, str]) -> int:
        return await self._prompt_store.seed_prompts(defaults)

    # ── Schedule operations (delegated) ──

    @property
    def schedule_store(self) -> ScheduleStore:
        """获取 ScheduleStore 实例（供 ScheduleManager 使用）。"""
        if self._schedule_store is None:
            raise RuntimeError("Database not initialized")
        return self._schedule_store
