"""Which scope a request belongs to, and how every part of a run finds out.

The scope is the authorization boundary: threads, memories, durable facts and
processes all live inside one. This template serves a single tenant by
default, so the answer is a configured constant -- but it is a **function of
the request**, because that is what authentication replaces.

Replacing the function is enough, and this module is what makes that true:
`ScopeMiddleware` resolves the scope once per request and puts it in a context
variable, and everything that needs it during the run -- the snapshot store,
the memory and process tools, the webhook a subagent is given -- reads
`scope_of_run()` instead of a value fixed when the agent was built.
"""

from __future__ import annotations

import logging
import re
from contextvars import ContextVar
from typing import Any, Protocol

from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from ..config import get_settings

logger = logging.getLogger(__name__)

VALID_SCOPE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$")

current_scope: ContextVar[str | None] = ContextVar("current_scope", default=None)


class ScopeResolver(Protocol):
    def __call__(self, request: Any) -> str: ...


def scope_of_request(request: Any) -> str:
    """The configured scope, unless a header has been declared trusted.

    `MASTER_SCOPE_HEADER` is empty by default, and until it is set the header
    is ignored: a value nobody verified is a request from the client, not an
    identity. Setting it means something in front -- a gateway, a proxy that
    authenticates -- is responsible for that header, which is exactly the
    shape the real thing will have.
    """
    settings = get_settings()
    header = settings.scope_header
    if not header:
        return settings.default_scope

    received = (getattr(request, "headers", {}) or {}).get(header) or ""
    scope = received.strip()
    if not scope:
        return settings.default_scope
    if not VALID_SCOPE.match(scope):
        logger.warning(
            "Scope '%s' refused: it is not a name. Falling back to the default one.",
            scope[:40],
        )
        return settings.default_scope
    return scope


def scope_of_run() -> str:
    """The scope of the request being served, or the default outside a request."""
    return current_scope.get() or get_settings().default_scope


class ScopeMiddleware:
    """Resolves the scope once per request and makes it visible to the whole run.

    A pure ASGI middleware: the context variable it sets is inherited by the
    endpoint, by the streaming response, and by the tool calls the agent makes
    while the response streams.
    """

    def __init__(self, app: ASGIApp, *, resolver: ScopeResolver) -> None:
        self.app = app
        self.resolver = resolver

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        token = current_scope.set(self.resolver(Request(scope)))
        try:
            await self.app(scope, receive, send)
        finally:
            current_scope.reset(token)
