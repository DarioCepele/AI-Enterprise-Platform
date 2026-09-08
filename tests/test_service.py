"""Il servizio nel suo insieme: cache calda, degrado, API."""
from __future__ import annotations

import httpx
import pytest

from memory_service.api import create_app
from memory_service.models import NewMessage
from memory_service.service import ThreadMemory

from conftest import needs_backends

pytestmark = [needs_backends, pytest.mark.integration]


@pytest.fixture
def memory(transcripts, hot) -> ThreadMemory:
    return ThreadMemory(transcripts, hot)


class BrokenHot:
    """Redis spento. Il servizio deve degradare, non cadere."""

    async def append(self, *args, **kwargs) -> None:
        raise ConnectionError("redis giu'")

    async def tail(self, *args, **kwargs) -> None:
        raise ConnectionError("redis giu'")

    async def forget(self, *args, **kwargs) -> None:
        raise ConnectionError("redis giu'")

    async def ping(self) -> None:
        raise ConnectionError("redis giu'")


async def client_for(memory: ThreadMemory) -> httpx.AsyncClient:
    app = create_app(memory=memory)
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def test_the_tail_comes_from_the_cache_once_it_is_warm(memory, scope):
    for i in range(4):
        await memory.append(scope, "t1", NewMessage(role="user", content=str(i)))

    result = await memory.tail(scope, "t1", limit=3)

    assert result.source == "hot"
    assert [m.content for m in result.messages] == ["1", "2", "3"]


async def test_without_redis_it_still_answers_from_the_durable_store(transcripts, scope):
    degraded = ThreadMemory(transcripts, BrokenHot())

    await degraded.append(scope, "t1", NewMessage(role="user", content="scritto comunque"))
    result = await degraded.tail(scope, "t1", limit=10)

    # Il messaggio e' al sicuro anche se la cache non ha mai visto nulla.
    assert result.source == "durable"
    assert [m.content for m in result.messages] == ["scritto comunque"]


async def test_the_cache_never_holds_the_only_copy(memory, transcripts, hot, scope):
    await memory.append(scope, "t1", NewMessage(role="user", content="unico"))
    await hot.forget(scope, "t1")

    result = await memory.tail(scope, "t1", limit=10)

    assert result.source == "durable"
    assert [m.content for m in result.messages] == ["unico"]


async def test_the_api_writes_and_reads_a_thread(memory, scope):
    async with await client_for(memory) as client:
        headers = {"X-Memory-Scope": scope}
        created = await client.post(
            "/threads/t1/messages",
            json={"role": "user", "content": "ciao"},
            headers=headers,
        )
        read = await client.get("/threads/t1/messages", headers=headers)

    assert created.status_code == 201
    assert created.json()["seq"] == 1
    assert [m["content"] for m in read.json()["messages"]] == ["ciao"]


async def test_the_api_refuses_a_request_without_scope(memory):
    async with await client_for(memory) as client:
        response = await client.get("/threads/t1/messages")

    # Senza scope non esiste un confine: meglio un errore di una lettura
    # su un ambito indovinato.
    assert response.status_code == 422


async def test_the_api_keeps_scopes_apart(memory, scope):
    async with await client_for(memory) as client:
        await client.post(
            "/threads/t1/messages",
            json={"role": "user", "content": "riservato"},
            headers={"X-Memory-Scope": scope},
        )
        altrui = await client.get(
            "/threads/t1/messages", headers={"X-Memory-Scope": f"{scope}-altro"}
        )

    assert altrui.json()["messages"] == []


async def test_the_api_forgets_a_thread(memory, scope):
    async with await client_for(memory) as client:
        headers = {"X-Memory-Scope": scope}
        await client.post(
            "/threads/t1/messages", json={"role": "user", "content": "ciao"}, headers=headers
        )
        removed = await client.delete("/threads/t1", headers=headers)
        read = await client.get("/threads/t1/messages", headers=headers)

    assert removed.json()["buckets_rimossi"] == 1
    assert read.json()["messages"] == []


async def test_health_says_ok_when_both_memories_answer(memory):
    async with await client_for(memory) as client:
        response = await client.get("/health")

    assert response.json() == {"status": "ok", "durable": "ok", "hot": "ok"}


async def test_health_says_degraded_when_redis_is_down(transcripts):
    async with await client_for(ThreadMemory(transcripts, BrokenHot())) as client:
        response = await client.get("/health")

    # Degradato, non guasto: i messaggi si scrivono e si leggono lo stesso.
    assert response.status_code == 200
    assert response.json()["status"] == "degraded"
