"""Il servizio: mette insieme memoria durevole e memoria a breve termine."""
from __future__ import annotations

import logging

from .models import NewMessage, Snapshot, StoredMessage, Transcript
from .snapshots import new_messages
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

    async def save_snapshot(self, scope: str, thread_id: str, snapshot: Snapshot) -> int:
        """Assorbe uno snapshot del thread. Restituisce i turni nuovi scritti.

        I messaggi non si sovrascrivono mai: la conversazione e' append-only, e
        uno snapshot che ne ripete di gia' visti aggiunge zero. E' quello che
        rende sicuro rimandare lo stato completo a ogni run.
        """
        stored = await self._durable.history(scope, thread_id)
        fresh = new_messages(stored, snapshot.messages)
        for message in fresh:
            await self.append(scope, thread_id, message)

        await self._durable.save_head(
            scope,
            thread_id,
            state=snapshot.state,
            interrupt=snapshot.interrupt,
            session_state=snapshot.session_state,
        )
        return len(fresh)

    async def read_snapshot(self, scope: str, thread_id: str) -> Snapshot | None:
        """Ricompone lo snapshot, o None se di quel thread non si sa nulla."""
        head = await self._durable.read_head(scope, thread_id)
        messages = await self._durable.history(scope, thread_id)
        if head is None and not messages:
            return None

        head = head or {}
        return Snapshot(
            # Si restituisce la forma originale quando c'e': un messaggio
            # ricostruito da ruolo e testo perderebbe le chiamate ai tool.
            messages=[
                message.payload
                if message.payload is not None
                else {"role": message.role, "content": message.content}
                for message in messages
            ],
            state=head.get("state"),
            interrupt=head.get("interrupt"),
            session_state=head.get("session_state"),
        )

    async def forget_scope(self, scope: str) -> int:
        """Dimentica tutti i thread di uno scope. Restituisce quanti erano."""
        threads = await self._durable.threads_of(scope)
        for thread_id in threads:
            await self.forget(scope, thread_id)
        return len(threads)

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
