"""Prompt loader: agents use this to fetch prompts from the database.

Falls back to hardcoded defaults when the DB prompt is not yet available.
"""

import logging
from typing import Optional

from multi_agent.store.pg_store import PgStore

logger = logging.getLogger(__name__)

_store: Optional[PgStore] = None


def init_prompt_loader(store: PgStore) -> None:
    """Initialize the prompt loader with a store instance. Called at startup."""
    global _store
    _store = store


async def load_prompt(prompt_id: str, fallback: str) -> str:
    """Load a prompt from the database, falling back to the hardcoded default.

    Args:
        prompt_id: The prompt identifier (e.g. 'pm_decompose').
        fallback: Hardcoded default content if DB has no entry yet.

    Returns:
        The prompt content string.
    """
    if _store is not None:
        try:
            prompt = await _store.get_active_prompt(prompt_id)
            if prompt is not None:
                return prompt.content
        except Exception as e:
            logger.warning("Failed to load prompt '%s' from DB: %s", prompt_id, e)
    return fallback
