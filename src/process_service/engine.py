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

import inspect
import logging
from typing import Any
from uuid import UUID

from dbos import DBOS, SetWorkflowID

from .agents import NEEDS_INPUT, AgentGateway
from .catalog import Catalog
from .definitions import ProcessDefinition, Step
from .expressions import ConditionError, evaluate
from .observability import current_trace, working_on
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


WAITING_HUMAN = "waiting_human"
WAITING_APPROVAL = "waiting_approval"
ESCALATED = "escalated"
REJECTED = "rejected"
APPROVED = "approved"
COMPENSATED = "compensated"
COMPENSATION_FAILED = "compensation_failed"

DEFAULT_AGENT_TIMEOUT_SECONDS = 3600.0

# An agent that keeps asking is an agent that is not going to finish: after this
# many rounds the step is handed on rather than looping in front of a person.
MAX_CLARIFICATIONS = 5

# Nobody is at their desk forever, and a process that waits forever is a process
# nobody trusts. A week, unless the definition says otherwise.
DEFAULT_APPROVAL_TIMEOUT_SECONDS = 7 * 24 * 3600.0


class Engine:
    """Runs instances. One per process, wired at startup."""

    def __init__(
        self,
        catalog: Catalog,
        store: InstanceStore,
        agents: AgentGateway | None = None,
    ) -> None:
        self.catalog = catalog
        self.store = store
        self.agents = agents

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
    if inspect.isawaitable(output):
        # A tool that talks to something else should not hold the loop while it
        # does: an async tool is the ordinary case, not the exception.
        output = await output
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
async def ask_agent(
    instance_id: str, step_id: str, scope: str, owner: str, question: str
) -> dict[str, Any]:
    """Starts the remote task and writes down which one it is.

    Recorded as a step because asking twice would start two tasks: after a
    crash the recovered instance reads the task it already started.
    """
    engine = current_engine()
    if engine.agents is None:
        raise RuntimeError("no agents are configured: an agent step cannot run")
    started = await engine.agents.ask(
        agent=owner, question=question, scope=scope, instance_id=instance_id, step_id=step_id
    )
    await engine.store.waiting_on(
        instance_id=UUID(instance_id),
        step_id=step_id,
        status=WAITING,
        task_id=started["task_id"],
    )
    await engine.store.set_status(instance_id=UUID(instance_id), status=WAITING)
    return started


def approval_topic(step_id: str) -> str:
    """The topic an approver decides on, one per step."""
    return f"{step_id}:approval"


@DBOS.step()
async def request_approval(instance_id: str, step_id: str, approvers: list[str]) -> None:
    """Puts the step in front of whoever can decide, and writes down who that is."""
    engine = current_engine()
    waiting_for = ", ".join(approvers) if approvers else "anyone"
    await engine.store.waiting_on(
        instance_id=UUID(instance_id),
        step_id=step_id,
        status=WAITING_APPROVAL,
        question=f"waiting for a decision by {waiting_for}",
    )
    await engine.store.set_status(instance_id=UUID(instance_id), status=WAITING_APPROVAL)
    logger.info("Instance %s step %s waits for %s to decide.", instance_id, step_id, waiting_for)


async def _run_approval_step(instance_id: str, step: Step, context: dict[str, Any]) -> Any:
    """Stops the instance in front of a person, for as long as the step allows.

    The wait costs nothing: the workflow is not running, the row says what is
    being waited for, and the decision arrives on the topic of this step. Which
    is what makes "the approval comes tomorrow, after a restart" an ordinary
    case rather than an exception.
    """
    await request_approval(instance_id, step.id, list(step.approvers))

    timeout = step.timeout_seconds or DEFAULT_APPROVAL_TIMEOUT_SECONDS
    decision = await DBOS.recv_async(topic=approval_topic(step.id), timeout_seconds=timeout)
    if decision is None:
        return await _timed_out(instance_id, step, timeout)

    output = {
        "decision": decision.get("decision", REJECTED),
        "by": decision.get("by", ""),
        "note": decision.get("note"),
    }
    await write_step_output(instance_id, step.id, output)
    logger.info(
        "Instance %s step %s: %s by %s.",
        instance_id,
        step.id,
        output["decision"],
        output["by"] or "somebody",
    )
    return output


