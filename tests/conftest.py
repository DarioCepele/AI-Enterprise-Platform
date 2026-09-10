"""Shared fixtures, against the real Postgres and Redis of demo-infra.

A conversation is a row that has to outlive the process that wrote it, and a
fake in memory would prove nothing about that -- least of all that two writers
on the same thread cannot take the same position.
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from urllib.parse import urlsplit, urlunsplit

import pytest
from dotenv import load_dotenv
from redis.asyncio import Redis

from memory_service.migrations import run_migrations
from memory_service.stores.hot import HotTail
from memory_service.stores.postgres import PostgresTranscripts, build_pool

load_dotenv()

# psycopg's async driver cannot run on Windows' default ProactorEventLoop. In a
# Linux container this never comes up; on a developer's machine it is the first
# thing that fails, so the choice is made here instead of in a README.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def _test_database(dsn: str | None) -> str | None:
    """The tests get a database of their own, next to the real one.

    Sharing it with a running service means a test deleting a thread while
    somebody is looking at it. The name is derived so that a fork does not have
    to configure a second variable.
    """
    if not dsn:
        return None
    configured = os.getenv("MEMORY_TEST_POSTGRES_DSN")
    if configured:
        return configured
    parsed = urlsplit(dsn)
    database = (parsed.path.lstrip("/") or "memoria") + "_test"
    return urlunsplit(parsed._replace(path=f"/{database}"))


POSTGRES_DSN = _test_database(os.getenv("MEMORY_POSTGRES_DSN"))
REDIS_URI = os.getenv("MEMORY_REDIS_URI")

needs_backends = pytest.mark.skipif(
    not (POSTGRES_DSN and REDIS_URI),
    reason="MEMORY_POSTGRES_DSN and MEMORY_REDIS_URI are required in .env",
)


def _create_database_if_missing(dsn: str) -> None:
    """Creates the test database once, so `uv run pytest` is the whole setup."""
    import psycopg

    parsed = urlsplit(dsn)
    database = parsed.path.lstrip("/")
    server = urlunsplit(parsed._replace(path="/postgres"))
    with psycopg.connect(server, autocommit=True) as connection:
        exists = connection.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (database,)
        ).fetchone()
        if not exists:
            connection.execute(f'CREATE DATABASE "{database}"')


@pytest.fixture(scope="session", autouse=True)
def database() -> None:
    if POSTGRES_DSN:
        _create_database_if_missing(POSTGRES_DSN)


@pytest.fixture
def scope() -> str:
    """A scope of its own per test, so two of them do not read each other."""
    return f"test-{uuid.uuid4().hex[:12]}"


@pytest.fixture
async def pool(database):
    connection_pool = build_pool(POSTGRES_DSN or "postgresql://127.0.0.1:5432/memoria_test")
    await connection_pool.open(wait=True)
    try:
        async with connection_pool.connection() as connection:
            await run_migrations(connection)
        yield connection_pool
    finally:
        await connection_pool.close()


@pytest.fixture
async def transcripts(pool) -> PostgresTranscripts:
    return PostgresTranscripts(pool)


@pytest.fixture
async def redis_client() -> Redis:
    client = Redis.from_url(REDIS_URI or "redis://127.0.0.1:6379/0", decode_responses=True)
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
async def hot(redis_client: Redis) -> HotTail:
    return HotTail(redis_client, ttl_seconds=60, max_messages=10)
