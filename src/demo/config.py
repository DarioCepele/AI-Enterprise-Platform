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
        cleaned = "".join(char if char.isalnum() else "_" for char in value.strip().lower())
        if not cleaned or not cleaned[0].isalpha():
            raise ValueError(f"subagent name '{value}' is not usable as a tool name")
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
    json_logs: bool = Field(default=False, validation_alias=AliasChoices("DEMO_JSON_LOGS"))

    memory_service_url: str = Field(
        default="", validation_alias=AliasChoices("DEMO_MEMORY_SERVICE_URL")
    )
    postgres_dsn: str = Field(default="", validation_alias=AliasChoices("DEMO_POSTGRES_DSN"))
    knowledge_agent_url: str = Field(
        default="", validation_alias=AliasChoices("DEMO_KNOWLEDGE_AGENT_URL")
    )
    knowledge_service_token: str = Field(
        default="", validation_alias=AliasChoices("DEMO_KNOWLEDGE_SERVICE_TOKEN")
    )
    subagents: Annotated[tuple[SubagentConfig, ...], NoDecode] = Field(
        default=(), validation_alias=AliasChoices("DEMO_SUBAGENTS")
    )
    public_url: str = Field(default="", validation_alias=AliasChoices("DEMO_PUBLIC_URL"))
    process_service_url: str = Field(
        default="", validation_alias=AliasChoices("DEMO_PROCESS_SERVICE_URL")
    )
    voice_service_url: str = Field(
        default="", validation_alias=AliasChoices("DEMO_VOICE_SERVICE_URL")
    )
    subagent_wait_seconds: float = Field(
        default=60.0, validation_alias=AliasChoices("DEMO_SUBAGENT_WAIT_SECONDS")
    )
    upload_dir: str = Field(default="", validation_alias=AliasChoices("DEMO_UPLOAD_DIR"))
    upload_max_bytes: int = Field(
        default=200 * 1024 * 1024, validation_alias=AliasChoices("DEMO_UPLOAD_MAX_BYTES")
    )
    upload_ttl_seconds: float = Field(
        default=3600.0, validation_alias=AliasChoices("DEMO_UPLOAD_TTL_SECONDS")
    )

    @field_validator(
        "base_url",
        "api_key",
        "model",
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

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def _origins(cls, value: object) -> object:
        """A comma-separated list, because that is what an environment holds."""
        if not isinstance(value, str):
            return value
        origins = tuple(part.strip() for part in value.split(",") if part.strip())
        return origins or DEFAULT_ORIGINS

    @model_validator(mode="after")
    def _knowledge_agent_is_a_subagent(self) -> "Settings":
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
    "use_fake_client": "Deterministic answers without a model. For tests and offline work.",
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
        "Memory service. Without it the conversation lives in RAM and dies with the process."
    ),
    "postgres_dsn": (
        "Shared logs and deduplicated notifications. Without it both are per replica."
    ),
    "knowledge_agent_url": "One subagent, the short way. Ignored when DEMO_SUBAGENTS is set.",
    "process_service_url": "Where durable processes live. Empty: the agent cannot start one.",
    "voice_service_url": (
        "demo-voice-service, for its POST /transcribe. Empty: the video-analysis tool is not shown."
    ),
    "knowledge_service_token": "Service token of that subagent, for its extended card.",
    "subagents": 'Subagents as JSON: [{"name":"x","url":"http://...","token":""}].',
    "public_url": "How a subagent reaches this agent back, for push notifications.",
    "subagent_wait_seconds": (
        "How long a turn waits before letting the outcome arrive by notification."
    ),
    "upload_dir": (
        "Folder for ephemeral video uploads. Empty: the OS temp folder."
    ),
    "upload_max_bytes": "Largest accepted upload for /uploads. Bigger is refused.",
    "upload_ttl_seconds": (
        "How long an uploaded file stays fetchable before it is swept away."
    ),
}


def env_table() -> str:
    """The environment table, generated from the fields themselves."""
    rows = ["| Variable | Default | What it decides |", "|---|---|---|"]
    for name, field in Settings.model_fields.items():
        alias = (
            next(iter(field.validation_alias.choices))
            if field.validation_alias is not None
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
