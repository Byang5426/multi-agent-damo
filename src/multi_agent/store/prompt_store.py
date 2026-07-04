"""Prompt 领域数据访问层。"""

import logging
from typing import Optional

import asyncpg

from multi_agent.models.prompt import AgentPrompt

logger = logging.getLogger(__name__)


class PromptStore:
    """Agent Prompt CRUD operations."""

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def get_active_prompt(self, prompt_id: str) -> Optional[AgentPrompt]:
        """获取某个 prompt_id 的当前生效版本。"""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM agent_prompts WHERE prompt_id = $1 AND is_active = true",
                prompt_id,
            )
        if not row:
            return None
        return self._row_to_prompt(row)

    async def get_prompt_by_agent(
        self, agent_name: str, role: str = "system"
    ) -> Optional[AgentPrompt]:
        """获取某个 Agent 的当前生效 Prompt。"""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM agent_prompts
                WHERE agent_name = $1 AND role = $2 AND is_active = true
                ORDER BY version DESC LIMIT 1
                """,
                agent_name,
                role,
            )
        if not row:
            return None
        return self._row_to_prompt(row)

    async def list_prompts(self, agent_name: Optional[str] = None) -> list[AgentPrompt]:
        """列出所有 Prompt（可按 agent 过滤）。"""
        async with self._pool.acquire() as conn:
            if agent_name:
                rows = await conn.fetch(
                    """
                    SELECT * FROM agent_prompts
                    WHERE agent_name = $1
                    ORDER BY agent_name, prompt_id, version DESC
                    """,
                    agent_name,
                )
            else:
                rows = await conn.fetch(
                    "SELECT * FROM agent_prompts ORDER BY agent_name, prompt_id, version DESC"
                )
        return [self._row_to_prompt(r) for r in rows]

    async def create_prompt(self, prompt: AgentPrompt) -> AgentPrompt:
        """创建新版本的 Prompt。"""
        async with self._pool.acquire() as conn:
            # 将同 prompt_id 的旧版本设为非活跃
            await conn.execute(
                "UPDATE agent_prompts SET is_active = false WHERE prompt_id = $1",
                prompt.prompt_id,
            )
            await conn.execute(
                """
                INSERT INTO agent_prompts
                    (prompt_id, agent_name, role, version, content, description, is_active,
                     created_at, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                """,
                prompt.prompt_id,
                prompt.agent_name,
                prompt.role,
                prompt.version,
                prompt.content,
                prompt.description,
                prompt.is_active,
                prompt.created_at,
                prompt.updated_at,
            )
        return prompt

    async def update_prompt_content(
        self, prompt_id: str, content: str, description: str = ""
    ) -> Optional[AgentPrompt]:
        """更新当前生效版本的 Prompt 内容（自动创建新版本）。"""
        current = await self.get_active_prompt(prompt_id)
        if not current:
            return None
        new_prompt = AgentPrompt(
            prompt_id=current.prompt_id,
            agent_name=current.agent_name,
            role=current.role,
            version=current.version + 1,
            content=content,
            description=description or current.description,
            is_active=True,
        )
        return await self.create_prompt(new_prompt)

    async def seed_prompts(self, defaults: dict[str, str]) -> int:
        """种子填充默认 Prompt，仅当 prompt_id 不存在时插入。

        Args:
            defaults: {prompt_id: content} 映射

        Returns:
            实际插入的数量
        """
        inserted = 0
        for prompt_id, content in defaults.items():
            existing = await self.get_active_prompt(prompt_id)
            if existing is None:
                # 推导 agent_name
                agent_name = prompt_id.split("_")[0] if "_" in prompt_id else prompt_id
                prompt = AgentPrompt(
                    prompt_id=prompt_id,
                    agent_name=agent_name,
                    role="system",
                    version=1,
                    content=content,
                    description=f"Default {prompt_id} prompt",
                    is_active=True,
                )
                await self.create_prompt(prompt)
                inserted += 1
                logger.info("Seeded prompt: %s", prompt_id)
        return inserted

    @staticmethod
    def _row_to_prompt(row: asyncpg.Record) -> AgentPrompt:
        return AgentPrompt(
            prompt_id=row["prompt_id"],
            agent_name=row["agent_name"],
            role=row["role"],
            version=row["version"],
            content=row["content"],
            description=row["description"],
            is_active=row["is_active"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
