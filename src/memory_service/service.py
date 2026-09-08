"""Il servizio: mette insieme memoria durevole e memoria a breve termine."""
from __future__ import annotations

import logging

from .models import NewMessage, StoredMessage, Transcript
from .stores.hot import HotTail
from .stores.mongo import MongoTranscripts

logger = logging.getLogger(__name__)


class ThreadMemory:
    """Scrive sempre su Mongo, legge da Redis quando puo'.

    L'ordine delle scritture non e' arbitrario: prima il durevole, poi la
    cache. Al contrario, un guasto fra le due lascerebbe in cache un messaggio
    che non esiste da nessun'altra parte -- il difetto peggiore possibile per
    un sistema di memoria, perche' sparisce da solo alla scadenza.
    """

    def __init__(self, durable: MongoTranscripts, hot: HotTail) -> None:
        self._durable = durable
        self._hot = hot

    async def append(self, scope: str, thread_id: str, message: NewMessage) -> StoredMessage:
        stored = await self._durable.append(scope, thread_id, message)
        try:
            await self._hot.append(scope, thread_id, stored)
        except Exception:
            # Redis giu' non e' una perdita di dati: il messaggio e' gia' al
            # sicuro. Si perde velocita', non memoria.
            logger.warning("Coda calda non aggiornata per il thread %s.", thread_id, exc_info=True)
        return stored

    async def tail(self, scope: str, thread_id: str, limit: int) -> Transcript:
        try:
            cached = await self._hot.tail(scope, thread_id, limit)
        except Exception:
            logger.warning("Coda calda non leggibile, si passa a Mongo.", exc_info=True)
            cached = None

        if cached is not None:
            return Transcript(thread_id=thread_id, messages=cached, source="hot")

        messages = await self._durable.tail(scope, thread_id, limit)
        return Transcript(thread_id=thread_id, messages=messages, source="durable")

    async def check(self) -> dict[str, str]:
        """Stato delle due memorie, con la differenza che conta.

        Mongo giu' e' un guasto: senza, i messaggi si perdono. Redis giu' e' un
        degrado: si risponde comunque, leggendo dal durevole.
        """
        await self._durable.ping()
        try:
            await self._hot.ping()
        except Exception:
            logger.warning("Redis non raggiungibile: servizio degradato.", exc_info=True)
            return {"status": "degraded", "durable": "ok", "hot": "down"}
        return {"status": "ok", "durable": "ok", "hot": "ok"}

    async def forget(self, scope: str, thread_id: str) -> int:
        """Cancella davvero: durevole prima, cache dopo."""
        removed = await self._durable.forget(scope, thread_id)
        try:
            await self._hot.forget(scope, thread_id)
        except Exception:
            # Una cancellazione che lascia la cache viva e' un dato cancellato
            # che continua a rispondere fino alla scadenza: va detto forte.
            logger.error("Coda calda NON cancellata per il thread %s.", thread_id, exc_info=True)
            raise
        return removed
