"""Le forme che il servizio scambia con chi lo chiama."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class NewMessage(BaseModel):
    """Un turno da appendere alla conversazione."""

    role: str
    content: str = ""
    external_id: str | None = None
    payload: dict[str, Any] | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class StoredMessage(NewMessage):
    """Un turno gia' scritto: ha una posizione e un istante."""

    seq: int
    ts: datetime


class Transcript(BaseModel):
    """La coda di una conversazione, dal piu' vecchio al piu' recente."""

    thread_id: str
    messages: list[StoredMessage]
    source: Literal["hot", "durable"]


class SearchQuery(BaseModel):
    """Una domanda da cercare nei ricordi."""

    query: str = Field(min_length=1)
    limit: int = Field(default=5, ge=1, le=50)


class Snapshot(BaseModel):
    """Lo stato di un thread come lo scambia il protocollo AG-UI.

    I messaggi sono la conversazione; il resto e' stato del thread. Il servizio
    tratta i due pezzi in modo diverso: i messaggi diventano turni numerati e
    interrogabili, lo stato viene conservato come arriva.
    """

    messages: list[dict[str, Any]] = Field(default_factory=list)
    state: dict[str, Any] | None = None
    interrupt: list[dict[str, Any]] | None = None
    session_state: dict[str, Any] | None = None
    curation: dict[str, int] | None = None
