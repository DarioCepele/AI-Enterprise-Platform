"""Configuration read from environment variables.

One structure, read in one place. What the product is called, which language it
answers in and which scope it serves are configuration, not constants buried in
a prompt or in a component: whoever forks this repository changes them here.
"""
from __future__ import annotations

from typing import Annotated

from dotenv import load_dotenv
from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

load_dotenv()

DEFAULT_ORIGINS = (
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:3001",
    "http://127.0.0.1:3001",
)

SINGLE_TENANT_SCOPE = "local-laboratory"

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

    memory_service_url: str = Field(
        default="", validation_alias=AliasChoices("DEMO_MEMORY_SERVICE_URL")
    )
    redis_uri: str = Field(default="", validation_alias=AliasChoices("DEMO_REDIS_URI"))
    knowledge_agent_url: str = Field(
        default="", validation_alias=AliasChoices("DEMO_KNOWLEDGE_AGENT_URL")
    )
    knowledge_service_token: str = Field(
        default="", validation_alias=AliasChoices("DEMO_KNOWLEDGE_SERVICE_TOKEN")
    )
    public_url: str = Field(default="", validation_alias=AliasChoices("DEMO_PUBLIC_URL"))
    subagent_wait_seconds: float = Field(
        default=60.0, validation_alias=AliasChoices("DEMO_SUBAGENT_WAIT_SECONDS")
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
        "redis_uri",
        "knowledge_agent_url",
        "knowledge_service_token",
        "public_url",
        mode="after",
    )
    @classmethod
    def _trimmed(cls, value: str) -> str:
        return value.strip()

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def _origins(cls, value: object) -> object:
        """A comma-separated list, because that is what an environment holds."""
        if not isinstance(value, str):
            return value
        origins = tuple(part.strip() for part in value.split(",") if part.strip())
        return origins or DEFAULT_ORIGINS

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
