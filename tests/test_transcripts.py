"""Durable memory: order, positions, tails, isolation and deletion."""
from __future__ import annotations

import asyncio

import pytest

from memory_service.models import NewMessage
from memory_service.stores.postgres import PostgresTranscripts

from conftest import needs_backends

pytestmark = [needs_backends, pytest.mark.integration]


async def write(store: PostgresTranscripts, scope: str, thread: str, *texts: str) -> None:
    for text in texts:
        await store.append(scope, thread, NewMessage(role="user", content=text))


async def test_a_message_comes_back_as_written(transcripts, scope):
    stored = await transcripts.append(
        scope, "t1", NewMessage(role="user", content="ciao", meta={"run": "r1"})
    )

    assert stored.seq == 1
    assert stored.content == "ciao"
    assert stored.meta == {"run": "r1"}


async def test_sequence_numbers_grow_within_the_thread(transcripts, scope):
    await write(transcripts, scope, "t1", "uno", "due", "tre")

    messages = await transcripts.tail(scope, "t1", limit=10)
    assert [m.seq for m in messages] == [1, 2, 3]
    assert [m.content for m in messages] == ["uno", "due", "tre"]


async def test_the_whole_conversation_comes_back_in_order(transcripts, scope):
    await write(transcripts, scope, "t1", "uno", "due", "tre", "quattro")

    # There is no bucket to cross any more: a turn is a row, and the order is
    # the position it was given when it was written.
    assert [m.content for m in await transcripts.history(scope, "t1")] == [
        "uno",
        "due",
        "tre",
        "quattro",
    ]


async def test_the_tail_is_the_end_of_the_conversation(transcripts, scope):
    await write(transcripts, scope, "t1", "uno", "due", "tre", "quattro", "cinque")

    messages = await transcripts.tail(scope, "t1", limit=4)
    assert [m.content for m in messages] == ["due", "tre", "quattro", "cinque"]


async def test_concurrent_writes_do_not_lose_messages(transcripts, scope):
    await asyncio.gather(
        *(
            transcripts.append(scope, "t1", NewMessage(role="user", content=str(i)))
            for i in range(12)
        )
    )

    messages = await transcripts.tail(scope, "t1", limit=100)
    assert len(messages) == 12
    assert sorted(m.content for m in messages) == sorted(str(i) for i in range(12))


async def test_threads_are_separate(transcripts, scope):
    await write(transcripts, scope, "t1", "di t1")
    await write(transcripts, scope, "t2", "di t2")

    assert [m.content for m in await transcripts.tail(scope, "t1", limit=10)] == ["di t1"]


async def test_scopes_are_separate(transcripts, scope):
    await write(transcripts, scope, "t1", "mio")
    await write(transcripts, f"{scope}-other", "t1", "di un other")

    assert [m.content for m in await transcripts.tail(scope, "t1", limit=10)] == ["mio"]


async def test_an_unknown_thread_is_empty_not_an_error(transcripts, scope):
    assert await transcripts.tail(scope, "mai-visto", limit=10) == []


async def test_forgetting_removes_the_thread(transcripts, scope):
    await write(transcripts, scope, "t1", "uno", "due", "tre", "quattro")

    removed = await transcripts.forget(scope, "t1")

    # What comes back is how many turns went with the thread, not how many
    # documents happened to hold them.
    assert removed == 4
    assert await transcripts.tail(scope, "t1", limit=10) == []
