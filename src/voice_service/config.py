"""Configuration read from environment variables."""
from __future__ import annotations

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()


class Settings(BaseSettings):
    """Everything the voice service reads from the environment."""

    model_config = SettingsConfigDict(env_prefix="VOICE_", extra="ignore", frozen=True)

    port: int = Field(default=8500, ge=1, le=65535)
    json_logs: bool = Field(default=False)


def get_settings() -> Settings:
    """Builds the Settings from the environment. No cache: tests change the env."""
    return Settings()