def human_topic(step_id: str) -> str:
    """The topic a person answers on, kept apart from what the agent notifies."""
    return f"{step_id}:human"


@DBOS.step()
async def reply_to_agent(
    instance_id: str, step_id: str, scope: str, owner: str, task_id: str, answer: str
) -> None:
    """Hands the person's answer to the agent that asked for it."""
    engine = current_engine()
    if engine.agents is None:
        raise RuntimeError("no agents are configured: an agent step cannot run")
    await engine.agents.reply(
        agent=owner,
        answer=answer,
        scope=scope,
        instance_id=instance_id,
        step_id=step_id,
        task_id=task_id,
    )
    await engine.store.waiting_on(
        instance_id=UUID(instance_id), step_id=step_id, status=WAITING, task_id=task_id
    )
    await engine.store.set_status(instance_id=UUID(instance_id), status=WAITING)


@DBOS.step()
async def note_question(instance_id: str, step_id: str, question: str) -> None:
    """The agent stopped and asked something: the step now waits for a person."""
    engine = current_engine()
    await engine.store.waiting_on(
        instance_id=UUID(instance_id),
        step_id=step_id,
        status=WAITING_HUMAN,
        question=question,
    )
    await engine.store.set_status(instance_id=UUID(instance_id), status=WAITING_HUMAN)
    logger.info("Instance %s step %s waits for an answer: %s", instance_id, step_id, question)


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
async def note_trace(instance_id: str) -> None:
    """Writes the trace this run belongs to into the history.

    The logs carry the instance, and the history carries the trace: from either
    end of a question -- a line in a collector, a row somebody is looking at --
    the other one can be found. Without a collector there is no trace, and
    nothing is written.
    """
    trace_id = current_trace()
    if not trace_id:
        return
    engine = current_engine()
    await engine.store.record_event(
        instance_id=UUID(instance_id), kind="trace", data={"trace_id": trace_id}
    )


@DBOS.step()
async def set_instance_status(instance_id: str, status: str, note: str | None = None) -> None:
    engine = current_engine()
    await engine.store.set_status(instance_id=UUID(instance_id), status=status, note=note)


def step_workflow_id(instance_id: str, step_id: str) -> str:
    """The workflow of one step of one instance.

    Derived rather than random, so that whatever wakes a step -- a webhook, an
    approval, a retry of the same instance -- addresses the one workflow that is
    waiting for it, and starting the same step twice starts nothing.
    """
    return f"{instance_id}:{step_id}"


@DBOS.workflow()
async def run_step(
    instance_id: str, scope: str, step_id: str, context: dict[str, Any]
) -> dict[str, Any]:
    """One step, in a workflow of its own.

    A step gets its own workflow so that several can be running -- or waiting --
    at the same time without the instance having to choose between them, and so
    that a long wait belongs to the step that is waiting rather than to the
    whole process.
    """
    with working_on(instance_id):
        return await _one_step(instance_id, scope, step_id, context)


async def _one_step(
    instance_id: str, scope: str, step_id: str, context: dict[str, Any]
) -> dict[str, Any]:
    engine = current_engine()
    plan = await read_plan(instance_id, scope)
    definition = engine.catalog.get(plan["process_id"], plan["process_version"])
    step = definition.step(step_id)
    if step is None:
        raise RuntimeError(f"step '{step_id}' is not in process '{plan['process_id']}'")

    try:
        return await _do_step(instance_id, scope, step, context)
    except Exception as error:  # noqa: BLE001 - the step failed, the instance has not
        # Whatever the step was doing, the process is entitled to hear that it
        # did not work and which one it was: an exception that escaped here
        # would take the whole instance down with a stack trace instead.
        await fail_step(instance_id, step.id, f"{type(error).__name__}: {error}")
        logger.warning("Instance %s step %s failed.", instance_id, step.id, exc_info=True)
        return _stopped(step.id, FAILED)


