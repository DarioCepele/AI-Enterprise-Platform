"""Configuration read from environment variables."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

from dotenv import load_dotenv
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

load_dotenv()

SINGLE_TENANT_SCOPE = "local-laboratory"

# Where the interface runs while somebody develops. Any other origin has to be
# named: an API that answered everybody would let any page a browser happens to
# have open read the instances of this service.
DEFAULT_ORIGINS = (
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:3001",
    "http://127.0.0.1:3001",
)


class Settings(BaseSettings):
    """Everything the process service reads from the environment."""

    model_config = SettingsConfigDict(env_prefix="PROCESS_", extra="ignore", frozen=True)

    postgres_dsn: str = Field(default="postgresql://127.0.0.1:5432/processes")
    definitions_path: Path = Field(default=Path("processes"))
    default_scope: str = Field(default=SINGLE_TENANT_SCOPE)
    scope_header: str = Field(default="X-Process-Scope")
    json_logs: bool = Field(default=False)
    pool_min_size: int = Field(default=1, ge=0)
    pool_max_size: int = Field(default=10, ge=1)
    port: int = Field(default=8300, ge=1, le=65535)
    allowed_origins: Annotated[tuple[str, ...], NoDecode] = Field(default=DEFAULT_ORIGINS)
    public_url: str = Field(default="http://localhost:8300")
    push_secret: str = Field(default="laboratory-without-a-secret")
    agents: Annotated[dict[str, str], NoDecode] = Field(default_factory=dict)

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def _origins(cls, value: object) -> object:
        """A comma-separated list, because that is what an environment holds."""
        if not isinstance(value, str):
            return value
        origins = tuple(part.strip() for part in value.split(",") if part.strip())
        return origins or DEFAULT_ORIGINS

    @field_validator("agents", mode="before")
    @classmethod
    def _agents(cls, value: object) -> object:
        """A JSON object of name -> url. Empty means no agent steps can run."""
        if not isinstance(value, str):
            return value
        text = value.strip()
        return json.loads(text) if text else {}


# What each variable decides, next to the fields it decides for. The table in
# the README is generated from here.
FIELD_NOTES = {
    "postgres_dsn": "Where instances live. Durable execution needs real transactions.",
    "definitions_path": "Folder of process definitions, loaded once at startup.",
    "default_scope": "Authorization boundary used when the header says nothing.",
    "scope_header": "Header carrying the scope of the caller.",
    "json_logs": "Structured logs for a collector instead of the readable line.",
    "pool_min_size": "Connections kept open.",
    "pool_max_size": "Connections at most.",
    "port": "Where the service listens when started locally.",
    "allowed_origins": "Which pages may read this API from a browser. Comma-separated.",
    "public_url": "How a remote agent reaches this service back, for notifications.",
    "push_secret": "Signs notification tokens. Change it: the default is public.",
    "agents": 'Agents a step may delegate to, as JSON: {"knowledge": "http://..."}.',
}


def get_settings() -> Settings:
    """Builds the Settings from the environment. No cache: tests change the env."""
    return Settings()


def env_table() -> str:
    """The environment table, generated from the fields themselves."""
    prefix = Settings.model_config.get("env_prefix", "")
    rows = ["| Variable | Default | What it decides |", "|---|---|---|"]
    for name, field in Settings.model_fields.items():
        default = field.get_default(call_default_factory=True)
        shown = f"`{default}`" if default not in ("", None) else "*(empty)*"
        rows.append(f"| `{prefix.upper()}{name.upper()}` | {shown} | {FIELD_NOTES.get(name, '')} |")
    return "\n".join(rows)


if __name__ == "__main__":
    print(env_table())
