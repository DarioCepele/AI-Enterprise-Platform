"""Migrations against a real Postgres: applied once, even by replicas racing."""

from __future__ import annotations

import asyncio
import os
import uuid
from urllib.parse import urlsplit, urlunsplit

import pytest

from platform_core.migrations import (
    Migration,
    lock_key,
    missing_migrations,
    run_migrations,
)

DSN = os.getenv("PLATFORM_TEST_POSTGRES_DSN", "")

pytestmark = pytest.mark.skipif(
    not DSN, reason="PLATFORM_TEST_POSTGRES_DSN is required: this needs a real Postgres"
)


def test_the_lock_key_is_a_signed_64_bit_number():
    key = lock_key("memory-service")
    assert -(2**63) <= key < 2**63
    assert key == lock_key("memory-service")
    assert key != lock_key("process-service")


@pytest.fixture
async def database():
    import psycopg

    name = f"migrations_{uuid.uuid4().hex[:10]}"
    parsed = urlsplit(DSN)
    server = urlunsplit(parsed._replace(path="/postgres"))
    async with await psycopg.AsyncConnection.connect(server, autocommit=True) as admin:
        await admin.execute(f'CREATE DATABASE "{name}"')
    try:
        yield urlunsplit(parsed._replace(path=f"/{name}"))
    finally:
        async with await psycopg.AsyncConnection.connect(
            server, autocommit=True
        ) as admin:
            await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


MIGRATIONS = (
    Migration(1, "things", ("CREATE TABLE IF NOT EXISTS things (id int PRIMARY KEY)",)),
    Migration(2, "an extension", ("CREATE EXTENSION IF NOT EXISTS vector",)),
)


async def test_two_replicas_racing_apply_each_migration_once(database):
    from psycopg_pool import AsyncConnectionPool

    pool = AsyncConnectionPool(database, min_size=2, max_size=4, open=False)
    await pool.open(wait=True)
    try:

        async def replica() -> list[Migration]:
            async with pool.connection() as connection:
                return await run_migrations(connection, MIGRATIONS, lock="test-service")

        first, second = await asyncio.gather(replica(), replica())
        assert sorted(m.version for m in first + second) == [1, 2]

        async with pool.connection() as connection:
            assert await missing_migrations(connection, MIGRATIONS) == []
            again = await run_migrations(connection, MIGRATIONS, lock="test-service")
        assert again == []
    finally:
        await pool.close()
