"""The operational logs leave the process, so two replicas tell one story."""

from __future__ import annotations

import asyncio
import logging
import uuid

import pytest
from conftest import needs_postgres

from master_agent.logging_bridge import LogCollector, SharedLogStream
from master_agent.migrations import run_migrations

pytestmark = [needs_postgres, pytest.mark.asyncio]


@pytest.fixture
def key() -> str:
    return f"test-logs:{uuid.uuid4().hex[:12]}"


def a_replica(pool, **kwargs) -> tuple[LogCollector, SharedLogStream]:
    """One process: its own collector and its own view of the shared stream."""
    collector = LogCollector()
    stream = SharedLogStream(pool, **kwargs)
    stream.attach(collector)
    return collector, stream


async def test_two_replicas_are_read_through_one_cursor(pool):
    first, first_stream = a_replica(pool)
    second, second_stream = a_replica(pool)

    first.append(
        {"ts": "t", "level": "INFO", "source": "a", "message": "from the first"}
    )
    second.append(
        {"ts": "t", "level": "INFO", "source": "b", "message": "from the second"}
    )
    await first_stream.flush()
    await second_stream.flush()

    page = await first_stream.since("")

    assert [entry["message"] for entry in page["entries"]] == [
        "from the first",
        "from the second",
    ]


async def test_the_cursor_does_not_repeat_what_was_already_read(pool):
    collector, stream = a_replica(pool)

    collector.append({"ts": "t", "level": "INFO", "source": "a", "message": "one"})
    await stream.flush()
    first = await stream.since("")

    collector.append({"ts": "t", "level": "INFO", "source": "a", "message": "two"})
    await stream.flush()
    second = await stream.since(first["cursor"])

    assert [entry["message"] for entry in second["entries"]] == ["two"]


async def test_reading_twice_from_the_same_cursor_is_idempotent(pool):
    collector, stream = a_replica(pool)
    collector.append({"ts": "t", "level": "INFO", "source": "a", "message": "one"})
    await stream.flush()

    assert (await stream.since(""))["entries"] == (await stream.since(""))["entries"]


async def test_a_trimmed_window_keeps_the_last_lines(pool):
    collector, stream = a_replica(pool, maxlen=2)

    for i in range(5):
        collector.append(
            {"ts": "t", "level": "INFO", "source": "a", "message": f"line {i}"}
        )
        await stream.flush()

    page = await stream.since("")
    later = await stream.since(page["cursor"])

    assert [entry["message"] for entry in page["entries"]] == ["line 3", "line 4"]
    assert later["dropped"] == 0


async def test_the_lines_lost_between_two_reads_are_counted(pool):
    collector, stream = a_replica(pool, maxlen=2)

    collector.append({"ts": "t", "level": "INFO", "source": "a", "message": "line 0"})
    await stream.flush()
    seen = await stream.since("")

    for i in range(1, 5):
        collector.append(
            {"ts": "t", "level": "INFO", "source": "a", "message": f"line {i}"}
        )
        await stream.flush()

    page = await stream.since(seen["cursor"])

    # Lines 1 and 2 were trimmed while the client was not looking: it must be
    # told, or it reads a hole as continuity.
    assert [entry["message"] for entry in page["entries"]] == ["line 3", "line 4"]
    assert page["dropped"] == 2


async def test_the_sequence_is_unique_across_replicas(pool):
    first, first_stream = a_replica(pool)
    second, second_stream = a_replica(pool)

    for i in range(3):
        first.append({"ts": "t", "level": "INFO", "source": "a", "message": f"a{i}"})
        second.append({"ts": "t", "level": "INFO", "source": "b", "message": f"b{i}"})
    await first_stream.flush()
    await second_stream.flush()

    seqs = [entry["seq"] for entry in (await first_stream.since(""))["entries"]]

    # The number comes from the database, not from either process: two replicas
    # that numbered their own lines would collide on the first read.
    assert len(set(seqs)) == len(seqs)


async def test_the_drain_task_publishes_without_being_asked(pool):
    collector, stream = a_replica(pool, flush_seconds=0.01)

    async with stream.running():
        collector.append(
            {"ts": "t", "level": "INFO", "source": "a", "message": "published"}
        )
        await asyncio.sleep(0.1)
        page = await stream.since("")

    assert [entry["message"] for entry in page["entries"]] == ["published"]


async def test_an_unreachable_database_keeps_the_lines_in_the_process(caplog):
    class Unreachable:
        def connection(self):
            raise ConnectionError("database unreachable")

    collector, stream = LogCollector(), SharedLogStream(Unreachable())
    stream.attach(collector)

    collector.append({"ts": "t", "level": "INFO", "source": "a", "message": "not lost"})
    with caplog.at_level(logging.WARNING, logger="master_agent.logging_bridge"):
        await stream.flush()

    # The local buffer is the fallback, and the LOG tab of this replica still
    # shows its own lines: degraded, not blind.
    assert [e["message"] for e in collector.since("")["entries"]] == ["not lost"]
    assert "logs not published" in caplog.text


async def test_seen_notifications_are_shared_through_postgres(pool):
    """Two replicas share one memory of what already landed, through the table."""
    from master_agent.a2a.push import SeenNotifications

    async with pool.connection() as connection:
        await run_migrations(connection)
    replica_a, replica_b = SeenNotifications(pool), SeenNotifications(pool)
    thread = uuid.uuid4().hex

    assert await replica_a.first_time(thread, "task-1", "TASK_STATE_COMPLETED")
    assert not await replica_b.first_time(thread, "task-1", "TASK_STATE_COMPLETED")
