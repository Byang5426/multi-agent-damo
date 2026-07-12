"""提示词加载器：Agent 通过此模块从数据库动态获取 Prompt。

当数据库中尚无对应 Prompt 时，回退到代码中的硬编码默认值。
设计为依赖注入式类实例，替代原先的全局可变状态方案。
"""

import logging
from typing import Optional

from multi_agent.store.pg_store import PgStore

logger = logging.getLogger(__name__)


class PromptLoader:
    """从数据库加载 Prompt，失败时回退到硬编码默认值。

    替代原先基于全局状态的可变模块级加载器。
    """

    def __init__(self, store: PgStore):
        self._store = store

    async def load_prompt(self, prompt_id: str, fallback: str) -> str:
        """从数据库加载 Prompt，回退到硬编码默认值。

        Args:
            prompt_id: Prompt 标识符（如 'pm_decompose'）。
            fallback: 数据库无记录时使用的硬编码默认内容。

        Returns:
            Prompt 内容字符串。
        """
        try:
            prompt = await self._store.get_active_prompt(prompt_id)
            if prompt is not None:
                return prompt.content
        except Exception as e:
            logger.warning("Failed to load prompt '%s' from DB: %s", prompt_id, e)
        return fallback


# ── 向后兼容的模块级 API ──
# 保持现有调用方正常工作，新代码可直接使用 PromptLoader 类。

_loader: Optional[PromptLoader] = None


def init_prompt_loader(store: PgStore) -> None:
    """初始化模块级 Prompt 加载器，在应用启动时调用。"""
    global _loader
    _loader = PromptLoader(store)


async def load_prompt(prompt_id: str, fallback: str) -> str:
    """模块级便捷函数，委托给已初始化的 PromptLoader 实例。"""
    if _loader is not None:
        return await _loader.load_prompt(prompt_id, fallback)
    return fallback
