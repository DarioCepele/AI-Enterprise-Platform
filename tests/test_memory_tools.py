"""Il tool con cui l'agente cerca nella propria memoria."""
from __future__ import annotations

import json
import logging

import httpx
import pytest

from demo.tools.memory_tools import build_memory_tools

RICORDI = {
    "ricordi": [
        {"thread_id": "t9", "seq": 4, "testo": "il referente e Marta", "somiglianza": 0.83},
        {"thread_id": "t9", "seq": 7, "testo": "budget 18k", "somiglianza": 0.61},
    ]
}


def tool_talking_to(handler):
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(record), base_url="http://memoria")
    return build_memory_tools("http://memoria", "tenant-a", client=client)[0], seen


async def invoke(tool, domanda: str) -> str:
    result = await tool.func(domanda=domanda)
    return result.text


@pytest.mark.asyncio
async def test_the_tool_asks_the_memory_and_reports_what_it_found():
    tool, seen = tool_talking_to(lambda _: httpx.Response(200, json=RICORDI))

    testo = await invoke(tool, "chi era il referente?")

    body = json.loads(seen[0].content)
    assert seen[0].url.path == "/search"
    assert body["query"] == "chi era il referente?"
    assert seen[0].headers["X-Memory-Scope"] == "tenant-a"
    assert "il referente e Marta" in testo


@pytest.mark.asyncio
async def test_the_question_does_not_travel_in_the_url():
    tool, seen = tool_talking_to(lambda _: httpx.Response(200, json=RICORDI))

    await invoke(tool, "una domanda riservata")

    assert "riservata" not in str(seen[0].url)
    assert seen[0].method == "POST"


@pytest.mark.asyncio
async def test_the_answer_says_these_are_fragments_not_certainties():
    tool, _ = tool_talking_to(lambda _: httpx.Response(200, json=RICORDI))

    testo = await invoke(tool, "budget?")

    assert "verificali" in testo


@pytest.mark.asyncio
async def test_nothing_found_is_said_plainly():
    tool, _ = tool_talking_to(lambda _: httpx.Response(200, json={"ricordi": []}))

    testo = await invoke(tool, "il colore della bicicletta")

    assert "Nessun ricordo" in testo
    assert "Non dare per scontato" in testo


@pytest.mark.asyncio
async def test_a_memory_service_down_does_not_kill_the_run(caplog):
    def broken(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("memoria irraggiungibile")

    tool, _ = tool_talking_to(broken)

    with caplog.at_level(logging.ERROR, logger="demo.tools.memory_tools"):
        testo = await invoke(tool, "qualsiasi cosa")

    assert "non e' raggiungibile" in testo
    assert "Ricerca nei ricordi fallita" in caplog.text
