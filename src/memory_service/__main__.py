"""Local entry point: uv run python -m memory_service."""
from __future__ import annotations

import asyncio
import logging
import sys

import uvicorn

from .api import create_app
from .config import get_settings


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    # psycopg's async driver cannot run on Windows' default ProactorEventLoop.
    # In a Linux container this never comes up; on a developer's machine it is
    # the first thing that fails, so the choice is made here and not in a README.
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    # Not `uvicorn.run`: it builds a loop of its own, which on Windows is the
    # very loop the line above exists to avoid -- and then nothing can reach
    # the database.
    config = uvicorn.Config(create_app(), host="127.0.0.1", port=get_settings().port)
    asyncio.run(uvicorn.Server(config).serve())


if __name__ == "__main__":
    main()
