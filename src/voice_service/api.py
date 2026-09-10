"""The HTTP surface of the voice service: health probes, for now.

This is the scaffold laid down before the Pipecat pipeline (WebSocket audio
in, VAD, STT, a turn handed to `demo-master-agent`'s AG-UI endpoint, TTS,
audio out) is built on top of it. Nothing here depends on Pipecat, a speech
backend, or a store: the only thing this service can currently fail to serve
is itself, so liveness and readiness answer the same way. Once a real
dependency (STT/TTS, a session store) lands, `/health/ready` should start
checking it -- the way `process-service` and `memory-service` check Postgres
before answering ok.
"""
from __future__ import annotations

import logging

from fastapi import FastAPI

from .config import Settings, get_settings

logger = logging.getLogger(__name__)

SERVICE_NAME = "voice-service"


def create_app(settings: Settings | None = None) -> FastAPI:
    """Builds the app. `settings` is passed in tests."""
    config = settings or get_settings()

    app = FastAPI(title="Voice service")

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        """Whether this process is stuck. Nothing behind this call can fail yet."""
        return {"status": "alive"}

    @app.get("/health")
    @app.get("/health/ready")
    async def ready() -> dict[str, str]:
        """Whether it can serve.

        No backend to check yet -- this scaffold has none. This is the seam
        a future dependency plugs into, not a promise that there is nothing
        to check.
        """
        return {"status": "ok"}

    logger.info("Voice service ready on port %d.", config.port)
    return app
