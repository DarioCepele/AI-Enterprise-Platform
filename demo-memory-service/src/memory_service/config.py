"""Service configuration loaded from the environment."""

from __future__ import annotations

from functools import lru_cache

from platform_core.settings import env_table as render_env_table
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Memory service settings.

    Deployment supplies complete connection URIs and credentials.
    """

    # Only the environment: a local .env is loaded by the entrypoint, never by
    # constructing settings, so no test depends on a file on somebody's disk.
    model_config = SettingsConfigDict(env_prefix="MEMORY_", extra="ignore")

    postgres_dsn: str = "postgresql://127.0.0.1:5432/memory"

    port: int = Field(default=8100, ge=1, le=65535)
    pool_min_size: int = Field(default=1, ge=0)
    pool_max_size: int = Field(default=10, ge=1)
    retention_days: int = Field(default=0, ge=0)
    json_logs: bool = Field(default=False)
    # A snapshot carries the whole conversation, tool results included: generous,
    # and still a ceiling nobody can push the service past with one request.
    max_request_bytes: int = Field(default=25_000_000, ge=1_000_000)

    summary_model: str = ""
    summary_base_url: str = "https://openrouter.ai/api/v1"
    summary_api_key: str = ""
    summary_language: str = ""

    embedding_model: str = ""

    drop_reasoning: bool = True
    keep_tool_results: int = Field(default=4, ge=0)
    max_context_messages: int | None = Field(default=60, ge=1)
    max_facts: int = Field(default=30, ge=0)


@lru_cache
def get_settings() -> Settings:
    """Cache one configuration instance per process.

    Configuration is not reloaded live.
    """
    return Settings()


# See the note in the master agent's config: the README table is generated here.
FIELD_NOTES = {
    "postgres_dsn": "Durable transcripts, summaries and facts. Required.",
    "port": "Where the service listens when started locally.",
    "pool_min_size": "Connections kept open.",
    "pool_max_size": "Connections at most.",
    "retention_days": (
        "Days of inactivity after which a thread is forgotten. 0 = never."
    ),
    "json_logs": "Structured logs for a collector instead of the readable line.",
    "max_request_bytes": "Largest request body accepted, streamed or declared.",
    "max_context_messages": "Window handed back to the agent, in messages.",
    "drop_reasoning": "Whether past reasoning leaves the rebuilt context.",
    "keep_tool_results": "How many recent tool results keep their content.",
    "summary_base_url": "Endpoint of the model that summarizes and extracts facts.",
    "summary_api_key": "Credential for that endpoint.",
    "summary_model": (
        "Model for summaries. Empty means no compaction and no durable facts."
    ),
    "summary_language": (
        "Language summaries are written in. Empty: the language of the conversation."
    ),
    "embedding_model": "Embedding model. Empty means no semantic search.",
    "max_facts": "How many durable facts are injected into a context.",
}


def env_table() -> str:
    """The environment table, generated from the fields themselves."""
    return render_env_table(Settings, FIELD_NOTES)


if __name__ == "__main__":
    print(env_table())
