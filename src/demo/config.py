"""Configuration read from environment variables.

One structure, read in one place. What the product is called, which language it
answers in and which scope it serves are configuration, not constants buried in
a prompt or in a component: whoever forks this repository changes them here.
"""

from __future__ import annotations

import json
from typing import Annotated

from dotenv import load_dotenv
from pydantic import AliasChoices, BaseModel, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

load_dotenv()

DEFAULT_ORIGINS = (
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:3001",
    "http://127.0.0.1:3001",
)

SINGLE_TENANT_SCOPE = "local-laboratory"


class SubagentConfig(BaseModel):
    """A remote agent this one may call, named as the model will see it."""

    name: str
    url: str
    token: str = ""

    @field_validator("name", mode="after")
    @classmethod
    def _identifier(cls, value: str) -> str:
        """The name becomes part of a tool name, so it has to be one."""
        cleaned = "".join(
            char if char.isalnum() else "_" for char in value.strip().lower()
        )
        if not cleaned or not cleaned[0].isalpha():
            raise ValueError(f"subagent name '{value}' is not usable as a tool name")
        return cleaned


class MCPServerConfig(BaseModel):
    """An external MCP server this agent may draw tools from.

    `name` becomes the `tool_name_prefix` handed to `MCPStreamableHTTPTool`,
    so two servers exposing a same-named tool (e.g. both offering
    `search`) do not collide.
    """

    name: str
    url: str
    token: str = ""
    allowed_tools: tuple[str, ...] = ()

    @field_validator("name", mode="after")
    @classmethod
    def _identifier(cls, value: str) -> str:
        cleaned = "".join(
            char if char.isalnum() else "_" for char in value.strip().lower()
        )
        if not cleaned or not cleaned[0].isalpha():
            raise ValueError(
                f"MCP server name '{value}' is not usable as a tool prefix"
            )
        return cleaned


