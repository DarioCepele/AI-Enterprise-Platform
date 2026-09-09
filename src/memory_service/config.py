"""Service configuration loaded from the environment."""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Memory service settings. Deployment supplies complete connection URIs and credentials."""

    model_config = SettingsConfigDict(env_file=".env", env_prefix="MEMORY_", extra="ignore")

    mongo_uri: str = "mongodb://127.0.0.1:27017"
    mongo_database: str = "demo_memory"
    redis_uri: str = "redis://127.0.0.1:6379/0"

    bucket_size: int = Field(default=50, ge=1, le=500)
    retention_days: int = Field(default=0, ge=0)

    hot_tail_seconds: int = Field(default=1800, ge=1)
    hot_tail_messages: int = Field(default=100, ge=1)

    summary_model: str = ""
    summary_base_url: str = "https://openrouter.ai/api/v1"
    summary_api_key: str = ""

    embedding_model: str = ""

    drop_reasoning: bool = True
    keep_tool_results: int = Field(default=4, ge=0)
    max_context_messages: int | None = Field(default=60, ge=1)
    max_facts: int = Field(default=30, ge=0)


@lru_cache
def get_settings() -> Settings:
    """Cache one configuration instance per process; configuration is not reloaded live."""
    return Settings()
