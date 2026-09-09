"""Shared fixtures. The instance store is tested against a real Postgres.

An instance is a row that has to outlive the process that wrote it: a fake in
memory would prove nothing about that.
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from urllib.parse import urlsplit, urlunsplit

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

def _test_database(dsn: str | None) -> str | None:
    """The tests get a database of their own, next to the real one.

    Sharing it with a running service means the service tries to recover the
    workflows the tests left behind -- processes it has never heard of -- and
    says so in the log of whoever is using it. The name is derived so that a
    fork does not have to configure a second variable.
    """
    if not dsn:
        return None
    configured = os.getenv("PROCESS_TEST_POSTGRES_DSN")
    if configured:
        return configured
    parsed = urlsplit(dsn)
    database = (parsed.path.lstrip("/") or "processes") + "_test"
    return urlunsplit(parsed._replace(path=f"/{database}"))


POSTGRES_DSN = _test_database(os.getenv("PROCESS_POSTGRES_DSN"))

needs_postgres = pytest.mark.skipif(
    not POSTGRES_DSN, reason="PROCESS_POSTGRES_DSN is required in .env"
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


@pytest.fixture
def scope() -> str:
    """A scope of its own per test, so instances do not read each other."""
    return f"test-{uuid.uuid4().hex[:12]}"


@pytest.fixture(scope="session", autouse=True)
def database() -> None:
    if POSTGRES_DSN:
        _create_database_if_missing(POSTGRES_DSN)


@pytest.fixture
async def pool(database):
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
def dbos(database):
    """One DBOS per test session: it owns tables and a recovery thread.

    Whatever an earlier run left waiting is cancelled before this one starts:
    the recovery thread would otherwise pick up a process that belonged to
    another test file, and run it against the catalogue of this one.
    """
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
    for leftover in DBOS.list_workflows(status=["PENDING", "ENQUEUED"]):
        DBOS.cancel_workflow(leftover.workflow_id)
    try:
        yield DBOS
    finally:
        for pending in DBOS.list_workflows(status=["PENDING", "ENQUEUED"]):
            DBOS.cancel_workflow(pending.workflow_id)
        DBOS.destroy()
