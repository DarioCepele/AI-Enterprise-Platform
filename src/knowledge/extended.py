"""The extended card, and who is entitled to see it."""
from __future__ import annotations

import logging
import os
from collections.abc import Sequence
from hmac import compare_digest

from a2a.server.context import ServerCallContext
from a2a.types import AgentCard, AgentSkill
from a2a.utils.errors import ExtendedAgentCardNotConfiguredError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

SCHEME = "service"
PREFIX = "bearer "
PROTECTED_PATHS = ("/extendedAgentCard", "/v1/extendedAgentCard")


def expected_token() -> str:
    return os.getenv("KNOWLEDGE_SERVICE_TOKEN", "")


def token_of_request(headers: dict[str, str]) -> str:
    value = ""
    for name, content in headers.items():
        if name.lower() == "authorization":
            value = content
            break
    if not value.lower().startswith(PREFIX):
        return ""
    return value[len(PREFIX) :].strip()


def headers_are_valid(headers: dict[str, str]) -> bool:
    expected = expected_token()
    received = token_of_request(headers)
    return bool(expected) and bool(received) and compare_digest(received, expected)


def authenticated(context: ServerCallContext | None) -> bool:
    return headers_are_valid((context.state.get("headers") if context else None) or {})


class ServiceTokenOnly(BaseHTTPMiddleware):
    """401 on the extended card's REST path, telling the caller how to authenticate.

    It serves a legitimate client, which learns from the 401 which scheme to
    use; the rest of the API is untouched, because the public agent stays
    public.
    """

    async def dispatch(self, request: Request, call_next):
        if request.url.path in PROTECTED_PATHS and not headers_are_valid(
            dict(request.headers)
        ):
            logger.warning(
                "Extended card refused on %s: token missing or invalid.", request.url.path
            )
            return JSONResponse(
                {"error": "a service token is required"},
                status_code=401,
                headers={"WWW-Authenticate": f'Bearer realm="{SCHEME}"'},
            )
        return await call_next(request)


def catalogue_skill(documents: Sequence[str]) -> AgentSkill:
    return AgentSkill(
        id="catalogue",
        name="Document catalogue",
        description=(
            "Lists the indexed documents and allows citing them by name: "
            + ", ".join(documents)
        ),
        tags=["catalogue", "internal"],
    )


def build_extended_card(public: AgentCard, documents: Sequence[str]) -> AgentCard:
    """The public card plus what does not go in the shop window.

    Which documents we have indexed tells an onlooker what the organization
    works on: exactly the kind of detail that helps whoever has to use the
    agent, and not whoever happens to walk past.
    """
    extended = AgentCard()
    extended.CopyFrom(public)
    extended.description = (
        f"{public.description} Extended view: includes the indexed catalogue."
    )
    extended.skills.append(catalogue_skill(documents))
    return extended


async def card_for_the_caller(card: AgentCard, context: ServerCallContext) -> AgentCard:
    """Serves the extended card only to a caller that authenticated.

    To everyone else the extended card does not exist, rather than existing and
    being denied: the answer is the same one an agent without an extended card
    would give, and it does not confirm to a stranger that there is more to ask
    for here. The 401 with `WWW-Authenticate` goes to whoever arrives on the
    REST path, which is where a legitimate client looks for the credentials to
    use.
    """
    if not authenticated(context):
        logger.warning("Extended card refused: service token missing or invalid.")
        raise ExtendedAgentCardNotConfiguredError(
            "Authenticated Extended Card is not configured"
        )
    logger.info("Extended card served to an authenticated caller.")
    return card