async def _do_step(
    instance_id: str, scope: str, step: Step, context: dict[str, Any]
) -> dict[str, Any]:
    step_id = step.id
    if step.type == "agent":
        outcome = await _run_agent_step(instance_id, scope, step, context)
        if outcome is None:
            return _handed_on(step)
    elif step.type == "approval":
        outcome = await _run_approval_step(instance_id, step, context)
        if outcome is None:
            return _handed_on(step)
        if outcome["decision"] != APPROVED:
            # A refusal is not a failure: it is an answer, and the instance stops
            # in a state that says which one it got.
            return _stopped(step_id, REJECTED, output=outcome)
    elif step.type in SUSPENDING_TYPES:
        await suspend(instance_id, step_id, f"{step.type} step")
        return _stopped(step_id, WAITING)
    else:
        outcome = await _run_step(instance_id, step, context)

    if outcome is FAILED:
        return _stopped(step_id, FAILED)
    return {"step_id": step_id, "state": COMPLETED, "output": outcome, "goto": None}


def _stopped(step_id: str, state: str, output: Any = None) -> dict[str, Any]:
    return {"step_id": step_id, "state": state, "output": output, "goto": None}


def _handed_on(step: Step) -> dict[str, Any]:
    """A step whose time ran out: the process goes on where the definition says.

    An escalation that only wrote 'escalated' on a row would leave the instance
    for somebody to notice; handing the work to the declared step is what the
    word means.
    """
    if not step.on_timeout:
        return _stopped(step.id, FAILED)
    return {"step_id": step.id, "state": ESCALATED, "output": None, "goto": step.on_timeout}


@DBOS.workflow()
async def advance_instance(instance_id: str, scope: str) -> str:
    """Walks an instance as far as it can go, and says where it stopped.

    Steps that do not depend on each other run **together**: the ones ready at
    the same time are started as their own workflows, and the join waits for all
    of them before deciding anything -- including when one has already failed,
    because stopping early would leave the others running with nobody reading
    their outcome.
    """
    with working_on(instance_id):
        return await _advance(instance_id, scope)


async def _advance(instance_id: str, scope: str) -> str:
    engine = current_engine()
    plan = await read_plan(instance_id, scope)

    # The version comes from the instance, never from the catalogue's latest: a
    # definition that changed after this instance started is another process.
    definition = engine.catalog.get(plan["process_id"], plan["process_version"])

    await note_trace(instance_id)
    await set_instance_status(instance_id, RUNNING)
    context: dict[str, Any] = dict(plan["context"])
    done: set[str] = set()
    # In the order they finished, because undoing goes the other way round.
    finished: list[str] = []
    ready = [step.id for step in definition.entry_steps()]

    while ready:
        batch = [step_id for step_id in dict.fromkeys(ready) if step_id not in done]
        ready = []
        if not batch:
            break

        handles = []
        for step_id in batch:
            with SetWorkflowID(step_workflow_id(instance_id, step_id)):
                handles.append(
                    await DBOS.start_workflow_async(
                        run_step, instance_id, scope, step_id, context
                    )
                )
        results = [await handle.get_result() for handle in handles]

        for result in results:
            if result["state"] != COMPLETED:
                continue
            done.add(result["step_id"])
            finished.append(result["step_id"])
            if isinstance(result["output"], dict):
                context.update(
                    {key: value for key, value in result["output"].items() if value is not None}
                )

        # An escalated step did not do its work, but it said where the work
        # goes: the instance carries on there instead of stopping.
        for result in results:
            if result["state"] == ESCALATED and result.get("goto"):
                ready.append(result["goto"])

        stopped = [
            result
            for result in results
            if result["state"] != COMPLETED and not result.get("goto")
        ]
        if stopped:
            return await _stop_here(instance_id, definition, stopped, finished, context)

        for result in results:
            step = definition.step(result["step_id"])
            if step is not None and result["state"] == COMPLETED:
                ready.extend(definition.next_after(step, result["output"], done))

    await set_instance_status(instance_id, COMPLETED)
    return COMPLETED


