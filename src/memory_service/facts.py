"""Fatti duraturi: cio' che resta vero anche fuori da questa conversazione.

Il riassunto e' legato al thread: racconta *quella* conversazione. Un fatto no.
"Il referente e' Marta" vale anche in un thread nuovo, aperto domani, dove non
c'e' nessuna conversazione da riassumere -- ed e' li' che si vede la differenza
fra un agente con memoria e uno che ricomincia da capo ogni volta.

Per questo i fatti stanno **sullo scope**, non sul thread: cancellare una
conversazione non cancella cio' che si e' imparato dell'utente.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from .curation import MEMORY_ID_PREFIX

logger = logging.getLogger(__name__)

_KEY = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")


@dataclass(frozen=True)
class Fact:
    """Un fatto: una chiave stabile e un valore che puo' cambiare."""

    chiave: str
    valore: str


def parse_facts(raw: str) -> list[Fact]:
    """Legge la lista di fatti prodotta dal modello.

    Tollerante sulla forma -- un modello puo' avvolgere il JSON in un blocco
    markdown -- e severo sul contenuto: una voce senza chiave o senza valore
    viene scartata, perche' un fatto a meta' e' peggio di un fatto mancante.
    Un JSON illeggibile non solleva: i fatti sono un di piu', e non devono
    poter far fallire una conversazione.
    """
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n?|```$", "", text, flags=re.MULTILINE).strip()

    try:
        payload: Any = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("Fatti non interpretabili, ignorati: %s", text[:120])
        return []

    if isinstance(payload, dict):
        payload = payload.get("fatti") or payload.get("facts") or []
    if not isinstance(payload, list):
        return []

    facts: list[Fact] = []
    seen: set[str] = set()
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        chiave = str(entry.get("chiave") or entry.get("key") or "").strip().lower()
        valore = str(entry.get("valore") or entry.get("value") or "").strip()
        if not _KEY.match(chiave) or not valore:
            continue
        if chiave in seen:
            logger.info("Chiave '%s' ripetuta nell'estrazione: tengo il primo valore.", chiave)
            continue
        seen.add(chiave)
        facts.append(Fact(chiave=chiave, valore=valore))
    return facts


def facts_message(facts: list[dict[str, Any]]) -> dict[str, Any]:
    """I fatti come messaggio di sistema, riconoscibile al ritorno."""
    righe = "\n".join(f"- {fact['chiave']}: {fact['valore']}" for fact in facts)
    return {
        "id": f"{MEMORY_ID_PREFIX}fatti",
        "role": "system",
        "content": (
            "Cose che sai gia' di questo utente, da conversazioni precedenti:\n"
            f"{righe}\n"
            "Usale se servono. Se l'utente le contraddice, vale quello che dice adesso."
        ),
    }
