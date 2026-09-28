"""Schema changes that travel with the code.

Numbered, idempotent, recorded, applied at startup -- and serialised by an
advisory lock, so two replicas starting together apply each migration once.
The runner is the platform's (`platform_core.migrations`); the schema is this
service's own.
"""

from __future__ import annotations

from typing import Any

from platform_core.migrations import Migration, applied_versions
from platform_core.migrations import run_migrations as _run

INSTANCES = """
CREATE TABLE IF NOT EXISTS process_instances (
    id                  uuid PRIMARY KEY,
    scope               text NOT NULL,
    process_id          text NOT NULL,
    process_version     integer NOT NULL,
    status              text NOT NULL,
    input               jsonb NOT NULL DEFAULT '{}'::jsonb,
    context             jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS instances_by_scope_and_status
    ON process_instances (scope, status, created_at DESC);
"""

STEPS = """
CREATE TABLE IF NOT EXISTS instance_steps (
    instance_id   uuid NOT NULL REFERENCES process_instances (id) ON DELETE CASCADE,
    step_id       text NOT NULL,
    status        text NOT NULL,
    owner         text,
    output        jsonb,
    note          text,
    started_at    timestamptz,
    ended_at      timestamptz,
    PRIMARY KEY (instance_id, step_id)
);
"""


EFFECTS = """
CREATE TABLE IF NOT EXISTS side_effects (
    key          text PRIMARY KEY,
    instance_id  uuid NOT NULL REFERENCES process_instances (id) ON DELETE CASCADE,
    step_id      text NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now()
);
"""


STEP_TASKS = """
ALTER TABLE instance_steps ADD COLUMN IF NOT EXISTS task_id text;
ALTER TABLE instance_steps ADD COLUMN IF NOT EXISTS question text;
"""


INSTANCE_NOTE = """
ALTER TABLE process_instances ADD COLUMN IF NOT EXISTS note text;
"""


EVENTS = """
CREATE TABLE IF NOT EXISTS instance_events (
    id bigserial PRIMARY KEY,
    instance_id uuid NOT NULL REFERENCES process_instances (id) ON DELETE CASCADE,
    step_id text,
    kind text NOT NULL,
    data jsonb NOT NULL DEFAULT '{}'::jsonb,
    at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS instance_events_by_instance
    ON instance_events (instance_id, id);
"""


MIGRATIONS: tuple[Migration, ...] = (
    Migration(1, "instances and steps", (INSTANCES, STEPS)),
    Migration(2, "effects that must not repeat", (EFFECTS,)),
    Migration(3, "the remote task a step waits on", (STEP_TASKS,)),
    Migration(4, "why an instance stopped where it stopped", (INSTANCE_NOTE,)),
    Migration(5, "the history of what happened, in order", (EVENTS,)),
)

LATEST_VERSION = max(migration.version for migration in MIGRATIONS)


__all__ = [
    "LATEST_VERSION",
    "MIGRATIONS",
    "Migration",
    "applied_versions",
    "run_migrations",
]

LOCK = "process-service"


async def run_migrations(connection: Any) -> list[Migration]:
    """Applies the missing migrations, in order, one replica at a time."""
    return await _run(connection, MIGRATIONS, lock=LOCK)
