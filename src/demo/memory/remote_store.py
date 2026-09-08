"""Snapshot store che vive nel servizio di memoria, non in questo processo.

Implementa il protocollo `AGUIThreadSnapshotStore` dell'adattatore AG-UI
parlando HTTP con `demo-memory-service`. L'agente non conosce Mongo ne' Redis:
conosce un servizio, e quel servizio decide come e dove ricordare.

**Politica di guasto, dichiarata perche' non e' ovvia.** Un servizio di memoria
irraggiungibile non deve far fallire la conversazione: in lettura si degrada a
"thread sconosciuto" e l'agente riparte senza storia, in scrittura si registra
l'errore. In entrambi i casi la riga finisce su `demo.*`, quindi nel tab LOG:
un'amnesia silenziosa e' il difetto peggiore che possa avere questo pezzo.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx
from agent_framework.ag_ui import AGUIThreadSnapshot

logger = logging.getLogger(__name__)

SCOPE_HEADER = "X-Memory-Scope"

class MemoryServiceSnapshotStore:
    """La memoria dei thread, tenuta dal servizio di memoria."""

    def __init__(
        self,
        base_url: str,
        client: httpx.AsyncClient | None = None,
        timeout: float = 5.0,
    ) -> None:

        self._client = client or httpx.AsyncClient(base_url=base_url, timeout=timeout)

    @staticmethod
    def _headers(scope: str) -> dict[str, str]:

        return {SCOPE_HEADER: scope}

    async def save(
        self,
        *,
        scope: str,
        thread_id: str,
        snapshot: AGUIThreadSnapshot,
    ) -> None:
        body: dict[str, Any] = {
            "messages": snapshot.messages,
            "state": snapshot.state,
            "interrupt": snapshot.interrupt,
            "session_state": snapshot.session_state,
        }
        try:
            response = await self._client.put(
                f"/threads/{thread_id}/snapshot", json=body, headers=self._headers(scope)
            )
            response.raise_for_status()
        except Exception:

            logger.error("Memoria NON salvata per il thread %s.", thread_id, exc_info=True)
            return
        logger.info(
            "Memoria del thread %s aggiornata: %s turni nuovi.",
            thread_id,
            response.json().get("turni_nuovi", "?"),
        )

    async def get(self, *, scope: str, thread_id: str) -> AGUIThreadSnapshot | None:
        try:
            response = await self._client.get(
                f"/threads/{thread_id}/snapshot", headers=self._headers(scope)
            )
            if response.status_code == 404:
                return None
            response.raise_for_status()
            payload = response.json()
        except Exception:

            logger.error(
                "Memoria del thread %s non leggibile: si riparte senza storia.",
                thread_id,
                exc_info=True,
            )
            return None

        messages = payload.get("messages") or []
        curation = payload.get("curation")
        if curation:

            logger.info(
                "Contesto dalla memoria: %d messaggi (%d ragionamenti tolti, "
                "%d risultati svuotati, %d scartati).",
                len(messages),
                curation.get("ragionamenti_tolti", 0),
                curation.get("risultati_svuotati", 0),
                curation.get("messaggi_scartati", 0),
            )
        else:
            logger.info("Contesto dalla memoria: %d messaggi, senza potatura.", len(messages))

        return AGUIThreadSnapshot(
            messages=messages,
            state=payload.get("state"),
            interrupt=payload.get("interrupt"),
            session_state=payload.get("session_state"),
        )

    async def delete(self, *, scope: str, thread_id: str) -> bool:
        response = await self._client.delete(
            f"/threads/{thread_id}", headers=self._headers(scope)
        )
        response.raise_for_status()
        return bool(response.json().get("buckets_rimossi", 0))

    async def clear(self, *, scope: str | None = None) -> None:
        """Svuota uno scope intero.

        Senza scope non si fa: cancellare "tutto" attraverso un confine di
        autorizzazione e' esattamente l'operazione che quel confine esiste per
        impedire.
        """
        if scope is None:
            raise ValueError("clear() senza scope non e' supportato dal servizio di memoria")
        response = await self._client.delete("/scope", headers=self._headers(scope))
        response.raise_for_status()

    async def aclose(self) -> None:
        await self._client.aclose()
