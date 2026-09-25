"""Local start: uv run python -m knowledge."""
from __future__ import annotations

import logging

import uvicorn

from .server import create_app


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(create_app(), host="127.0.0.1", port=8200)


if __name__ == "__main__":
    main()
