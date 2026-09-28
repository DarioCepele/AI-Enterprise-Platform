"""A ceiling on what a client may send, whether it declares the size or not.

Checking `Content-Length` alone is a lock on the front door with the back door
open: a chunked request declares nothing and streams whatever it likes. This
middleware counts the bytes as they arrive and stops the request the moment it
crosses the limit, so no handler ever holds more than the ceiling.

A pure ASGI middleware, not a `BaseHTTPMiddleware`: it wraps `receive`, which
is the only place a streamed body can be counted, and it keeps context
variables flowing into the application untouched.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping

from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)


class _TooLarge(HTTPException):
    """Raised from `receive` the moment the body crosses the ceiling.

    An `HTTPException`, so that a Starlette or FastAPI application turns it
    into a 413 through its own exception handling -- the request stops there,
    whatever the handler was doing with the body. Anything else that lets it
    escape is answered here.
    """

    def __init__(self, limit: int) -> None:
        super().__init__(
            status_code=413, detail=f"request too large: over {limit} bytes"
        )


async def _reject(send: Send, status: int, detail: str) -> None:
    body = json.dumps({"detail": detail}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class BodySizeLimit:
    """Refuses bodies over `default` bytes, or over the limit of a path prefix."""

    def __init__(
        self, app: ASGIApp, *, default: int, by_prefix: Mapping[str, int] | None = None
    ) -> None:
        self.app = app
        self.default = default
        # Longest prefix first, so `/uploads/big` is not judged by `/uploads`.
        self.by_prefix = sorted(
            (by_prefix or {}).items(), key=lambda item: -len(item[0])
        )

    def limit_for(self, path: str) -> int:
        for prefix, limit in self.by_prefix:
            if path.startswith(prefix):
                return limit
        return self.default

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        limit = self.limit_for(scope.get("path", ""))
        declared = next(
            (
                value
                for name, value in scope.get("headers", [])
                if name == b"content-length"
            ),
            None,
        )
        if declared is not None:
            try:
                size = int(declared)
                if size < 0:
                    raise ValueError(size)
            except ValueError:
                await _reject(send, 400, "malformed Content-Length")
                return
            if size > limit:
                logger.warning(
                    "Request refused: %d bytes declared, limit %d.", size, limit
                )
                await _reject(send, 413, f"request too large: over {limit} bytes")
                return

        received = 0
        started = False

        async def counting_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise _TooLarge(limit)
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, counting_receive, tracking_send)
        except _TooLarge:
            logger.warning("Request refused mid-stream: over %d bytes.", limit)
            if not started:
                await _reject(send, 413, f"request too large: over {limit} bytes")