class Settings(BaseSettings):
    """Everything the process reads from the environment, validated at once."""

    # The .env file is loaded into the environment once, above: reading it here
    # too would make the file win over a variable a test just removed.
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
        default="qwen/qwen3.8-27b",
        validation_alias=AliasChoices("DEMO_VISION_MODEL"),
    )
    use_fake_client: bool = Field(
        default=False, validation_alias=AliasChoices("DEMO_FAKE_CLIENT")
    )
    allowed_origins: Annotated[tuple[str, ...], NoDecode] = Field(
        default=DEFAULT_ORIGINS, validation_alias=AliasChoices("DEMO_ALLOWED_ORIGINS")
    )

    product_name: str = Field(
        default="AG-UI Lab", validation_alias=AliasChoices("DEMO_PRODUCT_NAME")
    )
    product_language: str = Field(
        default="Italian", validation_alias=AliasChoices("DEMO_PRODUCT_LANGUAGE")
    )
    default_scope: str = Field(
        default=SINGLE_TENANT_SCOPE, validation_alias=AliasChoices("DEMO_DEFAULT_SCOPE")
    )
    scope_header: str = Field(
        default="", validation_alias=AliasChoices("DEMO_SCOPE_HEADER")
    )
    json_logs: bool = Field(
        default=False, validation_alias=AliasChoices("DEMO_JSON_LOGS")
    )

    memory_service_url: str = Field(
        default="", validation_alias=AliasChoices("DEMO_MEMORY_SERVICE_URL")
    )
    postgres_dsn: str = Field(
        default="", validation_alias=AliasChoices("DEMO_POSTGRES_DSN")
    )
    knowledge_agent_url: str = Field(
        default="", validation_alias=AliasChoices("DEMO_KNOWLEDGE_AGENT_URL")
    )
    knowledge_service_token: str = Field(
        default="", validation_alias=AliasChoices("DEMO_KNOWLEDGE_SERVICE_TOKEN")
    )
    subagents: Annotated[tuple[SubagentConfig, ...], NoDecode] = Field(
        default=(), validation_alias=AliasChoices("DEMO_SUBAGENTS")
    )
    public_url: str = Field(
        default="", validation_alias=AliasChoices("DEMO_PUBLIC_URL")
    )
    process_service_url: str = Field(
        default="", validation_alias=AliasChoices("DEMO_PROCESS_SERVICE_URL")
    )
    voice_service_url: str = Field(
        default="", validation_alias=AliasChoices("DEMO_VOICE_SERVICE_URL")
    )
    subagent_wait_seconds: float = Field(
        default=60.0, validation_alias=AliasChoices("DEMO_SUBAGENT_WAIT_SECONDS")
    )
    upload_dir: str = Field(
        default="", validation_alias=AliasChoices("DEMO_UPLOAD_DIR")
    )
    upload_max_bytes: int = Field(
        default=200 * 1024 * 1024,
        validation_alias=AliasChoices("DEMO_UPLOAD_MAX_BYTES"),
    )
    upload_ttl_seconds: float = Field(
        default=3600.0, validation_alias=AliasChoices("DEMO_UPLOAD_TTL_SECONDS")
    )
    mcp_servers: Annotated[tuple[MCPServerConfig, ...], NoDecode] = Field(
        default=(), validation_alias=AliasChoices("DEMO_MCP_SERVERS")
    )

    @field_validator(
        "base_url",
        "api_key",
        "model",
        "vision_model",
        "product_name",
        "product_language",
        "default_scope",
        "scope_header",
        "memory_service_url",
        "postgres_dsn",
        "knowledge_agent_url",
        "knowledge_service_token",
        "public_url",
        "process_service_url",
        "voice_service_url",
        "upload_dir",
        mode="after",
    )
    @classmethod
    def _trimmed(cls, value: str) -> str:
        return value.strip()

    @field_validator("subagents", mode="before")
    @classmethod
    def _subagents(cls, value: object) -> object:
        """A JSON list, or nothing. An empty variable means nothing, not an error."""
        if not isinstance(value, str):
            return value
        text = value.strip()
        if not text:
            return ()
        return json.loads(text)

    @field_validator("mcp_servers", mode="before")
    @classmethod
    def _mcp_servers(cls, value: object) -> object:
        """A JSON list, or nothing. An empty variable means nothing, not an error."""
        if not isinstance(value, str):
            return value
        text = value.strip()
        if not text:
            return ()
        return json.loads(text)

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def _origins(cls, value: object) -> object:
        """A comma-separated list, because that is what an environment holds."""
        if not isinstance(value, str):
            return value
        origins = tuple(part.strip() for part in value.split(",") if part.strip())
        return origins or DEFAULT_ORIGINS

    @model_validator(mode="after")
    def _knowledge_agent_is_a_subagent(self) -> Settings:
        """The single-agent variables stay valid, as one entry in the list.

        A fork that only has one subagent should not have to learn a JSON list
        to say so, and the compose file that has been passing
        DEMO_KNOWLEDGE_AGENT_URL for three stages keeps working.
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

    def require_model_access(self) -> None:
        """Fails now, with the name of what is missing, instead of at the first turn.

        A model without credentials answers nothing, and the error arrives when
        a person is already waiting: better to refuse to start.
        """
        if self.use_fake_client:
            return
        if not self.api_key:
            raise ValueError(
                "OPENAI_API_KEY is empty: set it, or set DEMO_FAKE_CLIENT=true to run "
                "without a model."
            )


def get_settings() -> Settings:
    """Builds the Settings from the environment. No cache: tests change the env."""
    return Settings()


# What each variable decides, next to the fields it decides it for. The table in
# the README is generated from here: one written by hand is one that lies after
# the second change.
FIELD_NOTES = {
    "base_url": "Where the model lives. Any OpenAI-compatible endpoint.",
    "api_key": "Credential for that endpoint. Required unless the fake client is on.",
    "model": "Model the master agent talks to.",
    "vision_model": (
        "Model the analyze_video tool talks to for frame description, kept separate "
        "from the conversation's own model since it must accept images natively."
    ),
    "use_fake_client": (
        "Deterministic answers without a model. For tests and offline work."
    ),
    "allowed_origins": "Comma-separated origins allowed by CORS.",
    "product_name": "What the agent calls itself in its own instructions.",
    "product_language": "Language the agent answers in.",
    "default_scope": "Authorization boundary used when nothing else says otherwise.",
    "scope_header": (
        "Header carrying the scope, read **only** when this is set: naming it "
        "means something in front has verified it."
    ),
    "json_logs": "Structured logs for a collector instead of the readable line.",
    "memory_service_url": (
        "Memory service. Without it the conversation lives in RAM and dies "
        "with the process."
    ),
    "postgres_dsn": (
        "Shared logs and deduplicated notifications. Without it both are per replica."
    ),
    "knowledge_agent_url": (
        "One subagent, the short way. Ignored when DEMO_SUBAGENTS is set."
    ),
    "process_service_url": (
        "Where durable processes live. Empty: the agent cannot start one."
    ),
    "voice_service_url": (
        "demo-voice-service, for its POST /transcribe. Empty: the "
        "video-analysis tool is not shown."
    ),
    "knowledge_service_token": "Service token of that subagent, for its extended card.",
    "subagents": 'Subagents as JSON: [{"name":"x","url":"http://...","token":""}].',
    "public_url": "How a subagent reaches this agent back, for push notifications.",
    "subagent_wait_seconds": (
        "How long a turn waits before letting the outcome arrive by notification."
    ),
    "upload_dir": ("Folder for ephemeral video uploads. Empty: the OS temp folder."),
    "upload_max_bytes": "Largest accepted upload for /uploads. Bigger is refused.",
    "upload_ttl_seconds": (
        "How long an uploaded file stays fetchable before it is swept away."
    ),
    "mcp_servers": (
        'External MCP servers as JSON: [{"name":"x","url":"http://...",'
        '"token":"","allowed_tools":[]}]. Each server\'s tools are added to the '
        "agent's own, connected lazily on first use."
    ),
}


NOT_CONFIGURED = "not configured by this deployment"

# Never read for their value, only named: a report that reads the running
# configuration has every credential within reach, so the ones that are
# credentials are listed once, here, and skipped wherever fields are iterated.
CREDENTIAL_FIELDS = frozenset({"api_key", "knowledge_service_token", "postgres_dsn"})

# What this deployment discloses about itself, and under which heading. An
# allowlist rather than a list of exclusions: a field added to Settings
# tomorrow stays out of the report until someone decides it may be shown, which
# is the safe direction to fail in when the next field is a token.
DISCLOSED_FIELDS = {
    "product_name": "identity",
    "product_language": "identity",
    "model": "models",
    "vision_model": "models",
    "use_fake_client": "models",
    "base_url": "providers",
    "voice_service_url": "providers",
    "process_service_url": "providers",
    "memory_service_url": "data",
    "default_scope": "boundaries",
    "scope_header": "boundaries",
}


def host_of_dsn(dsn: str) -> str:
    """L'indirizzo senza le credenziali: un log non e' il posto per una password.

    Nemmeno un report di trasparenza lo e': la stessa regola serve i log di
    avvio e `transparency_report()`, e vive qui per essere una sola.
    """
    return dsn.split("@")[-1] or "the configured database"


def _alias(name: str) -> str:
    """The environment variable that sets a field, as the field declares it."""
    field = Settings.model_fields[name]
    if isinstance(field.validation_alias, AliasChoices):
        return str(next(iter(field.validation_alias.choices)))
    return name.upper()


def _disclosed(value: object) -> object:
    """A configured value as the report shows it; what is empty says so."""
    if isinstance(value, tuple):
        return [str(item) for item in value]
    if value is None or value == "":
        return NOT_CONFIGURED
    return value


def _retention(settings: Settings) -> list[dict[str, object]]:
    """How long data stays, said only where the configuration actually says it.

    The upload TTL is the one window this deployment really has. For the
    conversation and the operational logs nothing is configured, and the report
    says exactly that: a number written here would be publishing a retention
    policy this code has no authority to decide.
    """
    memory = settings.memory_service_url
    return [
        {
            "subject": "uploaded video files",
            "window": f"{settings.upload_ttl_seconds:g} seconds, then swept away",
            "size_limit": f"{settings.upload_max_bytes} bytes",
            "set_by": _alias("upload_ttl_seconds"),
        },
        {
            "subject": "conversation history",
            "window": (
                f"kept by the memory service at {memory}; an expiry is {NOT_CONFIGURED}"
                if memory
                else "in this process only: lost when it restarts"
            ),
            "set_by": _alias("memory_service_url"),
        },
        {
            "subject": "operational logs",
            "window": (
                f"kept in the shared database; an expiry is {NOT_CONFIGURED}"
                if settings.postgres_dsn
                else "in this process only: lost when it restarts"
            ),
            "set_by": _alias("postgres_dsn"),
        },
    ]


def transparency_report(settings: Settings | None = None) -> dict[str, object]:
    """What this deployment is actually running, for AI Act art. 50 disclosure.

    Sibling of `env_table()` and deliberately not the same thing. That one
    documents the *schema*: it prints `field.get_default(...)` for every
    variable and never looks at an instance, which is why it is safe to publish
    as is -- a default is public by definition. This one reports the values
    this process was *configured* with: which model is answering here, through
    which endpoint, where the conversation is kept. Article 50 asks what is
    happening, not what would happen if nobody had configured anything, so a
    report built from defaults would be true of the repository and false of the
    deployment.

    Reading real values puts credentials in reach, so disclosure is an
    allowlist (`DISCLOSED_FIELDS`) and never `CREDENTIAL_FIELDS`; the tokens
    inside `subagents` and `mcp_servers` are not read either, and
    `postgres_dsn` appears only as the host `host_of_dsn()` already logs.
    """
    settings = settings or get_settings()
    sections: dict[str, list[dict[str, object]]] = {}
    for name in Settings.model_fields:
        section = DISCLOSED_FIELDS.get(name)
        if section is None or name in CREDENTIAL_FIELDS:
            continue
        sections.setdefault(section, []).append(
            {
                "variable": _alias(name),
                "value": _disclosed(getattr(settings, name)),
                "what_it_decides": FIELD_NOTES.get(name, ""),
            }
        )
    sections.setdefault("data", []).append(
        {
            "variable": _alias("postgres_dsn"),
            # The host, never the DSN: it carries the password.
            "value": (
                host_of_dsn(settings.postgres_dsn)
                if settings.postgres_dsn
                else NOT_CONFIGURED
            ),
            "what_it_decides": FIELD_NOTES.get("postgres_dsn", ""),
        }
    )
    return {
        "notice": (
            "Transparency about the AI system serving this endpoint, generated "
            "from the configuration this process is running with."
        ),
        "configuration": sections,
        "subagents": [
            {"name": agent.name, "url": agent.url} for agent in settings.subagents
        ],
        "mcp_servers": [
            {
                "name": server.name,
                "url": server.url,
                "allowed_tools": list(server.allowed_tools),
            }
            for server in settings.mcp_servers
        ],
        "retention": _retention(settings),
        "withheld": {
            "variables": sorted(_alias(name) for name in CREDENTIAL_FIELDS),
            "note": (
                "Also withheld: the token of every entry in "
                f"{_alias('subagents')} and {_alias('mcp_servers')}. Credentials "
                "are named here, never valued."
            ),
        },
    }


def env_table() -> str:
    """The environment table, generated from the fields themselves."""
    rows = ["| Variable | Default | What it decides |", "|---|---|---|"]
    for name, field in Settings.model_fields.items():
        alias = (
            next(iter(field.validation_alias.choices))
            if isinstance(field.validation_alias, AliasChoices)
            else name.upper()
        )
        default = field.get_default(call_default_factory=True)
        if isinstance(default, tuple):
            default = ", ".join(str(item) for item in default) or ""
        shown = f"`{default}`" if default not in ("", None) else "*(empty)*"
        rows.append(f"| `{alias}` | {shown} | {FIELD_NOTES.get(name, '')} |")
    return "\n".join(rows)


if __name__ == "__main__":
    print(env_table())
