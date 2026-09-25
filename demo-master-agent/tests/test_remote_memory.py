"""The thread store that lives in the memory service."""
from __future__ import annotations

import logging

import httpx
import pytest
from agent_framework.ag_ui import AGUIThreadSnapshot

from demo.memory.remote_store import MemoryServiceSnapshotStore

SNAPSHOT = AGUIThreadSnapshot(
    messages=[{"id": "m1", "role": "user", "content": "hello"}],
    state={"plan": {"status": "idle"}},
    session_state={"provider": "continuation"},
)

def store_talking_to(handler) -> tuple[MemoryServiceSnapshotStore, list[httpx.Request]]:
    """A store wired to a fake service, with the requests recorded."""
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(record), base_url="http://memory")
    return MemoryServiceSnapshotStore("http://memory", client=client), seen

@pytest.mark.asyncio
async def test_saving_sends_the_whole_snapshot_and_the_scope():
    store, seen = store_talking_to(lambda _: httpx.Response(200, json={"new_turns": 1}))

    await store.save(scope="tenant-a", thread_id="t1", snapshot=SNAPSHOT)

    request = seen[0]
    assert request.method == "PUT"
    assert request.url.path == "/threads/t1/snapshot"
    assert request.headers["X-Memory-Scope"] == "tenant-a"
    import json

    body = json.loads(request.content)
    assert body["messages"] == SNAPSHOT.messages
    assert body["state"] == SNAPSHOT.state
    assert body["session_state"] == SNAPSHOT.session_state

@pytest.mark.asyncio
async def test_reading_rebuilds_the_snapshot():
    payload = {
        "messages": SNAPSHOT.messages,
        "state": SNAPSHOT.state,
        "interrupt": None,
        "session_state": SNAPSHOT.session_state,
    }
    store, _ = store_talking_to(lambda _: httpx.Response(200, json=payload))

    snapshot = await store.get(scope="tenant-a", thread_id="t1")

    assert snapshot.messages == SNAPSHOT.messages
    assert snapshot.state == SNAPSHOT.state
    assert snapshot.session_state == SNAPSHOT.session_state

@pytest.mark.asyncio
async def test_an_unknown_thread_reads_as_nothing():
    store, _ = store_talking_to(
        lambda _: httpx.Response(404, json={"detail": "unknown"})
    )

    assert await store.get(scope="tenant-a", thread_id="never-seen") is None

@pytest.mark.asyncio
async def test_a_memory_service_down_does_not_stop_the_conversation(caplog):
    def broken(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("memory service unreachable")

    store, _ = store_talking_to(broken)

    with caplog.at_level(logging.ERROR, logger="demo.memory.remote_store"):
        snapshot = await store.get(scope="tenant-a", thread_id="t1")

    assert snapshot is None
    assert "unreadable" in caplog.text

@pytest.mark.asyncio
async def test_a_failed_save_is_logged_and_does_not_raise(caplog):
    store, _ = store_talking_to(
        lambda _: httpx.Response(500, json={"detail": "broken"})
    )

    with caplog.at_level(logging.ERROR, logger="demo.memory.remote_store"):

        await store.save(scope="tenant-a", thread_id="t1", snapshot=SNAPSHOT)

    assert "NOT saved" in caplog.text

@pytest.mark.asyncio
async def test_deleting_reports_whether_something_was_there():
    store, seen = store_talking_to(
        lambda _: httpx.Response(200, json={"buckets_removed": 2})
    )

    removed = await store.delete(scope="tenant-a", thread_id="t1")

    assert removed is True
    assert seen[0].method == "DELETE"
    assert seen[0].url.path == "/threads/t1"

@pytest.mark.asyncio
async def test_clearing_everything_without_a_scope_is_refused():
    store, seen = store_talking_to(
        lambda _: httpx.Response(200, json={"threads_removed": 0})
    )

    with pytest.raises(ValueError):
        await store.clear()

    assert seen == []

@pytest.mark.asyncio
async def test_clearing_a_scope_hits_that_scope_only():
    store, seen = store_talking_to(
        lambda _: httpx.Response(200, json={"threads_removed": 3})
    )

    await store.clear(scope="tenant-a")

    assert seen[0].url.path == "/scope"
    assert seen[0].headers["X-Memory-Scope"] == "tenant-a"

@pytest.mark.asyncio
async def test_the_pruning_done_by_the_memory_shows_up_in_the_logs(caplog):
    payload = {
        "messages": SNAPSHOT.messages,
        "state": None,
        "interrupt": None,
        "session_state": None,
        "curation": {
            "kept": 1,
            "reasoning_removed": 4,
            "results_emptied": 2,
            "messages_dropped": 0,
        },
    }
    store, _ = store_talking_to(lambda _: httpx.Response(200, json=payload))

    with caplog.at_level(logging.INFO, logger="demo.memory.remote_store"):
        await store.get(scope="tenant-a", thread_id="t1")

    assert "4 reasonings removed" in caplog.text
    assert "2 results emptied" in caplog.text
