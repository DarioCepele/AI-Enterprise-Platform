"""Configuration read from environment variables.

One structure, read in one place. What the product is called, which language it
answers in and which scope it serves are configuration, not constants buried in
a prompt or in a component: whoever forks this repository changes them here.

Every variable is `MASTER_<NAME>`. The `DEMO_<NAME>` spelling it replaced is
still read, so an existing environment keeps working, and the service says at
startup which old names it found.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Annotated

from platform_core.mcp import MCPServerConfig, tool_identifier
from platform_core.settings import env_table as render_env_table
from platform_core.settings import variable_of
from pydantic import AliasChoices, BaseModel, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

logger = logging.getLogger(__name__)

PREFIX = "MASTER_"
LEGACY_PREFIX = "DEMO_"

DEFAULT_ORIGINS = (
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:3001",
    "http://127.0.0.1:3001",
)

SINGLE_TENANT_SCOPE = "local-laboratory"

__all__ = ["MCPServerConfig", "Settings", "SubagentConfig", "get_settings"]


def env(name: str) -> AliasChoices:
    """`MASTER_<NAME>`, and the `DEMO_<NAME>` it replaced, still read."""
    return AliasChoices(f"{PREFIX}{name}", f"{LEGACY_PREFIX}{name}")


def _list(value: object) -> object:
    """A comma-separated list, because that is what an environment holds."""
    if not isinstance(value, str):
        return value
    return tuple(part.strip() for part in value.split(",") if part.strip())


def _json_list(value: object) -> object:
    """A JSON list, or nothing. An empty variable means nothing, not an error."""
    if not isinstance(value, str):
        return value
    text = value.strip()
    return json.loads(text) if text else ()


class SubagentConfig(BaseModel):
    """A remote agent this one may call, named as the model will see it."""

    name: str
    url: str
    token: str = ""

    @field_validator("name", mode="after")
    @classmethod
    def _identifier(cls, value: str) -> str:
        """The name becomes part of a tool name, so it has to be one."""
        return tool_identifier(value, "subagent name")


class Settings(BaseSettings):
    """Everything the process reads from the environment, validated at once."""

    # Only the environment is read here. A local .env is loaded by the entrypoint
    # (`python -m master_agent`), never on import: a library module that read a file on
    # import would make every test depend on whatever sits on the developer's disk.
    model_config = SettingsConfigDict(extra="ignore", frozen=True)

    base_url: str = Field(
        default="https://openrouter.ai/api/v1",
        validation_alias=AliasChoices("OPENAI_BASE_URL"),
    )
    api_key: str = Field(default="", validation_alias=AliasChoices("OPENAI_API_KEY"))
    model: str = Field(
        default="anthropic/claude-sonnet-5",
        validation_alias=AliasChoices("OPENAI_CHAT_COMPLETION_MODEL"),
    )
    vision_model: str = Field(
        default="qwen/qwen3.8-27b", validation_alias=env("VISION_MODEL")
    )
    use_fake_client: bool = Field(default=False, validation_alias=env("FAKE_CLIENT"))
    allowed_origins: Annotated[tuple[str, ...], NoDecode] = Field(
        default=DEFAULT_ORIGINS, validation_alias=env("ALLOWED_ORIGINS")
    )
    cors_credentials: bool = Field(
        default=False, validation_alias=env("CORS_CREDENTIALS")
    )

    product_name: str = Field(
        default="Agent Platform", validation_alias=env("PRODUCT_NAME")
    )
    product_language: str = Field(default="", validation_alias=env("PRODUCT_LANGUAGE"))
    instructions_file: str = Field(
        default="", validation_alias=env("INSTRUCTIONS_FILE")
    )
    skills_dirs: Annotated[tuple[str, ...], NoDecode] = Field(
        default=(), validation_alias=env("SKILLS_DIRS")
    )
    tool_factories: Annotated[tuple[str, ...], NoDecode] = Field(
        default=(), validation_alias=env("TOOL_FACTORIES")
    )

    default_scope: str = Field(
        default=SINGLE_TENANT_SCOPE, validation_alias=env("DEFAULT_SCOPE")
    )
    scope_header: str = Field(default="", validation_alias=env("SCOPE_HEADER"))
    json_logs: bool = Field(default=False, validation_alias=env("JSON_LOGS"))
    logs_endpoint: bool = Field(default=True, validation_alias=env("LOGS_ENDPOINT"))

    memory_service_url: str = Field(
        default="", validation_alias=env("MEMORY_SERVICE_URL")
    )
    postgres_dsn: str = Field(default="", validation_alias=env("POSTGRES_DSN"))
    knowledge_agent_url: str = Field(
        default="", validation_alias=env("KNOWLEDGE_AGENT_URL")
    )
    knowledge_service_token: str = Field(
        default="", validation_alias=env("KNOWLEDGE_SERVICE_TOKEN")
    )
    subagents: Annotated[tuple[SubagentConfig, ...], NoDecode] = Field(
        default=(), validation_alias=env("SUBAGENTS")
    )
    public_url: str = Field(default="", validation_alias=env("PUBLIC_URL"))
    push_secret: str = Field(default="", validation_alias=env("PUSH_SECRET"))
    push_window_seconds: int = Field(
        default=3600, ge=60, validation_alias=env("PUSH_WINDOW_SECONDS")
    )
    process_service_url: str = Field(
        default="", validation_alias=env("PROCESS_SERVICE_URL")
    )
    voice_service_url: str = Field(
        default="", validation_alias=env("VOICE_SERVICE_URL")
    )
    subagent_wait_seconds: float = Field(
        default=60.0, validation_alias=env("SUBAGENT_WAIT_SECONDS")
    )
    upload_dir: str = Field(default="", validation_alias=env("UPLOAD_DIR"))
    upload_max_bytes: int = Field(
        default=200 * 1024 * 1024, validation_alias=env("UPLOAD_MAX_BYTES")
    )
    upload_ttl_seconds: float = Field(
        default=3600.0, validation_alias=env("UPLOAD_TTL_SECONDS")
    )
    media_hosts: Annotated[tuple[str, ...], NoDecode] = Field(
        default=(), validation_alias=env("MEDIA_HOSTS")
    )
    vision_max_video_bytes: int = Field(
        default=20 * 1024 * 1024, ge=1, validation_alias=env("VISION_MAX_VIDEO_BYTES")
    )
    mcp_servers: Annotated[tuple[MCPServerConfig, ...], NoDecode] = Field(
        default=(), validation_alias=env("MCP_SERVERS")
    )
    tools_requiring_approval: Annotated[tuple[str, ...], NoDecode] = Field(
        default=("start_process",), validation_alias=env("TOOLS_REQUIRING_APPROVAL")
    )
    max_model_calls: int = Field(
        default=15, ge=1, validation_alias=env("MAX_MODEL_CALLS")
    )
    max_tool_calls: int = Field(
        default=50, ge=1, validation_alias=env("MAX_TOOL_CALLS")
    )

    @field_validator(
        "base_url",
        "api_key",
        "model",
        "vision_model",
        "product_name",
        "product_language",
        "instructions_file",
        "default_scope",
        "scope_header",
        "memory_service_url",
        "postgres_dsn",
        "knowledge_agent_url",
        "knowledge_service_token",
        "public_url",
        "push_secret",
        "process_service_url",
        "voice_service_url",
        "upload_dir",
        mode="after",
    )
    @classmethod
    def _trimmed(cls, value: str) -> str:
        return value.strip()

    @field_validator("subagents", "mcp_servers", mode="before")
    @classmethod
    def _json(cls, value: object) -> object:
        return _json_list(value)

    @field_validator(
        "skills_dirs",
        "tool_factories",
        "media_hosts",
        "tools_requiring_approval",
        mode="before",
    )
    @classmethod
    def _lists(cls, value: object) -> object:
        return _list(value)

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def _origins(cls, value: object) -> object:
        origins = _list(value)
        return origins or DEFAULT_ORIGINS

    @model_validator(mode="after")
    def _knowledge_agent_is_a_subagent(self) -> Settings:
        """The single-agent variables stay valid, as one entry in the list.

        A fork that only has one subagent should not have to learn a JSON list
        to say so.
        """
        if self.subagents or not self.knowledge_agent_url:
            return self
        object.__setattr__(
            self,
            "subagents",
            (
                SubagentConfig(
                    name="knowledge",
                    url=self.knowledge_agent_url,
                    token=self.knowledge_service_token,
                ),
            ),
        )
        return self

    @model_validator(mode="after")
    def _tokens_from_their_own_variables(self) -> Settings:
        """A subagent's token can live in `MASTER_SUBAGENT_TOKEN_<NAME>`.

        The list of subagents is configuration anyone may read; their tokens
        are secrets, and a secret store hands out one variable per secret.
        """
        filled = tuple(
            agent.model_copy(
                update={
                    "token": os.getenv(
                        f"{PREFIX}SUBAGENT_TOKEN_{agent.name.upper()}", ""
                    )
                }
            )
            if not agent.token
            else agent
            for agent in self.subagents
        )
        object.__setattr__(self, "subagents", filled)
        return self

    def require_model_access(self) -> None:
        """Fails now, with the name of what is missing, instead of at the first turn.

        A model without credentials answers nothing, and the error arrives when
        a person is already waiting: better to refuse to start.
        """
        if self.use_fake_client:
            return
        if not self.api_key:
            raise ValueError(
                "OPENAI_API_KEY is empty: set it, or set MASTER_FAKE_CLIENT=true to "
                "run without a model."
            )

    def webhooks_enabled(self) -> bool:
        """Push notifications need an address to be reached at and a key to sign."""
        return bool(self.public_url and self.push_secret)


def get_settings() -> Settings:
    """Builds the Settings from the environment. No cache: tests change the env."""
    return Settings()


def legacy_variables() -> list[str]:
    """The `DEMO_*` names still set in this environment, to be renamed."""
    return sorted(name for name in os.environ if name.startswith(LEGACY_PREFIX))


# What each variable decides, next to the fields it decides it for. The table in
# the README is generated from here: one written by hand is one that lies after
# the second change.
FIELD_NOTES = {
    "base_url": "Where the model lives. Any OpenAI-compatible endpoint.",
    "api_key": "Credential for that endpoint. Required unless the fake client is on.",
    "model": "Model the master agent talks to.",
    "vision_model": (
        "Model the analyze_video tool talks to, separate from the conversation's: "
        "it must accept images, and video natively when it can."
    ),
    "use_fake_client": (
        "Deterministic answers without a model. For tests and offline work."
    ),
    "allowed_origins": "Comma-separated origins allowed by CORS.",
    "cors_credentials": (
        "Accept the page's cookies (a single sign-on or load-balancer cookie in "
        "front). Needs named origins, and the frontend's API_CREDENTIALS=include."
    ),
    "product_name": "What the agent calls itself in its own instructions.",
    "product_language": (
        "Language the agent answers in. Empty: the language the user writes in."
    ),
    "instructions_file": (
        "A file whose text replaces the built-in instructions of the agent. "
        "`{product}` and `{language}` are filled in."
    ),
    "skills_dirs": (
        "Comma-separated folders of extra skills (one SKILL.md per subfolder), "
        "added to the built-in ones."
    ),
    "tool_factories": (
        "Comma-separated `module:function` paths; each function returns a list of "
        "tools added to the agent. Extension without editing the platform."
    ),
    "default_scope": "Authorization boundary used when nothing else says otherwise.",
    "scope_header": (
        "Header carrying the scope, read **only** when this is set: naming it "
        "means something in front has verified it and strips it from clients."
    ),
    "json_logs": "Structured logs for a collector instead of the readable line.",
    "logs_endpoint": (
        "Whether `GET /logs` (the LOG tab) answers. Off in deployments where logs "
        "go to a collector."
    ),
    "memory_service_url": (
        "Memory service. Without it the conversation lives in RAM and dies "
        "with the process."
    ),
    "postgres_dsn": (
        "Shared logs, deduplicated notifications and uploads shared by every "
        "replica. Without it all three are per replica."
    ),
    "knowledge_agent_url": (
        "One subagent, the short way. Ignored when MASTER_SUBAGENTS is set."
    ),
    "knowledge_service_token": "Service token of that subagent, for its extended card.",
    "subagents": (
        'Subagents as JSON: [{"name":"x","url":"http://..."}]. Their tokens go in '
        "MASTER_SUBAGENT_TOKEN_<NAME>."
    ),
    "public_url": "How a subagent reaches this agent back, for push notifications.",
    "push_secret": (
        "Signs push notification tokens. Empty: no webhooks are registered, and "
        "none are accepted."
    ),
    "push_window_seconds": "Validity window of a push token; the previous one counts.",
    "process_service_url": (
        "Where durable processes live. Empty: the agent cannot start one."
    ),
    "voice_service_url": (
        "The voice service, for its POST /transcribe. Empty: the video-analysis "
        "tool is not shown."
    ),
    "subagent_wait_seconds": (
        "How long a turn waits before letting the outcome arrive by notification."
    ),
    "upload_dir": "Folder for uploads when there is no Postgres. Empty: OS temp.",
    "upload_max_bytes": "Largest accepted upload for /uploads. Bigger is refused.",
    "upload_ttl_seconds": (
        "How long an uploaded file stays fetchable before it is swept away."
    ),
    "media_hosts": (
        "Comma-separated hosts analyze_video may download from even when they "
        "resolve to a private address. Everything else must be public."
    ),
    "vision_max_video_bytes": (
        "Largest video sent whole to the vision model; above it, sampled frames "
        "are described instead."
    ),
    "mcp_servers": (
        'External MCP servers as JSON: [{"name":"x","url":"http://...",'
        '"token":"","allowed_tools":[],"approval":"never"}]. Tools are connected '
        "lazily on first use."
    ),
    "tools_requiring_approval": (
        "Comma-separated tools that stop for a person's approval before they run."
    ),
    "max_model_calls": "Model round trips one run may make before it has to answer.",
    "max_tool_calls": "Tool invocations one run may make before it has to answer.",
}


NOT_CONFIGURED = "not configured by this deployment"

# Never read for their value, only named: a report that reads the running
# configuration has every credential within reach, so the ones that are
# credentials are listed once, here, and skipped wherever fields are iterated.
CREDENTIAL_FIELDS = frozenset(
    {"api_key", "knowledge_service_token", "postgres_dsn", "push_secret"}
)

# What this deployment discloses about itself, and under which heading. An
# allowlist rather than a list of exclusions: a field added to Settings tomorrow
# stays out of the report until someone decides it may be shown. Internal
# addresses are not on it: the topology of a deployment helps an attacker and
# tells a user nothing about the AI they are talking to.
DISCLOSED_FIELDS = {
    "product_name": "identity",
    "product_language": "identity",
    "model": "models",
    "vision_model": "models",
    "use_fake_client": "models",
    "base_url": "providers",
}


def host_of_dsn(dsn: str) -> str:
    """The address without the credentials: a log is not the place for a password."""
    return dsn.split("@")[-1] or "the configured database"


def _disclosed(value: object) -> object:
    """A configured value as the report shows it; what is empty says so."""
    if isinstance(value, tuple):
        return [str(item) for item in value]
    if value is None or value == "":
        return NOT_CONFIGURED
    return value


def _retention(settings: Settings) -> list[dict[str, object]]:
    """How long data stays, said only where the configuration actually says it."""
    return [
        {
            "subject": "uploaded files",
            "window": f"{settings.upload_ttl_seconds:g} seconds, then swept away",
            "size_limit": f"{settings.upload_max_bytes} bytes",
            "set_by": variable_of(Settings, "upload_ttl_seconds"),
        },
        {
            "subject": "conversation history",
            "window": (
                f"kept by the memory service; an expiry is {NOT_CONFIGURED} here"
                if settings.memory_service_url
                else "in this process only: lost when it restarts"
            ),
            "set_by": variable_of(Settings, "memory_service_url"),
        },
        {
            "subject": "operational logs",
            "window": (
                "the most recent lines only, in the shared database"
                if settings.postgres_dsn
                else "the most recent lines only, in this process"
            ),
            "set_by": variable_of(Settings, "postgres_dsn"),
        },
    ]


def transparency_report(settings: Settings | None = None) -> dict[str, object]:
    """What this deployment is actually running, served at `GET /transparency`.

    The values this process was configured with, not the defaults: which model
    is answering, through which provider, how long data stays. Disclosure is
    an allowlist (`DISCLOSED_FIELDS`), never a credential, never an internal
    address: subagents and MCP servers appear by name only.
    """
    settings = settings or get_settings()
    sections: dict[str, list[dict[str, object]]] = {}
    for name in Settings.model_fields:
        section = DISCLOSED_FIELDS.get(name)
        if section is None or name in CREDENTIAL_FIELDS:
            continue
        sections.setdefault(section, []).append(
            {
                "variable": variable_of(Settings, name),
                "value": _disclosed(getattr(settings, name)),
                "what_it_decides": FIELD_NOTES.get(name, ""),
            }
        )
    return {
        "notice": (
            "Transparency about the AI system serving this endpoint, generated "
            "from the configuration this process is running with."
        ),
        "configuration": sections,
        "subagents": [agent.name for agent in settings.subagents],
        "mcp_servers": [server.name for server in settings.mcp_servers],
        "tools_requiring_approval": list(settings.tools_requiring_approval),
        "retention": _retention(settings),
        "withheld": {
            "variables": sorted(variable_of(Settings, n) for n in CREDENTIAL_FIELDS),
            "note": "Credentials and internal addresses are named, never valued.",
        },
    }


def env_table() -> str:
    """The environment table, generated from the fields themselves."""
    return render_env_table(Settings, FIELD_NOTES)


if __name__ == "__main__":
    print(env_table())
