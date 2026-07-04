"""Prompt loader: agents use this to fetch prompts from the database.

Falls back to hardcoded defaults when the DB prompt is not yet available.
"""

import logging
from typing import Optional

from multi_agent.store.pg_store import PgStore

logger = logging.getLogger(__name__)


class PromptLoader:
    """Loads prompts from database with fallback to hardcoded defaults.

    Replaces the previous global-state based module-level loader.
    """

    def __init__(self, store: PgStore):
        self._store = store

    async def load_prompt(self, prompt_id: str, fallback: str) -> str:
        """Load a prompt from the database, falling back to the hardcoded default.

        Args:
            prompt_id: The prompt identifier (e.g. 'pm_decompose').
            fallback: Hardcoded default content if DB has no entry yet.

        Returns:
            The prompt content string.
        """
        try:
            prompt = await self._store.get_active_prompt(prompt_id)
            if prompt is not None:
                return prompt.content
        except Exception as e:
            logger.warning("Failed to load prompt '%s' from DB: %s", prompt_id, e)
        return fallback


# ── Backward-compatible module-level API ──
# Keeps existing callers working while new code uses the class directly.

_loader: Optional[PromptLoader] = None


def init_prompt_loader(store: PgStore) -> None:
    """Initialize the module-level prompt loader. Called at startup."""
    global _loader
    _loader = PromptLoader(store)


async def load_prompt(prompt_id: str, fallback: str) -> str:
    """Module-level convenience function. Delegates to the initialized PromptLoader."""
    if _loader is not None:
        return await _loader.load_prompt(prompt_id, fallback)
    return fallback
