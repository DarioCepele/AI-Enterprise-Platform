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

    # `demo-master-agent`'s own entrypoint (`src/demo/__main__.py`) runs
    # `uvicorn.run(create_app(), host="127.0.0.1", port=8000)`: 8000 is its
    # real default, not a guess. Its AG-UI endpoint lives at `/agui` on that
    # base URL (see `demo-master-agent/src/demo/server/app.py`); the path is
    # appended by `voice_service.agui_client`, not part of this setting.
    master_agent_url: str = Field(default="http://127.0.0.1:8000")

    # Endpointing thresholds (Tappa 2 Step 3 of the plan): these mirror the
    # values that used to be hardcoded as `ANALYSIS_WINDOW_S`/
    # `SILENCE_THRESHOLD_S` in `voice_service.pipeline` -- same defaults,
    # now overridable without editing that module. `voice_service.api` reads
    # them and forwards them into `voice_service.pipeline.build_voice_pipeline`.
    #
    # `analysis_window_s`: how much audio `TurnEndpointingProcessor` feeds
    # `voice_service.vad.contains_speech` at a time. See that module's
    # docstring for why 320ms (above Silero's own 250ms minimum speech
    # duration).
    analysis_window_s: float = Field(default=0.32, gt=0)
    # `silence_threshold_s`: how much trailing silence after speech ends a
    # turn. A silence-only threshold cuts a turn at any long-enough pause,
    # including one in the middle of what a person would call one sentence
    # -- a known, accepted limitation for this stage (semantic turn-detection
    # is explicitly out of scope for Tappa 2, see the plan's Fonti and
    # `tests/test_endpointing.py`), not something this setting fixes.
    silence_threshold_s: float = Field(default=0.6, gt=0)


def get_settings() -> Settings:
    """Builds the Settings from the environment. No cache: tests change the env."""
    return Settings()
