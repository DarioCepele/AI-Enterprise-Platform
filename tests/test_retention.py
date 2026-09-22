"""What the memory forgets on purpose, and what it can rebuild."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from conftest import needs_backends

from memory_service.api import create_app
from memory_service.curation import ContextPolicy
from memory_service.models import NewMessage, Snapshot
from memory_service.service import ThreadMemory

pytestmark = [needs_backends, pytest.mark.integration]


@pytest.fixture
def memory(transcripts) -> ThreadMemory:
    return ThreadMemory(transcripts)


async def age(pool, scope: str, thread_id: str, days: int) -> None:
    """Moves a thread back in time, so retention has something to find."""
    async with pool.connection() as connection:
        await connection.execute(
            "UPDATE threads SET updated_at = %s WHERE scope = %s AND thread_id = %s",
            (datetime.now(UTC) - timedelta(days=days), scope, thread_id),
        )


async def test_retention_off_forgets_nothing(memory, pool, scope):
    await memory.append(scope, "old", NewMessage(role="user", content="one"))
    await age(pool, scope, "old", days=400)

    forgotten = await memory.apply_retention(days=0, scope=scope)

    assert forgotten == []
    assert (await memory.tail(scope, "old", limit=10)).messages


async def test_retention_does_not_reach_into_another_scope(memory, pool, scope):
    await memory.append(
        "another-tenant", "old", NewMessage(role="user", content="theirs")
    )
    await age(pool, "another-tenant", "old", days=40)

    # A maintenance call inside one tenant must not delete another's data: the
    # scope is an authorization boundary here too.
    assert await memory.apply_retention(days=30, scope=scope) == []
    assert (await memory.tail("another-tenant", "old", limit=10)).messages


async def test_a_thread_older_than_the_retention_is_gone(memory, pool, scope):
    await memory.append(scope, "old", NewMessage(role="user", content="one"))
    await memory.append(scope, "fresh", NewMessage(role="user", content="two"))
    await age(pool, scope, "old", days=40)

    forgotten = await memory.apply_retention(days=30, scope=scope)

    assert forgotten == [(scope, "old")]
    assert (await memory.tail(scope, "old", limit=10)).messages == []
    assert (await memory.tail(scope, "fresh", limit=10)).messages


async def test_retention_says_what_it_removed(memory, pool, scope, caplog):
    import logging

    await memory.append(scope, "old", NewMessage(role="user", content="one"))
    await age(pool, scope, "old", days=40)

    with caplog.at_level(logging.INFO, logger="memory_service.service"):
        await memory.apply_retention(days=30, scope=scope)

    # Deleting conversations is a product decision: it does not happen quietly.
    assert "retention" in caplog.text.lower()
    assert "old" in caplog.text


class Embedder:
    """One axis per keyword, so the test can assert what came back."""

    WORDS = ("go", "python")

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [
            [1.0 if word in text.lower() else 0.0 for word in self.WORDS]
            for text in texts
        ]


def thread_about(*topics: str) -> list[dict]:
    messages: list[dict] = []
    for i, topic in enumerate(topics):
        messages.append(
            {"id": f"u{i}", "role": "user", "content": f"let us talk about {topic}"}
        )
        messages.append(
            {"id": f"a{i}", "role": "assistant", "content": f"about {topic}"}
        )
    return messages


@pytest.fixture
def searchable(transcripts, pool) -> ThreadMemory:
    from memory_service.stores.vectors import PostgresMemories

    return ThreadMemory(
        transcripts,
        ContextPolicy(max_messages=4),
        embedder=Embedder(),
        memories=PostgresMemories(pool),
    )


async def test_the_index_is_rebuilt_from_the_transcripts(searchable, pool, scope):
    await searchable.save_snapshot(
        scope, "t1", Snapshot(messages=thread_about("go", "python", "go", "go"))
    )
    await searchable.compact_if_needed(scope, "t1")
    async with pool.connection() as connection:
        await connection.execute("DELETE FROM memories WHERE scope = %s", (scope,))

    rebuilt = await searchable.reindex(scope)

    # Losing the index costs a rebuild, not the memories: the transcripts are
    # the source, and this is the command that says so out loud.
    assert rebuilt > 0
    assert await searchable.search_memories(scope, "python", limit=3)


async def test_reindexing_one_thread_leaves_the_others_alone(searchable, scope):
    await searchable.save_snapshot(
        scope, "t1", Snapshot(messages=thread_about("go", "python", "go", "go"))
    )
    await searchable.save_snapshot(
        scope, "t2", Snapshot(messages=thread_about("python", "go", "python", "python"))
    )
    await searchable.compact_if_needed(scope, "t1")
    await searchable.compact_if_needed(scope, "t2")

    rebuilt = await searchable.reindex(scope, thread_id="t1")

    assert rebuilt > 0
    found = await searchable.search_memories(scope, "python", limit=10)
    assert {memory.thread_id for memory in found} == {"t1", "t2"}


async def test_reindexing_twice_does_not_duplicate(searchable, scope):
    await searchable.save_snapshot(
        scope, "t1", Snapshot(messages=thread_about("go", "python", "go", "go"))
    )
    await searchable.compact_if_needed(scope, "t1")

    first = await searchable.reindex(scope)
    await searchable.reindex(scope)

    found = await searchable.search_memories(scope, "go", limit=50)
    assert len(found) == first


async def client_for(memory: ThreadMemory) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=create_app(memory=memory))
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def test_the_admin_endpoints_report_what_they_did(memory, pool, scope):
    await memory.append(scope, "old", NewMessage(role="user", content="one"))
    await age(pool, scope, "old", days=40)

    async with await client_for(memory) as client:
        headers = {"X-Memory-Scope": scope}
        forgotten = await client.post(
            "/admin/retention", json={"days": 30}, headers=headers
        )
        rebuilt = await client.post("/admin/reindex", json={}, headers=headers)

    assert forgotten.json() == {"threads_forgotten": 1}
    assert rebuilt.json() == {"memories_indexed": 0}
