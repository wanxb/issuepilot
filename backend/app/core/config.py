"""应用配置。

从 .env / 环境变量加载，Pydantic Settings 自动校验。
所有 secret 字段不打印到日志（repr_kwargs override）。
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),  # 兼容 backend/ 和项目根两种 cwd
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- App ---
    app_name: str = "issuepilot"
    env: Literal["dev", "test", "prod"] = "dev"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # --- 数据库 ---
    database_url: str = "postgresql+asyncpg://issuepilot:issuepilot@localhost:5432/issuepilot"
    db_pool_size: int = 10
    db_max_overflow: int = 20

    # --- Redis / Celery ---
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = ""
    celery_result_backend: str = ""

    # --- Anthropic（直连 / 中转 二选一）---
    anthropic_api_key: SecretStr | None = None
    anthropic_base_url: str | None = None
    anthropic_auth_token: SecretStr | None = None

    # --- Fallback (DeepSeek Anthropic 兼容接口)  ---
    deepseek_anthropic_token: SecretStr | None = None

    # --- GitHub ---
    github_token: SecretStr | None = None
    github_dev_token: SecretStr | None = None
    github_username: str | None = None
    github_webhook_secret: SecretStr | None = None

    # --- 沙箱 ---
    sandbox_image_prefix: str = "agent-sandbox"
    dev_task_timeout_minutes: int = 20
    agent_b_max_turns: int = 25
    sandbox_cache_dir: str = "./.sandbox_cache"
    sandbox_workspace_dir: str = "./.sandbox_workspaces"

    # --- Workers ---
    max_analyze_workers: int = Field(default=3, ge=1)
    max_dev_workers: int = Field(default=2, ge=1)
    max_review_workers: int = Field(default=3, ge=1)
    max_dev_retry: int = Field(default=2, ge=0)
    max_review_retry: int = Field(default=3, ge=0)

    def model_post_init(self, __context: object) -> None:  # noqa: D401
        if not self.celery_broker_url:
            object.__setattr__(self, "celery_broker_url", self.redis_url)
        if not self.celery_result_backend:
            object.__setattr__(self, "celery_result_backend", self.redis_url)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
