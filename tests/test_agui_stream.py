"""Verifica la sequenza di eventi AG-UI prodotta da una run."""
import json

import httpx
import pytest

REQUEST = {
    "threadId": "t1",
    "runId": "r1",
    "state": {},
    "messages": [{"id": "m1", "role": "user", "content": "ciao"}],
    "tools": [],
    "context": [],
    "forwardedProps": {},
}


async def collect_events(app) -> list[dict]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        async with client.stream(
            "POST", "/agui", json=REQUEST, headers={"Accept": "text/event-stream"}
        ) as response:
            assert response.status_code == 200
            events = []
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    events.append(json.loads(line[len("data: "):]))
            return events


@pytest.mark.asyncio
async def test_run_starts_and_finishes(app):
    events = await collect_events(app)
    types = [e["type"] for e in events]

    assert types[0] == "RUN_STARTED"
    assert types[-1] == "RUN_FINISHED"
    assert "RUN_ERROR" not in types
    assert types.count("RUN_STARTED") == 1


@pytest.mark.asyncio
async def test_text_is_streamed_in_deltas(app):
    events = await collect_events(app)

    deltas = [e["delta"] for e in events if e["type"] == "TEXT_MESSAGE_CONTENT"]
    assert deltas == ["ciao ", "mondo"]


@pytest.mark.asyncio
async def test_every_text_message_start_has_an_end(app):
    events = await collect_events(app)

    starts = [e["messageId"] for e in events if e["type"] == "TEXT_MESSAGE_START"]
    ends = [e["messageId"] for e in events if e["type"] == "TEXT_MESSAGE_END"]
    assert sorted(starts) == sorted(ends)


@pytest.mark.asyncio
async def test_run_id_is_echoed_back(app):
    events = await collect_events(app)

    started = next(e for e in events if e["type"] == "RUN_STARTED")
    assert started["runId"] == "r1"
    assert started["threadId"] == "t1"


@pytest.mark.asyncio
async def test_tool_call_emits_result_then_state_snapshot(tool_app):
    """La catena completa di una tool call, senza LLM."""
    events = await collect_events(tool_app)
    types = [e["type"] for e in events]

    assert "TOOL_CALL_START" in types
    assert types.index("TOOL_CALL_RESULT") < types.index("STATE_SNAPSHOT")

    result = next(e for e in events if e["type"] == "TOOL_CALL_RESULT")
    # Il payload arriva serializzato, non come oggetto.
    assert isinstance(result["content"], str)
    assert json.loads(result["content"])["component"] == "ui-table"

    snapshot = next(e for e in events if e["type"] == "STATE_SNAPSHOT")
    assert snapshot["snapshot"]["artifacts"][0]["component"] == "ui-table"


@pytest.mark.asyncio
async def test_tool_snapshot_preserves_calls_and_nonempty_text(tool_app):
    """Lo snapshot conserva toolCalls senza testo e la risposta finale non vuota.

    La voce assistant con sole toolCalls e' parte del protocollo AG-UI.
    Il reducer deve evitare di renderizzarla come una bolla di testo vuota.
    """
    events = await collect_events(tool_app)

    snapshot = next(e for e in events if e["type"] == "MESSAGES_SNAPSHOT")
    assistant = [m for m in snapshot["messages"] if m.get("role") == "assistant"]
    assert all(m.get("content") or m.get("toolCalls") for m in assistant)
    text_messages = [m for m in assistant if not m.get("toolCalls")]
    assert [m["content"] for m in text_messages] == ["Ecco il confronto."]

    calls = [call for m in assistant for call in m.get("toolCalls", [])]
    result = next(e for e in events if e["type"] == "TOOL_CALL_RESULT")
    assert [(call["id"], call["function"]["name"]) for call in calls] == [
        (result["toolCallId"], "ui_table")
    ]
    assert events[-1]["type"] == "RUN_FINISHED"


@pytest.mark.asyncio
async def test_cors_preflight_allows_dev_frontend(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.options(
            "/agui",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert "POST" in response.headers["access-control-allow-methods"]
    assert "content-type" in response.headers["access-control-allow-headers"].lower()


@pytest.mark.asyncio
async def test_cors_preflight_allows_next_fallback_port(app):
    """Se la 3000 e' occupata Next slitta sulla 3001: il CORS deve seguirlo."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.options(
            "/agui",
            headers={
                "Origin": "http://localhost:3001",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3001"
