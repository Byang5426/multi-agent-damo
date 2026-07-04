"""Configuration management via environment variables."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # LLM API
    openai_api_key: str = ""
    openai_base_url: str = ""
    openai_api_model: str = "gpt-4o"  # Strong model for PM/Worker

    # Gateway uses a lighter model for routing
    gateway_model: str = "gpt-4o-mini"

    # PostgreSQL
    pg_host: str = "localhost"
    pg_port: int = 5432
    pg_user: str = "postgres"
    pg_password: str = ""
    pg_database: str = "multi_agent"
    pg_min_connections: int = 2
    pg_max_connections: int = 10

    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False

    # Agent limits
    max_retries_per_task: int = 3
    max_project_tasks: int = 20
    max_tokens_per_request: int = 4096

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    @property
    def pg_dsn(self) -> str:
        """构建 PostgreSQL DSN 连接字符串。"""
        return (
            f"postgresql://{self.pg_user}:{self.pg_password}"
            f"@{self.pg_host}:{self.pg_port}/{self.pg_database}"
        )


settings = Settings()
