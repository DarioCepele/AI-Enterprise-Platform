"""Lettura della configurazione da variabili d'ambiente."""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


# Il dev server di Next slitta sulla porta successiva se la 3000 e' occupata,
# quindi il default copre anche la 3001. Sovrascrivibile con DEMO_ALLOWED_ORIGINS.
_DEFAULT_ORIGINS = (
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:3001",
    "http://127.0.0.1:3001",
)


@dataclass(frozen=True)
class Settings:
    base_url: str
    api_key: str
    model: str
    use_fake_client: bool
    allowed_origins: tuple[str, ...]


def _read_origins() -> tuple[str, ...]:
    raw = os.getenv("DEMO_ALLOWED_ORIGINS", "")
    if not raw.strip():
        return _DEFAULT_ORIGINS
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def get_settings() -> Settings:
    """Costruisce le Settings dall'ambiente. Nessuna cache: i test cambiano l'env."""
    return Settings(
        base_url=os.getenv("OPENAI_BASE_URL", "https://openrouter.ai/api/v1"),
        api_key=os.getenv("OPENAI_API_KEY", ""),
        model=os.getenv("OPENAI_CHAT_COMPLETION_MODEL", "anthropic/claude-sonnet-5"),
        use_fake_client=os.getenv("DEMO_FAKE_CLIENT", "false").lower() == "true",
        allowed_origins=_read_origins(),
    )
