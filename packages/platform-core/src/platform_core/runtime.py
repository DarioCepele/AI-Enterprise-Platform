"""Starting a service on a developer's machine, the same way for every service.

Containers get their environment from the orchestrator and start uvicorn
directly. A local start (`python -m <service>`) needs three things more, and
they belong in one place:

- the local `.env`, read here and never on import, so tests stay hermetic;
- the selector event loop on Windows: psycopg's async driver cannot run on
  the default Proactor loop, and neither can a few socket libraries;
- `uvicorn.Server` instead of `uvicorn.run`, which would build a loop of its
  own -- on Windows, exactly the one the line above exists to avoid.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import Callable
from typing import Any


def serve(
    app_factory: Callable[[], Any], *, port: int, host: str = "127.0.0.1"
) -> None:
    """Loads `.env`, builds the app, and serves it until interrupted."""
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - python-dotenv is a dev convenience
        pass
    else:
        load_dotenv()

    import uvicorn

    logging.basicConfig(level=logging.INFO)
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())  # type: ignore[attr-defined,unused-ignore]
    config = uvicorn.Config(app_factory(), host=host, port=port)
    asyncio.run(uvicorn.Server(config).serve())
