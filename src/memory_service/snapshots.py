"""Da snapshot a turni: cosa di questo snapshot non e' ancora stato scritto.

Chi chiama manda lo **stato completo** del thread a ogni run, non il delta. Il
delta lo calcola qui il servizio, che e' l'unico posto che ha sotto gli occhi
sia quello che era gia' scritto sia quello che arriva: farlo calcolare al
chiamante significherebbe riscriverlo in ogni chiamante.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .models import NewMessage, StoredMessage


def _role_of(raw: dict[str, Any]) -> str:
    role = raw.get("role")
    return role if isinstance(role, str) and role else "unknown"


def _content_of(raw: dict[str, Any]) -> str:
    content = raw.get("content")
    return content if isinstance(content, str) else ""


def new_messages(
    stored: Sequence[StoredMessage],
    incoming: Sequence[dict[str, Any]],
) -> list[NewMessage]:
    """I messaggi dello snapshot che non risultano gia' scritti.

    Due criteri, in quest'ordine:

    1. **Per identificativo**, quando c'e'. E' l'unico affidabile: regge il
       riordino, la riscrittura di un turno e gli snapshot che ripartono da
       capo.
    2. **Per posizione**, per i messaggi senza id. Non tutti i protocolli ne
       danno uno; senza questo ripiego un messaggio anonimo verrebbe riscritto
       a ogni snapshot, e la conversazione si moltiplicherebbe da sola.

    Il ripiego posizionale e' volutamente prudente: conta *tutti* i messaggi
    gia' scritti, quindi in caso di dubbio scrive di meno, non di piu'. Un
    turno mancante si nota; un turno duplicato nel contesto del modello no.
    """
    known_ids = {message.external_id for message in stored if message.external_id}
    fresh: list[NewMessage] = []

    for index, raw in enumerate(incoming):
        external_id = raw.get("id")
        if isinstance(external_id, str) and external_id:
            if external_id in known_ids:
                continue
        elif index < len(stored):
            continue

        fresh.append(
            NewMessage(
                role=_role_of(raw),
                content=_content_of(raw),
                external_id=external_id if isinstance(external_id, str) else None,
                payload=raw,
            )
        )

    return fresh
