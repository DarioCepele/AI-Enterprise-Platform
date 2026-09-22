"""Service configuration loaded from the environment."""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Memory service settings.

    Deployment supplies complete connection URIs and credentials.
    """

    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="MEMORY_", extra="ignore"
    )

    postgres_dsn: str = "postgresql://127.0.0.1:5432/memoria"

    port: int = Field(default=8100, ge=1, le=65535)
    pool_min_size: int = Field(default=1, ge=0)
    pool_max_size: int = Field(default=10, ge=1)
    retention_days: int = Field(default=0, ge=0)
    json_logs: bool = Field(default=False)

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
    "max_context_messages": "Window handed back to the agent, in messages.",
    "drop_reasoning": "Whether past reasoning leaves the rebuilt context.",
    "keep_tool_results": "How many recent tool results keep their content.",
    "summary_base_url": "Endpoint of the model that summarizes and extracts facts.",
    "summary_api_key": "Credential for that endpoint.",
    "summary_model": (
        "Model for summaries. Empty means no compaction and no durable facts."
    ),
    "embedding_model": "Embedding model. Empty means no semantic search.",
    "max_facts": "How many durable facts are injected into a context.",
}


def env_table() -> str:
    """The environment table, generated from the fields themselves."""
    prefix = Settings.model_config.get("env_prefix", "")
    rows = ["| Variable | Default | What it decides |", "|---|---|---|"]
    for name, field in Settings.model_fields.items():
        default = field.get_default(call_default_factory=True)
        shown = f"`{default}`" if default not in ("", None) else "*(empty)*"
        note = FIELD_NOTES.get(name, "")
        rows.append(f"| `{prefix.upper()}{name.upper()}` | {shown} | {note} |")
    return "\n".join(rows)


if __name__ == "__main__":
    print(env_table())
