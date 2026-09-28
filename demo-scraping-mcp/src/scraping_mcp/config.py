"""Configuration read from environment variables, validated in one place."""

from __future__ import annotations

from platform_core.settings import env_table as render_env_table
from platform_core.urlguard import URLPolicy
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Everything the scraping MCP server reads from the environment."""

    model_config = SettingsConfigDict(
        env_prefix="SCRAPING_", extra="ignore", frozen=True
    )

    port: int = Field(
        default=8600,
        ge=1,
        le=65535,
        validation_alias=AliasChoices("SCRAPING_PORT", "PORT"),
    )
    json_logs: bool = Field(default=False)
    server_hosts: str = Field(default="scraping-mcp:*,localhost:*,127.0.0.1:*")
    allow_private_targets: bool = Field(default=False)
    target_hosts: str = Field(default="")
    max_chars: int = Field(default=50_000, ge=1_000)
    timeout_seconds: float = Field(default=45.0, gt=0)
    max_sessions: int = Field(default=32, ge=1)

    def policy(self) -> URLPolicy:
        hosts = frozenset(
            part.strip().lower()
            for part in self.target_hosts.split(",")
            if part.strip()
        )
        return URLPolicy(allow_private=self.allow_private_targets, allowed_hosts=hosts)

    def allowed_server_hosts(self) -> list[str]:
        return [part.strip() for part in self.server_hosts.split(",") if part.strip()]


def get_settings() -> Settings:
    """Builds the Settings from the environment. No cache: tests change the env."""
    return Settings()


FIELD_NOTES = {
    "port": "Where the server listens.",
    "json_logs": "Structured logs for a collector instead of the readable line.",
    "server_hosts": (
        "Host headers this server answers to (`host:*` for any port): the MCP "
        "DNS-rebinding protection."
    ),
    "allow_private_targets": (
        "Let `fetch_url` reach private, loopback and link-local addresses. "
        "Off: the scraper cannot be pointed at the platform's own network."
    ),
    "target_hosts": (
        "Comma-separated host names `fetch_url` may reach even when they resolve "
        "to a private address (an intranet wiki, say)."
    ),
    "max_chars": "Longest Markdown handed back to the model, in characters.",
    "timeout_seconds": "How long one page may take to render before it is abandoned.",
    "max_sessions": "Concurrent MCP sessions at most.",
}


def env_table() -> str:
    return render_env_table(Settings, FIELD_NOTES)


if __name__ == "__main__":
    print(env_table())
