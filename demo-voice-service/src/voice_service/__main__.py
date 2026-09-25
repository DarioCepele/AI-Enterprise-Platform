"""Local start: uv run python -m voice_service."""
from __future__ import annotations

import asyncio
import logging
import sys

import uvicorn

from .api import create_app
from .config import get_settings


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    # `demo-process-service` would not start on Windows because `uvicorn.run`
    # builds an event loop of its own, which on Windows defaults to the
    # ProactorEventLoop -- incompatible with the selector loop that service
    # needed for its Postgres driver. This scaffold has no database yet, but
    # it will carry a Pipecat pipeline (WebSocket audio, VAD, STT/TTS) that
    # needs the same selector loop for its async sockets, so the same fix is
    # applied here from the start instead of being rediscovered later.
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    # Not `uvicorn.run`: it builds a loop of its own, which on Windows would be
    # the ProactorEventLoop the line above exists to avoid.
    config = uvicorn.Config(create_app(), host="127.0.0.1", port=get_settings().port)
    asyncio.run(uvicorn.Server(config).serve())


if __name__ == "__main__":
    main()
