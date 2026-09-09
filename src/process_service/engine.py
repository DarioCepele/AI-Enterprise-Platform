"""What makes an instance move, and what makes it survive.

Every step is a DBOS step: its result is written down when it finishes, so a
process that dies and comes back does not run it again. The instance itself is
a DBOS workflow keyed on the instance id, which is what makes starting it twice
harmless.

What is not here yet, by design: agents and approvals suspend the instance and
wait for the blocks that come after this one. A suspended instance is a row, not
a held connection.
"""
from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from dbos import DBOS, SetWorkflowID

from .catalog import Catalog
from .definitions import ProcessDefinition, Step
from .expressions import ConditionError, evaluate
from .store import InstanceStore
from .tools import get_tool

logger = logging.getLogger(__name__)

PENDING = "pending"
RUNNING = "running"
WAITING = "waiting"
COMPLETED = "completed"
FAILED = "failed"
SKIPPED = "skipped"

SUSPENDING_TYPES = ("agent", "approval", "open_goal")

_ENGINE: "Engine | None" = None


class Engine:
    """Runs instances. One per process, wired at startup."""

    def __init__(self, catalog: Catalog, store: InstanceStore) -> None:
        self.catalog = catalog
        self.store = store

    async def start(self, instance_id: UUID, scope: str) -> None:
        """Starts the workflow of an instance, once.

        The workflow id **is** the instance id: asking twice for the same
        instance to run does not run it twice, which is the cheapest form of
        idempotency available.
        """
        with SetWorkflowID(str(instance_id)):
            await DBOS.start_workflow_async(advance_instance, str(instance_id), scope)


def use_engine(engine: Engine) -> None:
    global _ENGINE
    _ENGINE = engine


def current_engine() -> Engine:
    if _ENGINE is None:
        raise RuntimeError("the engine has not been wired: call use_engine() at startup")
    return _ENGINE


@DBOS.step()
async def run_tool_step(
    instance_id: str, step_id: str, tool_name: str, context: dict[str, Any]
) -> dict[str, Any]:
    """One tool call, written down when it returns.

    This is the boundary DBOS records: after a crash the recovered workflow
    reads the result from here instead of calling the tool again -- which is
    what "the invoice is not sent twice" means in practice.
    """
    engine = current_engine()
    output = get_tool(tool_name)(context)
    await engine.store.finish_step(
        instance_id=UUID(instance_id), step_id=step_id, status=COMPLETED, output=output
    )
    logger.info("Instance %s: step %s done by tool %s.", instance_id, step_id, tool_name)
    return output


@DBOS.step()
async def record_effect(instance_id: str, step_id: str, key: str) -> bool:
    """Writes the effect of a step under its idempotency key.

    Returns whether this was the first time. The key is unique in the database,
    so two attempts of the same step leave one effect even if they run on two
    different replicas.
    """
    engine = current_engine()
    first_time = await engine.store.record_effect(
        instance_id=UUID(instance_id), step_id=step_id, key=key
    )
    if not first_time:
        logger.info("Instance %s: effect '%s' already applied, not repeated.", instance_id, key)
    return first_time


@DBOS.step()
async def decide_branch(
    instance_id: str, step_id: str, branches: list[dict[str, str]], context: dict[str, Any]
) -> str | None:
    """Picks the first branch whose condition holds, and writes down which one.

    Recorded as a step because "why did it go that way" is a question somebody
    asks months later, when the context that answered it is gone.
    """
    engine = current_engine()
    for branch in branches:
        if evaluate(branch["when"], context):
            await engine.store.finish_step(
                instance_id=UUID(instance_id),
                step_id=step_id,
                status=COMPLETED,
                output={"when": branch["when"], "goto": branch["goto"]},
            )
            logger.info(
                "Instance %s: step %s took '%s' because of '%s'.",
                instance_id,
                step_id,
                branch["goto"],
                branch["when"],
            )
            return branch["goto"]

    await engine.store.finish_step(
        instance_id=UUID(instance_id),
        step_id=step_id,
        status=FAILED,
        output=None,
        note="no branch matched",
    )
    return None


