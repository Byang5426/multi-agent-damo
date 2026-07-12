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
    """基于 asyncpg 的异步 PostgreSQL 持久化层。

    门面（Facade）模式，委托各领域领域子 Store 处理：
    - ProjectStore: 项目 CRUD
    - TaskStore: 任务 CRUD
    - TraceStore: 追踪日志 CRUD
    - PromptStore: Agent 提示词 CRUD
    - ScheduleStore: 调度任务 CRUD
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

        # 领域子 Store（连接池创建后初始化）
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

        # 初始化领域子 Store
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

    # ── 项目操作（委托） ──

    async def create_project(self, project: Project) -> Project:
        return await self._project_store.create_project(project)

    async def get_project(self, project_id: str) -> Optional[Project]:
        return await self._project_store.get_project(project_id)

    async def update_project_status(self, project_id: str, status: ProjectStatus) -> None:
        await self._project_store.update_project_status(project_id, status)

    # ── 任务操作（委托） ──

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

    # ── 追踪日志操作（委托） ──

    async def save_trace(self, entry: TraceEntry) -> None:
        await self._trace_store.save_trace(entry)

    async def get_traces_by_project(self, project_id: str) -> list[TraceEntry]:
        return await self._trace_store.get_traces_by_project(project_id)

    # ── 提示词操作（委托） ──

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

    # ── 统计操作 ──

    async def get_stats(self) -> dict:
        """仪表盘统计摘要：项目数、任务数（按状态分组）。"""
        async with self._pool.acquire() as conn:
            project_count = await conn.fetchval("SELECT COUNT(*) FROM projects")
            task_total = await conn.fetchval("SELECT COUNT(*) FROM tasks")
            task_by_status = {}
            human_pending_count = 0
            rows = await conn.fetch(
                "SELECT status, COUNT(*) AS cnt FROM tasks GROUP BY status"
            )
            for row in rows:
                task_by_status[row["status"]] = row["cnt"]
                if row["status"] == "HUMAN_PENDING":
                    human_pending_count = row["cnt"]
            return {
                "project_count": project_count or 0,
                "task_total": task_total or 0,
                "task_by_status": task_by_status,
                "human_pending_count": human_pending_count,
            }

    async def get_config(self) -> dict:
        """读取当前运行时配置（屏蔽敏感字段）。"""
        from multi_agent.config import settings
        cfg = settings
        masked_key = (
            cfg.openai_api_key[:8] + "..." if len(cfg.openai_api_key) > 8 else "(未配置)"
        )
        masked_langfuse = (
            cfg.langfuse_public_key[:12] + "..."
            if len(cfg.langfuse_public_key) > 12
            else "(未配置)"
        )
        return {
            "openai_api_key_masked": masked_key,
            "openai_api_key_set": bool(cfg.openai_api_key),
            "openai_base_url": cfg.openai_base_url,
            "openai_api_model": cfg.openai_api_model,
            "gateway_model": cfg.gateway_model,
            "max_tokens_per_request": cfg.max_tokens_per_request,
            "max_retries_per_task": cfg.max_retries_per_task,
            "max_project_tasks": cfg.max_project_tasks,
            "host": cfg.host,
            "port": cfg.port,
            "langfuse_enabled": cfg.is_langfuse_enabled,
            "langfuse_host": cfg.langfuse_host,
            "langfuse_public_key_masked": masked_langfuse,
            "scheduler_enabled": cfg.scheduler_enabled,
            "scheduler_poll_interval": cfg.scheduler_poll_interval,
        }

    # ── 调度操作（委托） ──

    @property
    def schedule_store(self) -> ScheduleStore:
        """获取 ScheduleStore 实例（供 ScheduleManager 使用）。"""
        if self._schedule_store is None:
            raise RuntimeError("Database not initialized")
        return self._schedule_store
