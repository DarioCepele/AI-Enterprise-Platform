"""Which scope a request belongs to.

The scope is the authorization boundary: threads, memories and durable facts
all live inside one. This laboratory serves a single tenant, so the answer is a
configured constant -- but it is a **function of the request**, because that is
what authentication replaces. Whoever adds OIDC swaps this implementation and
touches nothing else.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Protocol

from ..config import get_settings

logger = logging.getLogger(__name__)

VALID_SCOPE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$")


class ScopeResolver(Protocol):
    def __call__(self, request: Any) -> str: ...


def scope_of_request(request: Any) -> str:
    """The configured scope, unless a header has been declared trusted.

    `DEMO_SCOPE_HEADER` is empty by default, and until it is set the header is
    ignored: a value nobody verified is a request from the client, not an
    identity. Setting it means something in front -- a gateway, a proxy that
    authenticates -- is responsible for that header, which is exactly the shape
    the real thing will have.
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
