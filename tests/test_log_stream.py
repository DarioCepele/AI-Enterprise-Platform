"""The operational logs leave the process, so two replicas tell one story."""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import uuid
from urllib.parse import urlsplit, urlunsplit

import pytest
from psycopg_pool import AsyncConnectionPool

from demo.logging_bridge import LogCollector, SharedLogStream
from demo.migrations import run_migrations

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def _test_database(dsn: str | None) -> str | None:
    """Un database dei test accanto a quello vero, come negli altri servizi."""
    if not dsn:
        return None
    parsed = urlsplit(dsn)
    database = (parsed.path.lstrip("/") or "agente") + "_test"
    return urlunsplit(parsed._replace(path=f"/{database}"))


POSTGRES_DSN = _test_database(os.getenv("DEMO_POSTGRES_DSN"))

needs_postgres = pytest.mark.skipif(
    not POSTGRES_DSN, reason="DEMO_POSTGRES_DSN is required: this test needs a real Postgres"
)

pytestmark = [needs_postgres, pytest.mark.asyncio]


def _create_database_if_missing(dsn: str) -> None:
    import psycopg

    parsed = urlsplit(dsn)
    database = parsed.path.lstrip("/")
    server = urlunsplit(parsed._replace(path="/postgres"))
    with psycopg.connect(server, autocommit=True) as connection:
        if not connection.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (database,)
        ).fetchone():
            connection.execute(f'CREATE DATABASE "{database}"')


@pytest.fixture
async def pool():
    _create_database_if_missing(POSTGRES_DSN or "")
    connection_pool = AsyncConnectionPool(POSTGRES_DSN or "", min_size=1, max_size=4, open=False)
    await connection_pool.open(wait=True)
    try:
        async with connection_pool.connection() as connection:
            await run_migrations(connection)
            # Ogni test parte dalla propria finestra: i log sono condivisi per
            # definizione, quindi non c'e' uno scope che li separi.
            await connection.execute("TRUNCATE operational_logs")
        yield connection_pool
    finally:
        await connection_pool.close()


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

    first.append({"ts": "t", "level": "INFO", "source": "a", "message": "from the first"})
    second.append({"ts": "t", "level": "INFO", "source": "b", "message": "from the second"})
    await first_stream.flush()
    await second_stream.flush()

    page = await first_stream.since("")

    assert [entry["message"] for entry in page["entries"]] == ["from the first", "from the second"]


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
        collector.append({"ts": "t", "level": "INFO", "source": "a", "message": f"line {i}"})
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
        collector.append({"ts": "t", "level": "INFO", "source": "a", "message": f"line {i}"})
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
        collector.append({"ts": "t", "level": "INFO", "source": "a", "message": "published"})
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
    with caplog.at_level(logging.WARNING, logger="demo.logging_bridge"):
        await stream.flush()

    # The local buffer is the fallback, and the LOG tab of this replica still
    # shows its own lines: degraded, not blind.
    assert [e["message"] for e in collector.since("")["entries"]] == ["not lost"]
    assert "logs not published" in caplog.text
