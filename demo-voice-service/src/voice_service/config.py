"""Configuration read from environment variables, validated in one place."""

from __future__ import annotations

from platform_core.settings import env_table as render_env_table
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from .agui_client import DEFAULT_APPROVAL_NOTICE

DEFAULT_ORIGINS = (
    "http://localhost:3000,http://127.0.0.1:3000,"
    "http://localhost:3001,http://127.0.0.1:3001"
)


class Settings(BaseSettings):
    """Everything the voice service reads from the environment."""

    model_config = SettingsConfigDict(env_prefix="VOICE_", extra="ignore", frozen=True)

    port: int = Field(default=8500, ge=1, le=65535)
    json_logs: bool = Field(default=False)

    # The master agent's AG-UI endpoint lives at `/agui` on this base URL; the
    # path is appended by `agui_client`, not part of this setting.
    master_agent_url: str = Field(default="http://127.0.0.1:8000")

    # Browsers do not apply CORS to WebSockets: any page open in the same
    # browser could otherwise talk to the agent through the microphone
    # endpoint. The Origin is checked against this list instead.
    allowed_origins: str = Field(default=DEFAULT_ORIGINS)
    transcribe_max_bytes: int = Field(default=100_000_000, ge=1_000_000)

    stt_model: str = Field(default="small")
    stt_language: str = Field(default="")
    tts_lang_code: str = Field(default="a")
    tts_voice: str = Field(default="af_heart")
    # Said when a turn asks for something that needs a person's approval: the
    # voice channel cannot show the question, so it is cancelled. In the TTS
    # language: the default is English, like the default voice.
    approval_notice: str = Field(default=DEFAULT_APPROVAL_NOTICE, min_length=1)

    # Endpointing: how much audio the VAD judges at a time (above Silero's own
    # 250ms minimum speech duration), and how much trailing silence ends a
    # turn. Silence-only endpointing also cuts at a long pause mid-sentence: a
    # known limit, not something these values fix.
    analysis_window_s: float = Field(default=0.32, gt=0)
    silence_threshold_s: float = Field(default=0.6, gt=0)

    def origins(self) -> frozenset[str]:
        return frozenset(
            part.strip().rstrip("/")
            for part in self.allowed_origins.split(",")
            if part.strip()
        )


def get_settings() -> Settings:
    """Builds the Settings from the environment. No cache: tests change the env."""
    return Settings()


FIELD_NOTES = {
    "port": "Where the service listens when started locally.",
    "json_logs": "Structured logs for a collector instead of the readable line.",
    "master_agent_url": "The master agent, whose AG-UI endpoint answers each turn.",
    "allowed_origins": (
        "Comma-separated pages allowed to open the voice WebSocket. Browsers do "
        "not apply CORS to WebSockets: this is the check that does."
    ),
    "transcribe_max_bytes": "Largest audio file `/transcribe` accepts.",
    "stt_model": (
        "faster-whisper model size (tiny, base, small, medium, large-v3), on CPU."
    ),
    "stt_language": "ISO 639-1 code of the speech. Empty: detected per turn.",
    "tts_lang_code": (
        "Kokoro language code: a (US English), b (UK English), e (Spanish), "
        "f (French), h (Hindi), i (Italian), j (Japanese), p (Portuguese), "
        "z (Chinese)."
    ),
    "tts_voice": "Kokoro voice, matching the language (af_heart, if_sara, ...).",
    "approval_notice": (
        "Said, in the TTS language, when a spoken request needs a person's "
        "approval: voice cannot ask, so the action is cancelled."
    ),
    "analysis_window_s": "Audio judged by the VAD at a time, in seconds.",
    "silence_threshold_s": "Trailing silence that ends a turn, in seconds.",
}


def env_table() -> str:
    return render_env_table(Settings, FIELD_NOTES)


if __name__ == "__main__":
    print(env_table())
