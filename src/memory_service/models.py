"""Le forme che il servizio scambia con chi lo chiama."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class NewMessage(BaseModel):
    """Un turno da appendere alla conversazione."""

    # `role` e' una stringa libera e non un elenco chiuso: i ruoli dei
    # protocolli cambiano (developer, tool, e quelli che verranno), e un
    # servizio di memoria che rifiuta un ruolo sconosciuto fa perdere il
    # messaggio invece di conservarlo. Qui si conserva, non si sorveglia.
    role: str
    content: str = ""
    # Identificativo dato da chi scrive. Serve a non riscrivere due volte lo
    # stesso turno quando arriva dentro due snapshot successivi.
    external_id: str | None = None
    # La forma originale del messaggio, conservata verbatim. Il protocollo del
    # chiamante ha campi che `role` e `content` non reggono -- chiamate a tool,
    # allegati -- e ricostruire una conversazione con quei campi persi
    # significa restituire al modello qualcosa che non e' mai successo.
    payload: dict[str, Any] | None = None
    # Metadati liberi di chi scrive: id della run, sorgente, quello che serve.
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


class Snapshot(BaseModel):
    """Lo stato di un thread come lo scambia il protocollo AG-UI.

    I messaggi sono la conversazione; il resto e' stato del thread. Il servizio
    tratta i due pezzi in modo diverso: i messaggi diventano turni numerati e
    interrogabili, lo stato viene conservato come arriva.
    """

    messages: list[dict[str, Any]] = Field(default_factory=list)
    state: dict[str, Any] | None = None
    interrupt: list[dict[str, Any]] | None = None
    # Stato privato di continuazione del server. Non e' materiale da
    # rimandare a un client: chi lo legge e' solo il backend che lo ha scritto.
    session_state: dict[str, Any] | None = None
    # Cosa e' stato tolto ricomponendo il contesto. Viaggia con lo snapshot
    # perche' una potatura silenziosa e' indistinguibile da una perdita di
    # dati: chi legge deve poterla vedere e scriverla nei propri log.
    curation: dict[str, int] | None = None
