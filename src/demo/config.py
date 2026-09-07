"""Lettura della configurazione da variabili d'ambiente."""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    base_url: str
    api_key: str
    model: str
    use_fake_client: bool


def get_settings() -> Settings:
    """Costruisce le Settings dall'ambiente. Nessuna cache: i test cambiano l'env."""
    return Settings(
        base_url=os.getenv("OPENAI_BASE_URL", "https://openrouter.ai/api/v1"),
        api_key=os.getenv("OPENAI_API_KEY", ""),
        model=os.getenv("OPENAI_CHAT_COMPLETION_MODEL", "anthropic/claude-sonnet-5"),
        use_fake_client=os.getenv("DEMO_FAKE_CLIENT", "false").lower() == "true",
    )
