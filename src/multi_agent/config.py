"""基于 pydantic-settings 的环境变量配置管理。"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """应用配置，从环境变量加载。"""

    # LLM API 配置
    openai_api_key: str = ""           # OpenAI / OpenRouter API 密钥
    openai_base_url: str = ""          # API 基础地址（OpenRouter: https://openrouter.ai/api/v1）
    openai_api_model: str = "gpt-4o"  # 主力模型（PM/Worker 使用）

    # Gateway 路由使用轻量模型
    gateway_model: str = "gpt-4o-mini"

    # PostgreSQL
    pg_host: str = "localhost"
    pg_port: int = 5432
    pg_user: str = "postgres"
    pg_password: str = ""
    pg_database: str = "multi_agent"
    pg_min_connections: int = 2
    pg_max_connections: int = 10

    # 服务配置
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False
    allowed_origins: str = "*"  # CORS 允许的来源，逗号分隔

    # Agent 执行限制
    max_retries_per_task: int = 3      # 单任务最大重试次数
    max_project_tasks: int = 20        # 单项目最大任务数
    max_tokens_per_request: int = 4096 # 单次请求最大 Token 数

    # Langfuse Trace（可选，未配置时仅保留 PostgreSQL 本地备份）
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"
    langfuse_enabled: bool = False  # 当 public_key 和 secret_key 均非空时自动启用

    # Scheduler（定时任务调度器）
    scheduler_enabled: bool = True          # 是否启用调度器
    scheduler_poll_interval: int = 30       # 轮询间隔（秒）

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    @property
    def pg_dsn(self) -> str:
        """构建 PostgreSQL DSN 连接字符串。"""
        return (
            f"postgresql://{self.pg_user}:{self.pg_password}"
            f"@{self.pg_host}:{self.pg_port}/{self.pg_database}"
        )

    @property
    def is_langfuse_enabled(self) -> bool:
        """Langfuse 是否已配置并启用。"""
        return bool(self.langfuse_public_key and self.langfuse_secret_key)


settings = Settings()
