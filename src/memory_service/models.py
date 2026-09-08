"""Le forme che il servizio scambia con chi lo chiama."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

Role = Literal["user", "assistant", "system", "tool"]


class NewMessage(BaseModel):
    """Un turno da appendere alla conversazione."""

    role: Role
    content: str
    # Metadati liberi di chi scrive: id del tool, id della run, quello che serve.
    # Il servizio non li interpreta, li conserva.
    meta: dict[str, Any] = Field(default_factory=dict)


class StoredMessage(NewMessage):
    """Un turno gia' scritto: ha una posizione e un istante."""

    seq: int
    ts: datetime


class Transcript(BaseModel):
    """La coda di una conversazione, dal piu' vecchio al piu' recente."""

    thread_id: str
    messages: list[StoredMessage]
    # Da dove arriva la risposta. Non e' decorazione: e' l'unico modo di
    # accorgersi che la cache non viene mai usata, o che copre sempre tutto.
    source: Literal["hot", "durable"]
