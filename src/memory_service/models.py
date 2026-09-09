"""Data models exchanged with service callers."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class NewMessage(BaseModel):
    """A turn to append to the conversation."""

    role: str
    content: str = ""
    external_id: str | None = None
    payload: dict[str, Any] | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class StoredMessage(NewMessage):
    """A stored turn with its position and timestamp."""

    seq: int
    ts: datetime


class Transcript(BaseModel):
    """The conversation tail, from oldest to newest."""

    thread_id: str
    messages: list[StoredMessage]
    source: Literal["hot", "durable"]


class SearchQuery(BaseModel):
    """A query to search across memories."""

    query: str = Field(min_length=1)
    limit: int = Field(default=5, ge=1, le=50)


class Snapshot(BaseModel):
    """Thread state exchanged through AG-UI. Messages become numbered, searchable turns; other thread state is preserved as supplied."""

    messages: list[dict[str, Any]] = Field(default_factory=list)
    state: dict[str, Any] | None = None
    interrupt: list[dict[str, Any]] | None = None
    session_state: dict[str, Any] | None = None
    curation: dict[str, int] | None = None


class RetentionRequest(BaseModel):
    """How long a conversation may stay. Without `days`, the configured value."""

    days: int | None = Field(default=None, ge=0)


class ReindexRequest(BaseModel):
    """Which thread to rebuild. Without one, the whole scope."""

    thread_id: str | None = None
