"""What callers see: instances and the state of their steps."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class StepState(BaseModel):
    """One step of one instance: where it is, and who has it."""

    step_id: str
    status: str
    owner: str | None = None
    task_id: str | None = None
    question: str | None = None
    output: dict[str, Any] | None = None
    note: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None


class Event(BaseModel):
    """One thing that happened to an instance, in the order it happened."""

    id: int
    instance_id: UUID
    step_id: str | None = None
    kind: str
    data: dict[str, Any] = Field(default_factory=dict)
    at: datetime | None = None


class Instance(BaseModel):
    """A running process, pinned to the version it started with."""

    id: UUID
    scope: str
    process_id: str
    process_version: int
    status: str
    note: str | None = None
    input: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None
    steps: list[StepState] = Field(default_factory=list)


class AnswerRequest(BaseModel):
    """What a person answers to a step that is waiting for a clarification."""

    text: str


class ApprovalRequest(BaseModel):
    """A decision on a step that is waiting for one, and who made it."""

    by: str
    decision: Literal["approved", "rejected"] = "approved"
    note: str | None = None


class StartRequest(BaseModel):
    """What starts an instance: the data the process works on."""

    input: dict[str, Any] = Field(default_factory=dict)
    version: int | None = None
