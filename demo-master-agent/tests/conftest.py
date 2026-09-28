import asyncio
import os
import sys
from urllib.parse import urlsplit, urlunsplit

import pytest
from psycopg_pool import AsyncConnectionPool

from master_agent.agents.master import build_master_agent
from master_agent.chat_clients.fake import (
    FakeStreamingChatClient,
    ToolCallingFakeClient,
)
from master_agent.migrations import run_migrations
from master_agent.server.app import create_app
from master_agent.tools.plan_tools import PlanStore


@pytest.fixture
def app():
    """App with a client that emits text only."""
    agent = build_master_agent(
        chat_client=FakeStreamingChatClient(chunks=["hello ", "world"])
    )
    return create_app(agent=agent)


@pytest.fixture
def tool_app():
    """App with a client that calls ui_table on the first round."""
    agent = build_master_agent(
        chat_client=ToolCallingFakeClient(
            tool_name="ui_table",
            tool_args={
                "title": "Comparison",
                "columns": ["Topic", "A", "B"],
                "rows": [["Coverage", "empty", "full"]],
            },
            final_text="Here is the comparison.",
        )
    )
    return create_app(agent=agent)


@pytest.fixture
def plan_app():
    """App with a client that writes a plan on the first round."""
    agent = build_master_agent(
        chat_client=ToolCallingFakeClient(
            tool_name="todo_write",
            tool_args={
                "steps": [
                    {
                        "id": 1,
                        "title": "First step",
                        "detail": "detail",
                        "source": "ui_table",
                    }
                ]
            },
            final_text="Plan ready.",
        ),
        plan_store=PlanStore(),
    )
    return create_app(agent=agent)


# --- a real Postgres, shared by the suites that need one ---

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def _test_database(dsn: str | None) -> str | None:
    """A test database next to the real one, as in the other services."""
    if not dsn:
        return None
    parsed = urlsplit(dsn)
    database = (parsed.path.lstrip("/") or "master") + "_test"
    return urlunsplit(parsed._replace(path=f"/{database}"))


POSTGRES_DSN = _test_database(os.getenv("MASTER_POSTGRES_DSN"))

needs_postgres = pytest.mark.skipif(
    not POSTGRES_DSN,
    reason="MASTER_POSTGRES_DSN is required: this test needs a real Postgres",
)


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
    connection_pool = AsyncConnectionPool(
        POSTGRES_DSN or "", min_size=1, max_size=4, open=False
    )
    await connection_pool.open(wait=True)
    try:
        async with connection_pool.connection() as connection:
            await run_migrations(connection)
            # Each test starts from its own window: the logs are shared by
            # definition, so there is no scope to keep them apart.
            await connection.execute("TRUNCATE operational_logs")
        yield connection_pool
    finally:
        await connection_pool.close()
