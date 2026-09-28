"""The extended card: what it says, and to whom."""
from __future__ import annotations

import httpx
import pytest
from a2a.server.context import ServerCallContext
from a2a.types import AgentCard
from a2a.utils.errors import ExtendedAgentCardNotConfiguredError

from analysis.extended import build_extended_card, card_for_the_caller
from analysis.server import build_agent_card, create_app

TOKEN = "service-token"  # noqa: S105 - fake token for tests, not a real secret
VERSION = {"A2A-Version": "1.0"}


@pytest.fixture(autouse=True)
def token(monkeypatch):
    monkeypatch.setenv("ANALYSIS_SERVICE_TOKEN", TOKEN)


def test_the_public_card_declares_that_more_exists_and_how_to_ask():
    card = build_agent_card("http://analysis:8400/")

    assert card.capabilities.extended_agent_card is True
    assert card.security_schemes["service"].http_auth_security_scheme.scheme == "bearer"


def test_the_public_card_does_not_say_how_it_measures():
    card = build_agent_card("http://analysis:8400/")

    # Where the agent decides that a series is too thin, or two options too
    # close, is what a caller needs to trust a number -- and not what a passer-by
    # needs to know.
    assert [s.id for s in card.skills] == ["numbers"]


def test_the_extended_card_adds_the_measures():
    extended = build_extended_card(
        build_agent_card("http://a:8400/"), ["mean", "spread"]
    )

    methods = next(s for s in extended.skills if s.id == "methods")
    assert "mean" in methods.description and "spread" in methods.description
    assert [s.id for s in extended.skills] == ["numbers", "methods"]


def a_context(headers: dict[str, str]) -> ServerCallContext:
    return ServerCallContext(state={"headers": headers})


@pytest.mark.asyncio
async def test_without_a_token_the_extended_card_does_not_exist():
    # Denying existence instead of denying access: a stranger is not even told
    # that there is something more to ask for.
    with pytest.raises(ExtendedAgentCardNotConfiguredError):
        await card_for_the_caller(AgentCard(), a_context({}))


@pytest.mark.asyncio
async def test_a_wrong_token_is_refused():
    with pytest.raises(ExtendedAgentCardNotConfiguredError):
        await card_for_the_caller(
            AgentCard(), a_context({"Authorization": "Bearer another"})
        )


@pytest.mark.asyncio
async def test_the_right_token_gets_the_card():
    card = AgentCard(name="analysis")

    served = await card_for_the_caller(
        card, a_context({"Authorization": f"Bearer {TOKEN}"})
    )

    assert served is card


@pytest.mark.asyncio
async def test_without_a_configured_token_nobody_gets_in(monkeypatch):
    # A server with no secret configured does not serve the card to everyone:
    # forgetting it must close the door, not open it.
    monkeypatch.delenv("ANALYSIS_SERVICE_TOKEN")

    with pytest.raises(ExtendedAgentCardNotConfiguredError):
        await card_for_the_caller(AgentCard(), a_context({"Authorization": "Bearer "}))


@pytest.fixture
def app(offline_agent):
    return create_app(agent=offline_agent, base_url="http://analysis:8400/")


async def ask_for_the_card(app, headers: dict[str, str]) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get("/extendedAgentCard", headers=headers)


@pytest.mark.asyncio
async def test_the_rest_path_answers_401_and_says_how(app):
    response = await ask_for_the_card(app, {})

    assert response.status_code == 401
    # The 401 is for the legitimate client: from WWW-Authenticate it learns
    # what to send.
    assert response.headers["WWW-Authenticate"].startswith("Bearer")


@pytest.mark.asyncio
async def test_the_rest_path_lets_the_right_token_through(app):
    response = await ask_for_the_card(
        app, {"Authorization": f"Bearer {TOKEN}", **VERSION}
    )

    assert response.status_code == 200
    assert "methods" in [s["id"] for s in response.json()["skills"]]


@pytest.mark.asyncio
async def test_the_public_card_stays_public(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/.well-known/agent-card.json")

    assert response.status_code == 200
    assert [s["id"] for s in response.json()["skills"]] == ["numbers"]
