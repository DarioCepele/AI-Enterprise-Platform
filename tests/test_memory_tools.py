"""The tool the agent uses to search its own memory."""
from __future__ import annotations

import json
import logging

import httpx
import pytest

from demo.tools.memory_tools import build_memory_tools

MEMORIES = {
    "memories": [
        {"thread_id": "t9", "seq": 4, "text": "the contact is Marta", "similarity": 0.83},
        {"thread_id": "t9", "seq": 7, "text": "budget 18k", "similarity": 0.61},
    ]
}


def tool_talking_to(handler):
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(record), base_url="http://memory")
    return build_memory_tools("http://memory", "tenant-a", client=client)[0], seen


async def invoke(tool, query: str) -> str:
    result = await tool.func(query=query)
    return result.text


@pytest.mark.asyncio
async def test_the_tool_asks_the_memory_and_reports_what_it_found():
    tool, seen = tool_talking_to(lambda _: httpx.Response(200, json=MEMORIES))

    text = await invoke(tool, "who was the contact?")

    body = json.loads(seen[0].content)
    assert seen[0].url.path == "/search"
    assert body["query"] == "who was the contact?"
    assert seen[0].headers["X-Memory-Scope"] == "tenant-a"
    assert "the contact is Marta" in text


@pytest.mark.asyncio
async def test_the_question_does_not_travel_in_the_url():
    tool, seen = tool_talking_to(lambda _: httpx.Response(200, json=MEMORIES))

    await invoke(tool, "a confidential question")

    assert "confidential" not in str(seen[0].url)
    assert seen[0].method == "POST"


@pytest.mark.asyncio
async def test_the_answer_says_these_are_fragments_not_certainties():
    tool, _ = tool_talking_to(lambda _: httpx.Response(200, json=MEMORIES))

    text = await invoke(tool, "budget?")

    assert "verify them" in text


@pytest.mark.asyncio
async def test_nothing_found_is_said_plainly():
    tool, _ = tool_talking_to(lambda _: httpx.Response(200, json={"memories": []}))

    text = await invoke(tool, "the colour of the bicycle")

    assert "No memory found" in text
    assert "Do not assume" in text


@pytest.mark.asyncio
async def test_a_memory_service_down_does_not_kill_the_run(caplog):
    def broken(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("memory unreachable")

    tool, _ = tool_talking_to(broken)

    with caplog.at_level(logging.ERROR, logger="demo.tools.memory_tools"):
        text = await invoke(tool, "anything at all")

    assert "unreachable right now" in text
    assert "Memory search failed" in caplog.text
