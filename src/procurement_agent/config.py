"""Application settings loaded from environment variables."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Validated runtime configuration.

    Secrets never leave typed SecretStr fields; callers must call `.get_secret_value()`.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: Literal["local", "test", "prod"] = "local"
    app_log_level: str = "INFO"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    app_timezone: str = "Europe/Berlin"

    erpnext_base_url: str = "http://localhost:8080"
    erpnext_site: str = "frontend"
    erpnext_admin_user: str = "Administrator"
    erpnext_admin_password: SecretStr = SecretStr("admin")
    erpnext_api_key: SecretStr | None = None
    erpnext_api_secret: SecretStr | None = None
    erpnext_timeout_seconds: float = 30.0

    database_url: str = "postgresql+psycopg://procurement:procurement@localhost:5433/procurement"

    llm_provider: Literal["openai", "deepseek", "anthropic", "gemini"] = "openai"
    llm_model: str = "gpt-4.1-mini"
    openai_api_key: SecretStr | None = None
    deepseek_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    google_api_key: SecretStr | None = None

    embedding_provider: Literal["local", "openai"] = "local"
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    openai_embedding_model: str = "text-embedding-3-small"

    slack_bot_token: SecretStr | None = None
    slack_app_token: SecretStr | None = None
    slack_signing_secret: SecretStr | None = None
    slack_approver_user_ids: str = Field(default="")
    slack_channel_id: str = ""

    require_human_approval_for_erp_writes: bool = True
    order_submit_limit: float = 5000

    @property
    def approver_user_ids(self) -> set[str]:
        return {uid.strip() for uid in self.slack_approver_user_ids.split(",") if uid.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
