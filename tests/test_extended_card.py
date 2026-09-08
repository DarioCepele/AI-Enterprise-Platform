"""La card estesa: cosa dice, e a chi."""
from __future__ import annotations

import httpx
import pytest
from a2a.server.context import ServerCallContext
from a2a.types import AgentCard
from a2a.utils.errors import ExtendedAgentCardNotConfiguredError

from knowledge.extended import build_extended_card, card_per_chi_chiede
from knowledge.server import build_agent_card, create_app

TOKEN = "token-di-servizio"
VERSIONE = {"A2A-Version": "1.0"}


@pytest.fixture(autouse=True)
def token(monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_SERVICE_TOKEN", TOKEN)


def test_the_public_card_declares_that_more_exists_and_how_to_ask():
    card = build_agent_card("http://knowledge:8200/")

    assert card.capabilities.extended_agent_card is True
    assert card.security_schemes["servizio"].http_auth_security_scheme.scheme == "bearer"


def test_the_public_card_does_not_list_the_corpus():
    card = build_agent_card("http://knowledge:8200/")

    # Quali documenti abbiamo indicizzato dice di cosa si occupa chi ci lavora.
    assert [s.id for s in card.skills] == ["confronto-linguaggi"]


def test_the_extended_card_adds_the_catalogue():
    estesa = build_extended_card(build_agent_card("http://k:8200/"), ["go", "rust"])

    catalogo = next(s for s in estesa.skills if s.id == "catalogo")
    assert "go" in catalogo.description and "rust" in catalogo.description
    assert [s.id for s in estesa.skills] == ["confronto-linguaggi", "catalogo"]


def contesto(intestazioni: dict[str, str]) -> ServerCallContext:
    return ServerCallContext(state={"headers": intestazioni})


@pytest.mark.asyncio
async def test_without_a_token_the_extended_card_does_not_exist():
    # Negare l'esistenza invece di negare l'accesso: a un estraneo non si
    # conferma nemmeno che ci sia qualcosa di piu' da chiedere.
    with pytest.raises(ExtendedAgentCardNotConfiguredError):
        await card_per_chi_chiede(AgentCard(), contesto({}))


@pytest.mark.asyncio
async def test_a_wrong_token_is_refused():
    with pytest.raises(ExtendedAgentCardNotConfiguredError):
        await card_per_chi_chiede(AgentCard(), contesto({"Authorization": "Bearer altro"}))


@pytest.mark.asyncio
async def test_the_right_token_gets_the_card():
    card = AgentCard(name="knowledge")

    servita = await card_per_chi_chiede(card, contesto({"Authorization": f"Bearer {TOKEN}"}))

    assert servita is card


@pytest.mark.asyncio
async def test_without_a_configured_token_nobody_gets_in(monkeypatch):
    # Un server senza segreto configurato non serve la card a chiunque:
    # la dimenticanza deve chiudere, non aprire.
    monkeypatch.delenv("KNOWLEDGE_SERVICE_TOKEN")

    with pytest.raises(ExtendedAgentCardNotConfiguredError):
        await card_per_chi_chiede(AgentCard(), contesto({"Authorization": "Bearer "}))


@pytest.fixture
def app():
    return create_app(agent=None, base_url="http://knowledge:8200/")


async def chiedi_la_card(app, intestazioni: dict[str, str]) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get("/extendedAgentCard", headers=intestazioni)


@pytest.mark.asyncio
async def test_the_rest_path_answers_401_and_says_how(app):
    response = await chiedi_la_card(app, {})

    assert response.status_code == 401
    # Il 401 e' per il client legittimo: dal WWW-Authenticate impara cosa mandare.
    assert response.headers["WWW-Authenticate"].startswith("Bearer")


@pytest.mark.asyncio
async def test_the_rest_path_lets_the_right_token_through(app):
    response = await chiedi_la_card(app, {"Authorization": f"Bearer {TOKEN}", **VERSIONE})

    assert response.status_code == 200
    assert "catalogo" in [s["id"] for s in response.json()["skills"]]


@pytest.mark.asyncio
async def test_the_public_card_stays_public(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/.well-known/agent-card.json")

    assert response.status_code == 200
    assert [s["id"] for s in response.json()["skills"]] == ["confronto-linguaggi"]
