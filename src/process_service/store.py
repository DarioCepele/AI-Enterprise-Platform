"""Where instances live.

Plain SQL on Postgres, one connection pool per process. The engine that runs the
steps arrives next; this is the part that has to survive it -- an instance is a
row, and a row outlives the process that created it.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from .definitions import ProcessDefinition
from .models import Instance, StepState

logger = logging.getLogger(__name__)

RUNNING = "running"
PENDING = "pending"


def build_pool(dsn: str, min_size: int = 1, max_size: int = 10) -> AsyncConnectionPool:
    """One pool per process. Sized for a laboratory: raise both for real load."""
    return AsyncConnectionPool(dsn, min_size=min_size, max_size=max_size, open=False)


class InstanceStore:
    """Instances and the state of their steps."""

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def create(
        self,
        *,
        scope: str,
        definition: ProcessDefinition,
        payload: dict[str, Any],
    ) -> Instance:
        """Starts an instance on **this** version of the definition.

        The version is copied into the row on purpose: the catalogue will move
        on, and an instance has to keep finishing the process it started.
        """
        instance_id = uuid4()
        async with self._pool.connection() as connection:
            await connection.execute(
                """
                INSERT INTO process_instances
                    (id, scope, process_id, process_version, status, input, context)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    instance_id,
                    scope,
                    definition.id,
                    definition.version,
                    PENDING,
                    json.dumps(payload),
                    json.dumps({}),
                ),
            )
            for step in definition.steps:
                await connection.execute(
                    """
                    INSERT INTO instance_steps (instance_id, step_id, status, owner)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (instance_id, step.id, PENDING, step.owner),
                )
        logger.info(
            "Instance %s of %s@%d created.", instance_id, definition.id, definition.version
        )
        return await self.get(scope=scope, instance_id=instance_id)  # type: ignore[return-value]

    async def get(self, *, scope: str, instance_id: UUID) -> Instance | None:
        async with self._pool.connection() as connection, connection.cursor(
            row_factory=dict_row
        ) as cursor:
            await cursor.execute(
                "SELECT * FROM process_instances WHERE id = %s AND scope = %s",
                (instance_id, scope),
            )
            found = await cursor.fetchone()
            if found is None:
                return None
            await cursor.execute(
                "SELECT * FROM instance_steps WHERE instance_id = %s ORDER BY step_id",
                (instance_id,),
            )
            steps = await cursor.fetchall()
        return _instance_of(found, steps)

    async def list(
        self,
        *,
        scope: str,
        status: str | None = None,
        limit: int = 50,
    ) -> list[Instance]:
        query = "SELECT * FROM process_instances WHERE scope = %s"
        parameters: list[Any] = [scope]
        if status:
            query += " AND status = %s"
            parameters.append(status)
        query += " ORDER BY created_at DESC LIMIT %s"
        parameters.append(limit)

        async with self._pool.connection() as connection, connection.cursor(
            row_factory=dict_row
        ) as cursor:
            await cursor.execute(query, parameters)
            rows = await cursor.fetchall()
            instances = []
            for row in rows:
                await cursor.execute(
                    "SELECT * FROM instance_steps WHERE instance_id = %s ORDER BY step_id",
                    (row["id"],),
                )
                instances.append(_instance_of(row, await cursor.fetchall()))
        return instances

    async def mark_step(self, *, instance_id: UUID, step_id: str, status: str) -> None:
        async with self._pool.connection() as connection:
            await connection.execute(
                """
                UPDATE instance_steps
                   SET status = %s,
                       started_at = COALESCE(started_at, now())
                 WHERE instance_id = %s AND step_id = %s
                """,
                (status, instance_id, step_id),
            )

    async def waiting_on(
        self,
        *,
        instance_id: UUID,
        step_id: str,
        status: str,
        task_id: str | None = None,
        question: str | None = None,
    ) -> None:
        """Writes what a suspended step is waiting for, so it is answerable."""
        async with self._pool.connection() as connection:
            await connection.execute(
                """
                UPDATE instance_steps
                   SET status = %s,
                       task_id = COALESCE(%s, task_id),
                       question = %s,
                       started_at = COALESCE(started_at, now())
                 WHERE instance_id = %s AND step_id = %s
                """,
                (status, task_id, question, instance_id, step_id),
            )

    async def finish_step(
        self,
        *,
        instance_id: UUID,
        step_id: str,
        status: str,
        output: dict[str, Any] | None = None,
        note: str | None = None,
    ) -> None:
        async with self._pool.connection() as connection:
            await connection.execute(
                """
                UPDATE instance_steps
                   SET status = %s,
                       output = %s,
                       note = %s,
                       started_at = COALESCE(started_at, now()),
                       ended_at = now()
                 WHERE instance_id = %s AND step_id = %s
                """,
                (status, json.dumps(output) if output is not None else None, note,
                 instance_id, step_id),
            )

    async def record_effect(self, *, instance_id: UUID, step_id: str, key: str) -> bool:
        """Writes an effect under its key, and says whether it is the first one.

        The uniqueness lives in the database, not in the process: two replicas
        retrying the same step still leave one effect.
        """
        async with self._pool.connection() as connection:
            done = await connection.execute(
                """
                INSERT INTO side_effects (key, instance_id, step_id)
                VALUES (%s, %s, %s)
                ON CONFLICT (key) DO NOTHING
                """,
                (key, instance_id, step_id),
            )
            return done.rowcount == 1

    async def effects_of(self, *, instance_id: UUID) -> list[str]:
        async with self._pool.connection() as connection:
            rows = await (
                await connection.execute(
                    "SELECT key FROM side_effects WHERE instance_id = %s ORDER BY created_at",
                    (instance_id,),
                )
            ).fetchall()
        return [row[0] for row in rows]

    async def set_status(self, *, instance_id: UUID, status: str) -> None:
        async with self._pool.connection() as connection:
            await connection.execute(
                "UPDATE process_instances SET status = %s, updated_at = now() WHERE id = %s",
                (status, instance_id),
            )

    async def ping(self) -> None:
        """Raises if the database does not answer."""
        async with self._pool.connection() as connection:
            await connection.execute("SELECT 1")


def _instance_of(row: dict[str, Any], steps: list[dict[str, Any]]) -> Instance:
    return Instance(
        id=row["id"],
        scope=row["scope"],
        process_id=row["process_id"],
        process_version=row["process_version"],
        status=row["status"],
        input=row["input"],
        context=row["context"],
        created_at=_moment(row["created_at"]),
        updated_at=_moment(row["updated_at"]),
        steps=[
            StepState(
                step_id=step["step_id"],
                status=step["status"],
                owner=step["owner"],
                task_id=step.get("task_id"),
                question=step.get("question"),
                output=step["output"],
                note=step["note"],
                started_at=_moment(step["started_at"]),
                ended_at=_moment(step["ended_at"]),
            )
            for step in steps
        ],
    )


def _moment(value: datetime | None) -> datetime | None:
    return value


async def prepare(connection: AsyncConnection) -> None:
    """Run the migrations on a connection that owns its transaction."""
    from .migrations import run_migrations

    await run_migrations(connection)
