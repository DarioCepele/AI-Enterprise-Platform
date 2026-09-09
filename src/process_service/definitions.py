"""A process is data: steps, dependencies, branches, and who runs what.

The definition is validated once, at load time, and a wrong one names the step
that is wrong. A process that fails at the first instance instead of at startup
fails in front of whoever is using it.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

STEP_TYPES = ("tool", "agent", "approval", "decision", "open_goal")


class DefinitionError(ValueError):
    """A definition that cannot run, with the step that makes it so."""


class Branch(BaseModel):
    """Where a decision goes, and on which condition."""

    model_config = ConfigDict(extra="forbid")

    when: str
    goto: str


class Step(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    type: Literal["tool", "agent", "approval", "decision", "open_goal"]
    depends_on: list[str] = []

    # Only some of these make sense per type; which ones is checked below, where
    # the error can name the step instead of a field path nobody can place.
    tool: str | None = None
    owner: str | None = None
    approvers: list[str] = []
    branches: list[Branch] = []
    participants: list[str] = []
    timeout_seconds: float | None = None
    on_timeout: str | None = None
    idempotency_key: str | None = None
    compensate_with: str | None = None
    input: dict[str, Any] = {}


class ProcessDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    version: int
    name: str = ""
    description: str = ""
    steps: list[Step]

    def key(self) -> tuple[str, int]:
        """What a running instance refers to: an instance keeps its version."""
        return (self.id, self.version)

    def step(self, step_id: str) -> Step | None:
        return next((step for step in self.steps if step.id == step_id), None)

    def branch_targets(self) -> set[str]:
        return {branch.goto for step in self.steps for branch in step.branches}

    def entry_steps(self) -> list[Step]:
        """Where an instance starts: no dependencies, and nobody branches to it.

        A step reachable only through a decision must not start on its own --
        otherwise both sides of a branch would run, which is the opposite of
        what a branch is for.
        """
        targets = self.branch_targets()
        return [step for step in self.steps if not step.depends_on and step.id not in targets]


REQUIRED_BY_TYPE = {
    "tool": ("tool", "a tool step needs 'tool'"),
    "agent": ("owner", "an agent step needs 'owner': who runs it is not a detail"),
    "decision": ("branches", "a decision step needs 'branches'"),
    "open_goal": ("participants", "an open goal needs 'participants'"),
}


def parse_definition(payload: dict[str, Any]) -> ProcessDefinition:
    try:
        definition = ProcessDefinition.model_validate(payload)
    except ValidationError as error:
        raise DefinitionError(_readable(error)) from error

    _check_unique_ids(definition)
    _check_required_fields(definition)
    _check_references(definition)
    _check_no_cycles(definition)
    return definition


def _readable(error: ValidationError) -> str:
    """Pydantic's message, with the step it belongs to in front of it."""
    parts = []
    for problem in error.errors():
        location = ".".join(str(item) for item in problem["loc"])
        received = problem.get("input")
        parts.append(f"{location}: {problem['msg']} (got {received!r})")
    return "; ".join(parts)


def _check_unique_ids(definition: ProcessDefinition) -> None:
    seen: set[str] = set()
    for step in definition.steps:
        if step.id in seen:
            raise DefinitionError(f"step '{step.id}' is declared twice")
        seen.add(step.id)


def _check_required_fields(definition: ProcessDefinition) -> None:
    for step in definition.steps:
        if step.type not in STEP_TYPES:
            raise DefinitionError(
                f"step '{step.id}': unknown type '{step.type}'. Known: {', '.join(STEP_TYPES)}"
            )
        required = REQUIRED_BY_TYPE.get(step.type)
        if required and not getattr(step, required[0]):
            raise DefinitionError(f"step '{step.id}': {required[1]}")


def _check_references(definition: ProcessDefinition) -> None:
    known = {step.id for step in definition.steps}
    for step in definition.steps:
        for dependency in step.depends_on:
            if dependency not in known:
                raise DefinitionError(
                    f"step '{step.id}' depends on '{dependency}', which does not exist"
                )
        if step.on_timeout and step.on_timeout not in known:
            raise DefinitionError(
                f"step '{step.id}' escalates to '{step.on_timeout}', which does not exist"
            )
        for branch in step.branches:
            if branch.goto not in known:
                raise DefinitionError(
                    f"step '{step.id}' branches to '{branch.goto}', which does not exist"
                )


def _check_no_cycles(definition: ProcessDefinition) -> None:
    """A cycle would be an instance that never ends, found now instead of then."""
    pending = {step.id: set(step.depends_on) for step in definition.steps}
    while pending:
        ready = [step_id for step_id, waiting in pending.items() if not waiting]
        if not ready:
            raise DefinitionError(
                "these steps depend on each other in a cycle: " + ", ".join(sorted(pending))
            )
        for step_id in ready:
            del pending[step_id]
        for waiting in pending.values():
            waiting.difference_update(ready)
