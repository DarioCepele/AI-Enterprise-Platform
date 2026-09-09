"""Shared fixtures. The instance store is tested against a real Postgres.

An instance is a row that has to outlive the process that wrote it: a fake in
memory would prove nothing about that.
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid

import pytest
from dotenv import load_dotenv

from process_service.migrations import run_migrations
from process_service.store import InstanceStore, build_pool

load_dotenv()

# psycopg's async driver cannot run on Windows' default ProactorEventLoop. In a
# Linux container this never comes up; on a developer's machine it is the first
# thing that fails, so the choice is made here instead of in a README.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

POSTGRES_DSN = os.getenv("PROCESS_POSTGRES_DSN")

needs_postgres = pytest.mark.skipif(
    not POSTGRES_DSN, reason="PROCESS_POSTGRES_DSN is required in .env"
)


@pytest.fixture
def scope() -> str:
    """A scope of its own per test, so instances do not read each other."""
    return f"test-{uuid.uuid4().hex[:12]}"


@pytest.fixture
async def pool():
    connection_pool = build_pool(POSTGRES_DSN or "postgresql://127.0.0.1:5432/processes")
    await connection_pool.open(wait=True)
    try:
        async with connection_pool.connection() as connection:
            await run_migrations(connection)
        yield connection_pool
    finally:
        await connection_pool.close()


@pytest.fixture
async def store(pool) -> InstanceStore:
    return InstanceStore(pool)


@pytest.fixture(scope="session")
def dbos():
    """One DBOS per test session: it owns tables and a recovery thread."""
    from dbos import DBOS

    DBOS(
        config={
            "name": "process-service-tests",
            "system_database_url": POSTGRES_DSN or "",
            "run_admin_server": False,
            "enable_otlp": False,
        }
    )
    DBOS.launch()
    try:
        yield DBOS
    finally:
        DBOS.destroy()
