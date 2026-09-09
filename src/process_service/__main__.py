"""Local start: uv run python -m process_service."""
from __future__ import annotations

import asyncio
import logging
import sys

import uvicorn

from .api import create_app


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    # psycopg's async driver cannot run on Windows' default ProactorEventLoop. In a
    # Linux container this never comes up; on a developer's machine it is the first
    # thing that fails, so the choice is made here instead of in a README.
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    uvicorn.run(create_app(), host="127.0.0.1", port=8300)


if __name__ == "__main__":
    main()
