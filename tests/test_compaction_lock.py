"""One compaction per thread, however many replicas are running."""
from __future__ import annotations

import asyncio
import logging

import pytest

from memory_service.curation import ContextPolicy
from memory_service.models import Snapshot
from memory_service.service import ThreadMemory
from memory_service.stores.locks import InProcessLock, PostgresLock

from conftest import needs_backends

pytestmark = [needs_backends, pytest.mark.integration]

TURNS = [
    {"id": f"m{i}", "role": "user" if i % 2 == 0 else "assistant", "content": f"turn {i}"}
    for i in range(12)
]


class SlowSummarizer:
    """Takes long enough that a second replica starts while the first is working."""

    _model = "slow"

    def __init__(self) -> None:
        self.calls = 0
        self.started = asyncio.Event()

    async def summarize(self, messages):
        self.calls += 1
        self.started.set()
        await asyncio.sleep(0.3)
        return "a summary"


def a_replica(transcripts, summarizer, lock) -> ThreadMemory:
    return ThreadMemory(
        transcripts,
        ContextPolicy(max_messages=4),
        summarizer=summarizer,
        lock=lock,
    )


async def test_two_replicas_compact_a_thread_once(transcripts, pool, scope):
    summarizer = SlowSummarizer()
    first = a_replica(transcripts, summarizer, PostgresLock(pool))
    second = a_replica(transcripts, summarizer, PostgresLock(pool))
    await first.save_snapshot(scope, "t1", Snapshot(messages=TURNS))

    await asyncio.gather(
        first.compact_if_needed(scope, "t1"),
        second.compact_if_needed(scope, "t1"),
    )

    # Two replicas, one model call: the second finds the lock taken and leaves.
    assert summarizer.calls == 1


async def test_the_replica_that_finds_the_lock_taken_says_so(transcripts, pool, scope, caplog):
    summarizer = SlowSummarizer()
    first = a_replica(transcripts, summarizer, PostgresLock(pool))
    second = a_replica(transcripts, summarizer, PostgresLock(pool))
    await first.save_snapshot(scope, "t1", Snapshot(messages=TURNS))

    with caplog.at_level(logging.INFO, logger="memory_service.service"):
        await asyncio.gather(
            first.compact_if_needed(scope, "t1"),
            second.compact_if_needed(scope, "t1"),
        )

    assert "already running" in caplog.text


async def test_the_lock_is_released_and_the_next_compaction_runs(transcripts, pool, scope):
    summarizer = SlowSummarizer()
    replica = a_replica(transcripts, summarizer, PostgresLock(pool))
    await replica.save_snapshot(scope, "t1", Snapshot(messages=TURNS))

    await replica.compact_if_needed(scope, "t1")
    later = [
        {"id": f"n{i}", "role": "user" if i % 2 == 0 else "assistant", "content": f"later {i}"}
        for i in range(12)
    ]
    await replica.save_snapshot(scope, "t1", Snapshot(messages=TURNS + later))
    await replica.compact_if_needed(scope, "t1")

    # A lock never released would make the thread uncompactable for good: an
    # advisory lock has no lease to wait out.
    assert summarizer.calls == 2


async def test_two_threads_do_not_wait_for_each_other(transcripts, pool, scope):
    summarizer = SlowSummarizer()
    replica = a_replica(transcripts, summarizer, PostgresLock(pool))
    await replica.save_snapshot(scope, "t1", Snapshot(messages=TURNS))
    await replica.save_snapshot(scope, "t2", Snapshot(messages=TURNS))

    await asyncio.gather(
        replica.compact_if_needed(scope, "t1"),
        replica.compact_if_needed(scope, "t2"),
    )

    assert summarizer.calls == 2


async def test_a_lock_taken_is_not_given_to_somebody_else(pool):
    mine = PostgresLock(pool)
    yours = PostgresLock(pool)

    async with mine.hold("shared") as taken:
        assert taken is True
        async with yours.hold("shared") as also_taken:
            # Leaving the second block must not release the first one's lock:
            # with an advisory lock it cannot, because the lock belongs to the
            # connection that took it.
            assert also_taken is False

    # And once the holder is done, the next one gets it.
    async with yours.hold("shared") as free_now:
        assert free_now is True


async def test_a_lock_goes_with_the_connection_that_took_it(pool):
    """No lease, so nothing to expire -- and nothing to leak either.

    The Redis version needed a TTL: long enough to outlast the work, short
    enough to unstick a replica that died holding it. Here the lock dies with
    the connection, so both problems are the same problem, already solved.
    """
    lock = PostgresLock(pool)

    async with lock.hold("slow") as taken:
        assert taken is True
        async with pool.connection() as connection:
            held = await (
                await connection.execute(
                    "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory'"
                )
            ).fetchone()
            assert held[0] >= 1

    async with pool.connection() as connection:
        left = await (
            await connection.execute(
                "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory'"
            )
        ).fetchone()
    assert left[0] == 0


async def test_without_a_database_the_lock_still_guards_one_process(transcripts, scope):
    summarizer = SlowSummarizer()
    replica = a_replica(transcripts, summarizer, InProcessLock())
    await replica.save_snapshot(scope, "t1", Snapshot(messages=TURNS))

    await asyncio.gather(
        replica.compact_if_needed(scope, "t1"),
        replica.compact_if_needed(scope, "t1"),
    )

    assert summarizer.calls == 1