async def _stop_here(
    instance_id: str,
    definition: ProcessDefinition,
    stopped: list[dict[str, Any]],
    finished: list[str],
    context: dict[str, Any],
) -> str:
    """Says where the instance stopped, because of which steps, and undoes what it can.

    With several steps running together the state alone is not an answer: what
    somebody needs is the name of the ones that did not get through -- and, when
    the process is not going to continue, the work already done outside has to
    be taken back.
    """
    for state in (FAILED, REJECTED, ESCALATED, WAITING):
        named = [result["step_id"] for result in stopped if result["state"] == state]
        if not named:
            continue

        undone = []
        if state in (FAILED, REJECTED):
            undone = await _compensate(instance_id, definition, finished, context)

        final = COMPENSATED if state == FAILED and undone else state
        note = f"{state}: {', '.join(named)}"
        if undone:
            note += f"; undone: {', '.join(undone)}"
        await set_instance_status(instance_id, final, note)
        logger.info("Instance %s is %s -- %s.", instance_id, final, note)
        return final

    return WAITING


async def _compensate(
    instance_id: str,
    definition: ProcessDefinition,
    finished: list[str],
    context: dict[str, Any],
) -> list[str]:
    """Undoes the steps that declared how, last first.

    Reverse order is not a detail: a step undone before the one that came after
    it would be undoing something the other still relies on.
    """
    undone: list[str] = []
    for step_id in reversed(finished):
        step = definition.step(step_id)
        if step is None or not step.compensate_with:
            continue
        key = (
            f"undo:{instance_id}:{step.id}:{context.get(step.idempotency_key, '')}"
            if step.idempotency_key
            else ""
        )
        if await compensate_step(instance_id, step.id, step.compensate_with, key, context):
            undone.append(step.id)
    return undone


async def _run_agent_step(
    instance_id: str, scope: str, step: Step, context: dict[str, Any]
) -> Any:
    """Delegates to a remote agent and waits, for as long as the step allows.

    The wait is durable: the workflow is not holding a connection, and a process
    that dies here comes back waiting for the same answer. What wakes it is the
    webhook, which sends a message to this instance under the step's name.
    """
    question = str(step.input.get("question") or context.get("question") or "").strip()
    if not question:
        question = f"{step.id}: {context}"

    started = await ask_agent(instance_id, step.id, scope, step.owner or "", question)

    timeout = step.timeout_seconds or DEFAULT_AGENT_TIMEOUT_SECONDS
    answer = await DBOS.recv_async(topic=step.id, timeout_seconds=timeout)
    if answer is None:
        return await _timed_out(instance_id, step, timeout)

    for _ in range(MAX_CLARIFICATIONS):
        if answer.get("state") != NEEDS_INPUT:
            break
        # The agent stopped to ask something. A person answers it, and the answer
        # goes back into the same task: starting a new one would throw away what
        # the agent had already worked out.
        await note_question(instance_id, step.id, answer.get("text", ""))
        from_a_person = await DBOS.recv_async(topic=human_topic(step.id), timeout_seconds=timeout)
        if from_a_person is None:
            return await _timed_out(instance_id, step, timeout)

        await reply_to_agent(
            instance_id,
            step.id,
            scope,
            step.owner or "",
            answer.get("task_id") or started.get("task_id", ""),
            str(from_a_person.get("text", "")),
        )
        answer = await DBOS.recv_async(topic=step.id, timeout_seconds=timeout)
        if answer is None:
            return await _timed_out(instance_id, step, timeout)

    if answer.get("state") == NEEDS_INPUT:
        await escalate(instance_id, step.id, step.on_timeout, timeout)
        return None

    task_id = answer.get("task_id") or started.get("task_id", "")
    result = await read_result(instance_id, step.id, step.owner or "", task_id)
    text = result.get("text") or answer.get("text", "")
    output = {
        "agent": step.owner,
        "text": text,
        "task_id": task_id,
        "usage": result.get("usage") or {},
    }
    await write_step_output(instance_id, step.id, output)
    return output