@DBOS.step()
async def suspend(instance_id: str, step_id: str, reason: str) -> None:
    engine = current_engine()
    await engine.store.mark_step(instance_id=UUID(instance_id), step_id=step_id, status=WAITING)
    await engine.store.set_status(instance_id=UUID(instance_id), status=WAITING)
    logger.info("Instance %s waits on step %s: %s.", instance_id, step_id, reason)


@DBOS.step()
async def read_plan(instance_id: str, scope: str) -> dict[str, Any]:
    """What the instance was started with, read once and written down.

    A workflow that read mutable rows on every replay would take a different
    path the second time -- and a different path means the recorded steps stop
    matching, which is DBOS telling us the run is not reproducible.
    """
    engine = current_engine()
    instance = await engine.store.get(scope=scope, instance_id=UUID(instance_id))
    if instance is None:
        raise RuntimeError(f"instance {instance_id} does not exist in scope {scope}")
    return {
        "process_id": instance.process_id,
        "process_version": instance.process_version,
        "context": {**instance.input, **instance.context},
    }


@DBOS.step()
async def set_instance_status(instance_id: str, status: str) -> None:
    engine = current_engine()
    await engine.store.set_status(instance_id=UUID(instance_id), status=status)


@DBOS.workflow()
async def advance_instance(instance_id: str, scope: str) -> str:
    """Walks an instance as far as it can go, and says where it stopped."""
    engine = current_engine()
    plan = await read_plan(instance_id, scope)

    # The version comes from the instance, never from the catalogue's latest: a
    # definition that changed after this instance started is another process.
    definition = engine.catalog.get(plan["process_id"], plan["process_version"])

    await set_instance_status(instance_id, RUNNING)
    context: dict[str, Any] = dict(plan["context"])
    done: set[str] = set()
    ready = [step.id for step in definition.entry_steps()]

    while ready:
        step_id = ready.pop(0)
        step = definition.step(step_id)
        if step is None or step_id in done:
            continue

        if step.type in SUSPENDING_TYPES:
            await suspend(instance_id, step_id, f"{step.type} step")
            return WAITING

        outcome = await _run_step(instance_id, step, context)
        if outcome is FAILED:
            await set_instance_status(instance_id, FAILED)
            return FAILED

        done.add(step_id)
        if isinstance(outcome, dict):
            context.update({key: value for key, value in outcome.items() if value is not None})

        ready.extend(_next_steps(definition, step, outcome, done))

    await set_instance_status(instance_id, COMPLETED)
    return COMPLETED


async def _run_step(instance_id: str, step: Step, context: dict[str, Any]) -> Any:
    if step.type == "decision":
        branches = [branch.model_dump() for branch in step.branches]
        try:
            return await decide_branch(instance_id, step.id, branches, context)
        except ConditionError:
            logger.error("Instance %s: step %s has a condition that does not hold up.", instance_id, step.id)
            return FAILED

    if step.idempotency_key:
        key = f"{instance_id}:{step.id}:{context.get(step.idempotency_key, '')}"
        if not await record_effect(instance_id, step.id, key):
            return {}

    return await run_tool_step(instance_id, step.id, step.tool or "", context)


def _next_steps(
    definition: ProcessDefinition, step: Step, outcome: Any, done: set[str]
) -> list[str]:
    """What becomes runnable after this step.

    A branch hands control to exactly one target. Everything else follows the
    dependencies, and a step runs only once every step it waits for is done.
    """
    if step.type == "decision":
        return [outcome] if isinstance(outcome, str) else []

    return [
        candidate.id
        for candidate in definition.steps
        if step.id in candidate.depends_on
        and candidate.id not in done
        and all(dependency in done for dependency in candidate.depends_on)
    ]
