"""Walking the history of an instance again, without doing anything.

"Why did it go that way?" is asked months later, when whoever asks has only the
rows. Replaying answers it from the recorded events alone: no tool is called, no
agent is asked, no model is run -- what the outside world said is read back as
data. If the recorded path and the rules of the definition disagree, that is
worth an exception rather than a shrug: it means the history no longer explains
the instance.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .definitions import ProcessDefinition
from .expressions import ConditionError, evaluate
from .models import Event

logger = logging.getLogger(__name__)

COMPLETED = "completed"
ESCALATED = "escalated"
COMPENSATED = "compensated"
COMPENSATION_FAILED = "compensation_failed"

# States a step can end in and still let the process carry on.
WENT_ON = (COMPLETED, ESCALATED, COMPENSATED, COMPENSATION_FAILED)


class ReplayDiverged(Exception):
    """The history and the definition no longer tell the same story."""


@dataclass
class Replayed:
    """What the history says happened, re-derived from the events."""

    path: list[str] = field(default_factory=list)
    decisions: dict[str, str] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)
    status: str | None = None
    undone: list[str] = field(default_factory=list)


def replay(definition: ProcessDefinition, events: list[Event]) -> Replayed:
    """Re-derives the path an instance took, from its events and its definition.

    The parts that could go either way -- what a tool returned, what an agent
    answered -- are read from the history. The parts that are rules -- which
    branch a condition takes, which step comes next -- are worked out again, and
    checked against what was recorded. Two replays of the same history give the
    same answer, because nothing here asks anybody anything.
    """
    history = _read(events)
    walked = Replayed(
        context=dict(history.input), status=history.status, undone=history.undone
    )

    done: set[str] = set()
    ready = [step.id for step in definition.entry_steps()]

    while ready:
        batch = [step_id for step_id in dict.fromkeys(ready) if step_id not in done]
        ready = []
        for step_id in batch:
            step = definition.step(step_id)
            if step is None:
                raise ReplayDiverged(
                    f"the history mentions '{step_id}', which this definition "
                    "does not have"
                )

            state = history.states.get(step_id)
            if state not in WENT_ON:
                # The instance stopped here, or never got this far: the path ends
                # where the history ends, which is the honest answer.
                continue

            output = history.outputs.get(step_id)
            if step.type == "decision":
                output = _decision_of(step_id, definition, output, walked)
                walked.decisions[step_id] = output

            walked.path.append(step_id)
            done.add(step_id)
            if isinstance(output, dict):
                walked.context.update(
                    {key: value for key, value in output.items() if value is not None}
                )
            walked.outputs[step_id] = output

            if state == ESCALATED and step.on_timeout:
                ready.append(step.on_timeout)
                continue
            ready.extend(definition.next_after(step, output, done))

    return walked


def _decision_of(
    step_id: str, definition: ProcessDefinition, recorded: Any, walked: Replayed
) -> str:
    """Works the branch out again, and refuses to disagree with the history.

    This is the whole point of replaying a decision instead of trusting it: if
    the rule and the record no longer agree, something changed underneath -- the
    definition, or the data the decision read -- and the instance can no longer
    be explained by what is written down.
    """
    step = definition.step(step_id)
    if step is None:
        raise ReplayDiverged(
            f"the history mentions '{step_id}', which this definition does not have"
        )
    for branch in step.branches:
        try:
            holds = evaluate(branch.when, walked.context)
        except ConditionError as error:
            raise ReplayDiverged(f"step '{step_id}': {error}") from error
        if not holds:
            continue
        recorded_goto = recorded.get("goto") if isinstance(recorded, dict) else None
        if isinstance(recorded, dict) and recorded_goto not in (None, branch.goto):
            raise ReplayDiverged(
                f"step '{step_id}' took '{recorded['goto']}' when it ran, and takes "
                f"'{branch.goto}' now: the history no longer explains the instance"
            )
        return branch.goto

    raise ReplayDiverged(f"step '{step_id}': no branch holds any more")


@dataclass
class _History:
    input: dict[str, Any] = field(default_factory=dict)
    states: dict[str, str] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    status: str | None = None
    undone: list[str] = field(default_factory=list)


def _read(events: list[Event]) -> _History:
    """Folds the events into what each step ended up being, in order."""
    history = _History()
    for event in events:
        if event.kind == "instance_created":
            history.input = dict(event.data.get("input") or {})
        elif event.kind == "instance_status":
            history.status = event.data.get("status")
        elif event.kind == "step_finished" and event.step_id:
            history.states[event.step_id] = str(event.data.get("status"))
            if event.data.get("output") is not None:
                history.outputs[event.step_id] = event.data["output"]
        elif event.kind == "step_noted" and event.step_id:
            status = str(event.data.get("status"))
            history.states[event.step_id] = status
            if status == COMPENSATED:
                history.undone.append(event.step_id)
    return history
