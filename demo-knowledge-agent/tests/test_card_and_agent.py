"""The subagent and its A2A identity card."""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from a2a.client.client_factory import is_legacy_version

from knowledge.agent import build_knowledge_tools, catalogue
from knowledge.server import build_agent_card, create_app

CARD = build_agent_card("http://knowledge:8200/")


def test_the_card_declares_streaming():
    assert CARD.capabilities.streaming is True


def test_the_card_declares_a_current_protocol_version():
    interface = CARD.supported_interfaces[0]

    assert interface.protocol_binding == "JSONRPC"
    assert is_legacy_version(interface.protocol_version) is False


def test_the_card_points_at_the_url_it_was_built_for():
    assert CARD.supported_interfaces[0].url == "http://knowledge:8200/"


def test_the_corpus_is_not_empty():
    assert set(catalogue()) >= {"go", "python", "rust"}


def test_reading_a_document_returns_its_text(tmp_path: Path):
    (tmp_path / "uno.md").write_text("# Uno\n\ncontenuto", encoding="utf-8")
    read = build_knowledge_tools(tmp_path)[0]

    assert "contenuto" in read.func(name="uno").text


def test_an_unknown_document_lists_the_available_ones(tmp_path: Path):
    (tmp_path / "uno.md").write_text("# Uno", encoding="utf-8")
    read = build_knowledge_tools(tmp_path)[0]

    text = read.func(name="due").text

    assert "does not exist" in text
    assert "uno" in text


def test_the_catalogue_is_in_the_tool_description(tmp_path: Path):
    (tmp_path / "go.md").write_text("# Go", encoding="utf-8")
    (tmp_path / "rust.md").write_text("# Rust", encoding="utf-8")

    read = build_knowledge_tools(tmp_path)[0]

    assert "go" in read.description and "rust" in read.description


@pytest.mark.asyncio
async def test_the_card_is_served_where_a2a_clients_look_for_it():
    app = create_app(agent=object())
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        card = await client.get("/.well-known/agent-card.json")
        health = await client.get("/health")

    assert card.status_code == 200
    assert card.json()["capabilities"]["streaming"] is True
    assert health.json()["status"] == "alive"


@pytest.mark.asyncio
async def test_liveness_and_readiness_answer_separately():
    app = create_app(agent=object())
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        alive = await client.get("/health/live")
        ready = await client.get("/health/ready")

    assert alive.json() == {"status": "alive"}
    assert ready.json()["documents"]
