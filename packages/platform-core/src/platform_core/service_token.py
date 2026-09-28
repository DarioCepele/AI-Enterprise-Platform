"""The extended agent card, and who is entitled to see it.

An A2A agent is public; its extended card -- the catalogue behind the skill --
is for callers that present the service token. To everyone else the extended
card does not exist, rather than existing and being denied: the answer is the
one an agent without an extended card would give. The REST path answers 401
with `WWW-Authenticate`, which is where a legitimate client learns what to send.

A token that is not configured closes the door: forgetting it must cost a card,
not expose one. Requires `a2a-sdk` (the `a2a` extra).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable, Mapping
from hmac import compare_digest

from a2a.server.context import ServerCallContext
from a2a.types import AgentCard
from a2a.utils.errors import ExtendedAgentCardNotConfiguredError
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

logger = logging.getLogger(__name__)

SCHEME = "service"
PREFIX = "bearer "
PROTECTED_PATHS = ("/extendedAgentCard", "/v1/extendedAgentCard")


class ServiceToken:
    """The token an agent expects, read from the environment when it is checked."""

    def __init__(self, variable: str) -> None:
        self.variable = variable

    def expected(self) -> str:
        return os.getenv(self.variable, "")

    @staticmethod
    def presented(headers: Mapping[str, str]) -> str:
        value = next(
            (
                content
                for name, content in headers.items()
                if name.lower() == "authorization"
            ),
            "",
        )
        if not value.lower().startswith(PREFIX):
            return ""
        return value[len(PREFIX) :].strip()

    def headers_are_valid(self, headers: Mapping[str, str]) -> bool:
        expected = self.expected()
        received = self.presented(headers)
        return bool(expected) and bool(received) and compare_digest(received, expected)

    def authenticated(self, context: ServerCallContext | None) -> bool:
        headers = (context.state.get("headers") if context else None) or {}
        return self.headers_are_valid(headers)

    def middleware(self) -> Callable[[ASGIApp], BaseHTTPMiddleware]:
        """The REST guard: 401 on the extended card's path, nothing else touched."""
        token = self

        class ServiceTokenOnly(BaseHTTPMiddleware):
            async def dispatch(
                self, request: Request, call_next: RequestResponseEndpoint
            ) -> Response:
                if request.url.path in PROTECTED_PATHS and not token.headers_are_valid(
                    dict(request.headers)
                ):
                    logger.warning(
                        "Extended card refused on %s: token missing or invalid.",
                        request.url.path,
                    )
                    return JSONResponse(
                        {"error": "a service token is required"},
                        status_code=401,
                        headers={"WWW-Authenticate": f'Bearer realm="{SCHEME}"'},
                    )
                return await call_next(request)

        return ServiceTokenOnly

    def card_modifier(
        self,
    ) -> Callable[[AgentCard, ServerCallContext], Awaitable[AgentCard]]:
        """The JSON-RPC side: the extended card only for a caller that authenticated."""
        token = self

        async def card_for_the_caller(
            card: AgentCard, context: ServerCallContext
        ) -> AgentCard:
            if not token.authenticated(context):
                logger.warning(
                    "Extended card refused: service token missing or invalid."
                )
                raise ExtendedAgentCardNotConfiguredError(
                    "Authenticated Extended Card is not configured"
                )
            logger.info("Extended card served to an authenticated caller.")
            return card

        return card_for_the_caller
