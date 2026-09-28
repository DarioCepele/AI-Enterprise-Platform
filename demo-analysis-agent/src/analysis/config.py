"""Configuration read from environment variables, validated in one place.

What every A2A agent of the platform reads lives in `platform_core.settings`;
this agent adds nothing but its prefix. The README table is generated from the
fields (`env_table`): one written by hand is one that lies after the second
change.
"""

from __future__ import annotations

from platform_core.settings import A2A_AGENT_NOTES, A2AAgentSettings
from platform_core.settings import env_table as render_env_table
from pydantic import Field
from pydantic_settings import SettingsConfigDict


class Settings(A2AAgentSettings):
    """Everything the analysis agent reads from the environment."""

    model_config = SettingsConfigDict(
        env_prefix="ANALYSIS_", extra="ignore", frozen=True
    )

    base_url: str = Field(default="http://localhost:8400/")


def get_settings() -> Settings:
    """Builds the Settings from the environment. No cache: tests change the env."""
    return Settings()


FIELD_NOTES = {
    **A2A_AGENT_NOTES,
    "model": "The model this agent reasons with. It computes with tools, not with it.",
}


def env_table() -> str:
    return render_env_table(Settings, FIELD_NOTES)


if __name__ == "__main__":
    print(env_table())
