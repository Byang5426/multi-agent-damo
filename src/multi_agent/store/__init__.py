"""Storage layer.

使用 PostgreSQL (asyncpg) 作为唯一存储后端。
"""

from multi_agent.store.pg_store import PgStore

__all__ = ["PgStore"]
