"""One compaction per thread, however many replicas are running."""
from __future__ import annotations

import asyncio
import logging

import pytest

from memory_service.curation import ContextPolicy
from memory_service.models import Snapshot
from memory_service.service import ThreadMemory
from memory_service.stores.locks import InProcessLock, RedisLock

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


def a_replica(transcripts, hot, summarizer, lock) -> ThreadMemory:
    return ThreadMemory(
        transcripts,
        hot,
        ContextPolicy(max_messages=4),
        summarizer=summarizer,
        lock=lock,
    )


async def test_two_replicas_compact_a_thread_once(transcripts, hot, redis_client, scope):
    summarizer = SlowSummarizer()
    first = a_replica(transcripts, hot, summarizer, RedisLock(redis_client))
    second = a_replica(transcripts, hot, summarizer, RedisLock(redis_client))
    await first.save_snapshot(scope, "t1", Snapshot(messages=TURNS))

    await asyncio.gather(
        first.compact_if_needed(scope, "t1"),
        second.compact_if_needed(scope, "t1"),
    )

    # Two replicas, one model call: the second finds the lock taken and leaves.
    assert summarizer.calls == 1


async def test_the_replica_that_finds_the_lock_taken_says_so(
    transcripts, hot, redis_client, scope, caplog
):
    summarizer = SlowSummarizer()
    first = a_replica(transcripts, hot, summarizer, RedisLock(redis_client))
    second = a_replica(transcripts, hot, summarizer, RedisLock(redis_client))
    await first.save_snapshot(scope, "t1", Snapshot(messages=TURNS))

    with caplog.at_level(logging.INFO, logger="memory_service.service"):
        await asyncio.gather(
            first.compact_if_needed(scope, "t1"),
            second.compact_if_needed(scope, "t1"),
        )

    assert "already running" in caplog.text


async def test_the_lock_is_released_and_the_next_compaction_runs(
    transcripts, hot, redis_client, scope
):
    summarizer = SlowSummarizer()
    replica = a_replica(transcripts, hot, summarizer, RedisLock(redis_client))
    await replica.save_snapshot(scope, "t1", Snapshot(messages=TURNS))

    await replica.compact_if_needed(scope, "t1")
    later = [
        {"id": f"n{i}", "role": "user" if i % 2 == 0 else "assistant", "content": f"later {i}"}
        for i in range(12)
    ]
    await replica.save_snapshot(scope, "t1", Snapshot(messages=TURNS + later))
    await replica.compact_if_needed(scope, "t1")

    # A lock never released would make the thread uncompactable until its TTL.
    assert summarizer.calls == 2


async def test_two_threads_do_not_wait_for_each_other(transcripts, hot, redis_client, scope):
    summarizer = SlowSummarizer()
    replica = a_replica(transcripts, hot, summarizer, RedisLock(redis_client))
    await replica.save_snapshot(scope, "t1", Snapshot(messages=TURNS))
    await replica.save_snapshot(scope, "t2", Snapshot(messages=TURNS))

    await asyncio.gather(
        replica.compact_if_needed(scope, "t1"),
        replica.compact_if_needed(scope, "t2"),
    )

    assert summarizer.calls == 2


async def test_a_lock_of_another_holder_is_not_released(redis_client):
    mine = RedisLock(redis_client)
    yours = RedisLock(redis_client)

    async with mine.hold("shared") as taken:
        assert taken is True
        async with yours.hold("shared") as also_taken:
            assert also_taken is False
        # Releasing on the way out of a lock we never took would hand the work
        # to whoever comes next while the holder is still working.
        assert await redis_client.get("lock:shared") is not None


async def test_an_expired_lock_is_reported(redis_client, caplog):
    lock = RedisLock(redis_client, ttl_seconds=1)

    with caplog.at_level(logging.WARNING, logger="memory_service.stores.locks"):
        async with lock.hold("slow") as taken:
            assert taken is True
            await asyncio.sleep(1.2)

    assert "expired before the work finished" in caplog.text


async def test_without_redis_the_lock_still_guards_one_process(transcripts, hot, scope):
    summarizer = SlowSummarizer()
    replica = a_replica(transcripts, hot, summarizer, InProcessLock())
    await replica.save_snapshot(scope, "t1", Snapshot(messages=TURNS))

    await asyncio.gather(
        replica.compact_if_needed(scope, "t1"),
        replica.compact_if_needed(scope, "t1"),
    )

    assert summarizer.calls == 1
