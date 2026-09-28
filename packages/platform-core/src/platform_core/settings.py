"""Configuration conventions shared by every service.

Two things live here. `A2AAgentSettings` is what every A2A agent of the
platform reads, whatever its domain: the subagents differ only in their prefix
and in what their tools do. `env_table` renders a settings class as the table
the READMEs publish, from the fields themselves -- a table written by hand is a
table that lies after the second change.
"""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings

from .mcp import MCPServerConfig, parse_mcp_servers
from .push import PushURLPolicy


class A2AAgentSettings(BaseSettings):
    """What an A2A agent of the platform reads. Subclasses set `env_prefix`."""

    base_url: str = Field(default="http://localhost:8000/")
    service_token: str = Field(default="")
    json_logs: bool = Field(default=False)
    language: str = Field(default="")
    mcp_servers: str = Field(default="")
    push_allowed_urls: str = Field(default="")
    push_encryption_key: str = Field(default="")
    task_store_dsn: str = Field(default="")

    model_base_url: str = Field(
        default="https://openrouter.ai/api/v1",
        validation_alias=AliasChoices("OPENAI_BASE_URL"),
    )
    model_api_key: str = Field(
        default="", validation_alias=AliasChoices("OPENAI_API_KEY")
    )
    model: str = Field(
        default="anthropic/claude-sonnet-5",
        validation_alias=AliasChoices("OPENAI_CHAT_COMPLETION_MODEL"),
    )
    fake_client: bool = Field(default=False)

    def mcp(self) -> tuple[MCPServerConfig, ...]:
        return parse_mcp_servers(self.mcp_servers)

    def push_policy(self) -> PushURLPolicy:
        return PushURLPolicy.from_text(self.push_allowed_urls)


A2A_AGENT_NOTES = {
    "base_url": "The url this agent declares in its own card.",
    "service_token": "Token that unlocks the extended card. Empty: nobody gets it.",
    "json_logs": "Structured logs for a collector instead of the readable line.",
    "language": "Language of the answers. Empty: the language of the request.",
    "mcp_servers": (
        'External MCP servers as JSON: [{"name":"x","url":"http://...",'
        '"allowed_tools":[],"approval":"never"}]. Empty: no MCP tools.'
    ),
    "push_allowed_urls": (
        "Comma-separated URL prefixes push notifications may be sent to. "
        "Empty: webhooks are refused."
    ),
    "push_encryption_key": (
        "Fernet key encrypting webhook tokens at rest in the durable store. "
        "Empty: stored as given."
    ),
    "task_store_dsn": (
        "Postgres for tasks and webhooks, so they survive a restart. "
        "Empty: kept in memory and lost with the process."
    ),
    "model_base_url": "Where the model lives. Any OpenAI-compatible endpoint.",
    "model_api_key": "Credential for that endpoint.",
    "model": "The model this agent reasons with.",
    "fake_client": (
        "Fixed answers without a model, and without a credential: for tests and "
        "for running the platform offline."
    ),
}


def variable_of(settings: type[BaseSettings], name: str) -> str:
    """The environment variable that sets a field, as the field declares it."""
    field = settings.model_fields[name]
    alias = field.validation_alias
    if isinstance(alias, AliasChoices):
        return str(alias.choices[0])
    if isinstance(alias, str):
        return alias
    prefix = str(settings.model_config.get("env_prefix", ""))
    return f"{prefix}{name}".upper()


def env_table(settings: type[BaseSettings], notes: Mapping[str, str]) -> str:
    """The Markdown table of a settings class: variable, default, what it decides."""
    rows = ["| Variable | Default | What it decides |", "|---|---|---|"]
    for name, field in settings.model_fields.items():
        default = field.get_default(call_default_factory=True)
        if isinstance(default, tuple | list):
            default = ", ".join(str(item) for item in default)
        shown = f"`{default}`" if default not in ("", None) else "*(empty)*"
        rows.append(
            f"| `{variable_of(settings, name)}` | {shown} | {notes.get(name, '')} |"
        )
    return "\n".join(rows)
