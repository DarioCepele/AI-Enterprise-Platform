"""Notifiche push dei sottoagenti: chi le riceve, e come si fida."""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

HEADER = "X-A2A-Notification-Token"


def _segreto() -> bytes:
    return os.getenv("DEMO_PUSH_SECRET", "laboratorio-senza-segreto").encode()


def token_per(thread_id: str) -> str:
    """Il token che il sottoagente rimandera' indietro con la notifica.

    Firma il **thread**, non il task: il webhook si registra prima che il task
    esista, quindi un token sul task id non si potrebbe calcolare in anticipo.

    Firmato e non memorizzato: verificarlo non richiede stato di processo, cosi'
    due repliche accettano gli stessi token senza ricordare nulla. Il segreto
    vive nell'ambiente.
    """
    return hmac.new(_segreto(), thread_id.encode(), hashlib.sha256).hexdigest()


def token_valido(thread_id: str, ricevuto: str | None) -> bool:
    if not ricevuto:
        return False
    return hmac.compare_digest(token_per(thread_id), ricevuto)


def url_webhook(base: str, scope: str, thread_id: str) -> str:
    """La correlazione sta nell'URL: chi riceve sa gia' a quale thread appartiene.

    L'alternativa -- una tabella da task a thread -- sarebbe stato di processo,
    o una query in piu' su ogni notifica.
    """
    return f"{base.rstrip('/')}/a2a/push/{scope}/{thread_id}"


TERMINALI = {
    "TASK_STATE_COMPLETED",
    "TASK_STATE_FAILED",
    "TASK_STATE_CANCELED",
    "TASK_STATE_REJECTED",
}


def riassunto(notifica: dict[str, Any]) -> tuple[str, str, str]:
    """Estrae (task_id, stato, testo) da una notifica, senza fidarsi della forma.

    Il sottoagente notifica **un evento alla volta**, non solo la fine, e li
    manda come StreamResponse in camelCase: a volte un task intero, a volte un
    aggiornamento di stato, a volte un artefatto. Qui si accetta tutto e si
    lascia al chiamante decidere cosa ignorare.
    """
    task = notifica.get("task") or {}
    stato_aggiornato = notifica.get("statusUpdate") or notifica.get("status_update") or {}
    artefatto_aggiornato = notifica.get("artifactUpdate") or notifica.get("artifact_update") or {}

    task_id = str(
        task.get("id")
        or stato_aggiornato.get("taskId")
        or stato_aggiornato.get("task_id")
        or artefatto_aggiornato.get("taskId")
        or artefatto_aggiornato.get("task_id")
        or ""
    )
    stato = str(
        (task.get("status") or {}).get("state")
        or (stato_aggiornato.get("status") or {}).get("state")
        or ""
    )

    artefatti = list(task.get("artifacts") or [])
    if artefatto_aggiornato.get("artifact"):
        artefatti.append(artefatto_aggiornato["artifact"])
    parti = [
        parte["text"]
        for artefatto in artefatti
        for parte in artefatto.get("parts") or []
        if isinstance(parte.get("text"), str)
    ]
    return task_id, stato, "".join(parti).strip()


def terminale(stato: str) -> bool:
    """Se questa notifica chiude il task o e' solo un avanzamento."""
    return stato in TERMINALI
