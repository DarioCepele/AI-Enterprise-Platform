"""The operational logs leave the process, so two replicas tell one story."""
from __future__ import annotations

import asyncio
import logging
import os
import uuid

import pytest
from redis.asyncio import Redis

from demo.logging_bridge import LogCollector, RedisLogStream

REDIS_URI = os.getenv("DEMO_REDIS_URI")

needs_redis = pytest.mark.skipif(
    not REDIS_URI, reason="DEMO_REDIS_URI is required: this test needs a real Redis"
)

pytestmark = [needs_redis, pytest.mark.asyncio]


@pytest.fixture
async def redis_client():
    client = Redis.from_url(REDIS_URI, decode_responses=True)
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
def key() -> str:
    return f"test-logs:{uuid.uuid4().hex[:12]}"


def a_replica(redis_client, key, **kwargs) -> tuple[LogCollector, RedisLogStream]:
    """One process: its own collector and its own view of the shared stream."""
    collector = LogCollector()
    stream = RedisLogStream(redis_client, key=key, **kwargs)
    stream.attach(collector)
    return collector, stream


async def test_two_replicas_are_read_through_one_cursor(redis_client, key):
    first, first_stream = a_replica(redis_client, key)
    second, second_stream = a_replica(redis_client, key)

    first.append({"ts": "t", "level": "INFO", "source": "a", "message": "from the first"})
    second.append({"ts": "t", "level": "INFO", "source": "b", "message": "from the second"})
    await first_stream.flush()
    await second_stream.flush()

    page = await first_stream.since("")

    assert [entry["message"] for entry in page["entries"]] == ["from the first", "from the second"]


async def test_the_cursor_does_not_repeat_what_was_already_read(redis_client, key):
    collector, stream = a_replica(redis_client, key)

    collector.append({"ts": "t", "level": "INFO", "source": "a", "message": "one"})
    await stream.flush()
    first = await stream.since("")

    collector.append({"ts": "t", "level": "INFO", "source": "a", "message": "two"})
    await stream.flush()
    second = await stream.since(first["cursor"])

    assert [entry["message"] for entry in second["entries"]] == ["two"]


async def test_reading_twice_from_the_same_cursor_is_idempotent(redis_client, key):
    collector, stream = a_replica(redis_client, key)
    collector.append({"ts": "t", "level": "INFO", "source": "a", "message": "one"})
    await stream.flush()

    assert (await stream.since(""))["entries"] == (await stream.since(""))["entries"]


async def test_a_trimmed_stream_says_how_many_lines_are_gone(redis_client, key):
    collector, stream = a_replica(redis_client, key, maxlen=2)

    for i in range(5):
        collector.append({"ts": "t", "level": "INFO", "source": "a", "message": f"line {i}"})
        await stream.flush()

    page = await stream.since("")
    later = await stream.since(page["cursor"])

    assert [entry["message"] for entry in page["entries"]] == ["line 3", "line 4"]
    assert later["dropped"] == 0


async def test_the_lines_lost_between_two_reads_are_counted(redis_client, key):
    collector, stream = a_replica(redis_client, key, maxlen=2)

    collector.append({"ts": "t", "level": "INFO", "source": "a", "message": "line 0"})
    await stream.flush()
    seen = await stream.since("")

    for i in range(1, 5):
        collector.append({"ts": "t", "level": "INFO", "source": "a", "message": f"line {i}"})
        await stream.flush()

    page = await stream.since(seen["cursor"])

    # Lines 1 and 2 were trimmed while the client was not looking: it must be
    # told, or it reads a hole as continuity.
    assert [entry["message"] for entry in page["entries"]] == ["line 3", "line 4"]
    assert page["dropped"] == 2


async def test_the_sequence_is_unique_across_replicas(redis_client, key):
    first, first_stream = a_replica(redis_client, key)
    second, second_stream = a_replica(redis_client, key)

    for i in range(3):
        first.append({"ts": "t", "level": "INFO", "source": "a", "message": f"a{i}"})
        second.append({"ts": "t", "level": "INFO", "source": "b", "message": f"b{i}"})
    await first_stream.flush()
    await second_stream.flush()

    seqs = [entry["seq"] for entry in (await first_stream.since(""))["entries"]]

    assert len(set(seqs)) == len(seqs)


async def test_the_drain_task_publishes_without_being_asked(redis_client, key):
    collector, stream = a_replica(redis_client, key, flush_seconds=0.01)

    async with stream.running():
        collector.append({"ts": "t", "level": "INFO", "source": "a", "message": "published"})
        await asyncio.sleep(0.1)
        page = await stream.since("")

    assert [entry["message"] for entry in page["entries"]] == ["published"]


async def test_an_unreachable_redis_keeps_the_lines_in_the_process(key, caplog):
    class Unreachable:
        async def incrby(self, *args, **kwargs):
            raise ConnectionError("redis unreachable")

    collector, stream = LogCollector(), RedisLogStream(Unreachable(), key=key)
    stream.attach(collector)

    collector.append({"ts": "t", "level": "INFO", "source": "a", "message": "not lost"})
    with caplog.at_level(logging.WARNING, logger="demo.logging_bridge"):
        await stream.flush()

    # The local buffer is the fallback, and the LOG tab of this replica still
    # shows its own lines: degraded, not blind.
    assert [e["message"] for e in collector.since("")["entries"]] == ["not lost"]
    assert "logs not published" in caplog.text