async def _timed_out(instance_id: str, step: Step, timeout: float) -> None:
    """A step that ran out of time, handed on where the definition says."""
    await escalate(instance_id, step.id, step.on_timeout, timeout)
    return None


@DBOS.step()
async def read_result(
    instance_id: str, step_id: str, owner: str, task_id: str
) -> dict[str, Any]:
    """Reads the finished task: the answer, and what it cost.

    The notification is a signal -- it can arrive without the text, and it never
    carries the cost. Reading the task back is also what puts "how many rounds,
    how many tokens" into the history, which is the only way the fan-out can be
    compared with doing the same work in one agent.
    """
    engine = current_engine()
    if engine.agents is None:
        raise RuntimeError("no agents are configured: an agent step cannot run")
    if not task_id:
        return {}
    try:
        result = await engine.agents.result_of(agent=owner, task_id=task_id)
    except Exception:
        logger.warning("Task %s of %s could not be read back.", task_id[:8], owner, exc_info=True)
        return {}

    usage = result.get("usage") or {}
    if usage:
        await engine.store.record_event(
            instance_id=UUID(instance_id),
            step_id=step_id,
            kind="step_usage",
            data={"agent": owner, **usage},
        )
        logger.info(
            "Instance %s step %s cost %s rounds, %s/%s tokens.",
            instance_id,
            step_id,
            usage.get("rounds", "?"),
            usage.get("input_tokens", "?"),
            usage.get("output_tokens", "?"),
        )
    return result


@DBOS.step()
async def compensate_step(
    instance_id: str, step_id: str, tool_name: str, key: str, context: dict[str, Any]
) -> bool:
    """Undoes one step, and says whether it managed.

    A compensation that raises must not stop the ones after it: what has been
    done to the outside world is undone as far as it can be, and what could not
    be undone is written where somebody will read it.
    """
    engine = current_engine()
    if key and not await engine.store.record_effect(
        instance_id=UUID(instance_id), step_id=f"{step_id}:undo", key=key
    ):
        # Already undone, by an earlier attempt of this same instance.
        return True

    try:
        output = get_tool(tool_name)(context)
        if inspect.isawaitable(output):
            output = await output
    except Exception as error:  # noqa: BLE001 - one compensation, not the run
        await engine.store.note_step(
            instance_id=UUID(instance_id),
            step_id=step_id,
            status=COMPENSATION_FAILED,
            note=f"'{tool_name}' failed: {type(error).__name__}: {error}",
        )
        logger.error(
            "Instance %s: step %s could not be undone.", instance_id, step_id, exc_info=True
        )
        return False

    await engine.store.note_step(
        instance_id=UUID(instance_id),
        step_id=step_id,
        status=COMPENSATED,
        note=f"undone by '{tool_name}'",
    )
    logger.info("Instance %s: step %s undone by %s.", instance_id, step_id, tool_name)
    return True


@DBOS.step()
async def fail_step(instance_id: str, step_id: str, note: str) -> None:
    engine = current_engine()
    await engine.store.finish_step(
        instance_id=UUID(instance_id), step_id=step_id, status=FAILED, output=None, note=note
    )


@DBOS.step()
async def write_step_output(instance_id: str, step_id: str, output: dict[str, Any]) -> None:
    engine = current_engine()
    await engine.store.finish_step(
        instance_id=UUID(instance_id), step_id=step_id, status=COMPLETED, output=output
    )


@DBOS.step()
async def escalate(
    instance_id: str, step_id: str, goes_to: str | None, timeout: float
) -> None:
    engine = current_engine()
    note = (
        f"no answer within {timeout:.0f}s, escalated to '{goes_to}'"
        if goes_to
        else f"no answer within {timeout:.0f}s"
    )
    await engine.store.finish_step(
        instance_id=UUID(instance_id),
        step_id=step_id,
        status=ESCALATED if goes_to else FAILED,
        output=None,
        note=note,
    )
    await engine.store.set_status(
        instance_id=UUID(instance_id), status=ESCALATED if goes_to else FAILED
    )
    logger.warning("Instance %s step %s: %s.", instance_id, step_id, note)


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
