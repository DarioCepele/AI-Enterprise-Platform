"""Il servizio: mette insieme memoria durevole e memoria a breve termine."""
from __future__ import annotations

import logging
from dataclasses import replace

from .curation import ContextPolicy, curate, summary_message, window_start
from .embedder import Embedder
from .facts import facts_message, parse_facts
from .models import NewMessage, Snapshot, StoredMessage, Transcript
from .snapshots import new_messages
from .stores.hot import HotTail
from .stores.mongo import MongoTranscripts
from .stores.vectors import Memory, RedisMemories
from .summarizer import FactExtractor, Summarizer

logger = logging.getLogger(__name__)


def _searchable_text(payload: dict) -> str:
    """Il testo di un messaggio, se ne ha uno che valga la pena cercare.

    Ragionamento e risultati di tool restano fuori: il primo e' il modello che
    parla con se stesso, i secondi si riottengono richiamando il tool.
    """
    if payload.get("role") not in {"user", "assistant"}:
        return ""
    content = payload.get("content")
    return content.strip() if isinstance(content, str) else ""


def _payload_of(message: StoredMessage) -> dict:
    """La forma originale del messaggio, o una minima se non c'era."""
    if message.payload is not None:
        return message.payload
    return {"role": message.role, "content": message.content}


class ThreadMemory:
    """Scrive sempre su Mongo, legge da Redis quando puo'.

    L'ordine delle scritture non e' arbitrario: prima il durevole, poi la
    cache. Al contrario, un guasto fra le due lascerebbe in cache un messaggio
    che non esiste da nessun'altra parte -- il difetto peggiore possibile per
    un sistema di memoria, perche' sparisce da solo alla scadenza.
    """

    def __init__(
        self,
        durable: MongoTranscripts,
        hot: HotTail,
        policy: ContextPolicy | None = None,
        summarizer: Summarizer | None = None,
        extractor: FactExtractor | None = None,
        max_facts: int = 30,
        embedder: Embedder | None = None,
        memories: RedisMemories | None = None,
    ) -> None:
        self._durable = durable
        self._hot = hot
        self._policy = policy or ContextPolicy()
        self._summarizer = summarizer
        self._extractor = extractor
        self._max_facts = max_facts
        self._embedder = embedder
        self._memories = memories
        self._compacting: set[tuple[str, str]] = set()

    async def append(self, scope: str, thread_id: str, message: NewMessage) -> StoredMessage:
        stored = await self._durable.append(scope, thread_id, message)
        try:
            await self._hot.append(scope, thread_id, stored)
        except Exception:
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

    async def compact_if_needed(self, scope: str, thread_id: str) -> None:
        """Riassume i turni che stanno per uscire dalla finestra.

        **Va invocata dopo aver risposto, mai dentro una richiesta.** Un
        riassunto costa un'inferenza -- decine di secondi nel caso peggiore --
        e tenerla dentro la PUT dello snapshot manda in timeout il client:
        misurato, non temuto. Il riassunto serve al turno successivo, non a
        questo, quindi puo' benissimo arrivare dopo.
        """
        nulla_da_fare = (
            self._summarizer is None and self._extractor is None and self._memories is None
        )
        if nulla_da_fare or not self._policy.max_messages:
            return

        key = (scope, thread_id)
        if key in self._compacting:
            logger.info("Compattazione del thread %s gia' in corso, salto.", thread_id)
            return
        self._compacting.add(key)
        try:
            await self._compact(scope, thread_id)
            await self._learn_facts(scope, thread_id)
            await self._index_memories(scope, thread_id)
        finally:
            self._compacting.discard(key)

    async def _index_memories(self, scope: str, thread_id: str) -> None:
        """Rende cercabili per significato i turni usciti dalla finestra.

        Si indicizza **solo** quello che esce: cio' che e' ancora nella
        finestra il modello ce l'ha gia' davanti, e ritrovarglielo sarebbe
        ripetizione. La ricerca serve a raggiungere quello che non c'e' piu'.
        """
        if self._embedder is None or self._memories is None or not self._policy.max_messages:
            return

        history = await self._durable.history(scope, thread_id)
        payloads = [_payload_of(message) for message in history]
        cut = window_start(payloads, self._policy.max_messages)
        if cut == 0:
            return

        indicizzati = await self._durable.indexed_upto(scope, thread_id)
        entries = [
            (thread_id, message.seq, _searchable_text(payload))
            for message, payload in zip(history[:cut], payloads[:cut], strict=True)
            if message.seq > indicizzati and _searchable_text(payload)
        ]
        if not entries:
            return

        try:
            vectors = await self._embedder.embed([testo for _, _, testo in entries])
            await self._memories.index(scope, entries, vectors)
        except Exception:
            logger.error(
                "Ricordi NON indicizzati per il thread %s: la ricerca semantica "
                "non li trovera'.",
                thread_id,
                exc_info=True,
            )
            return

        await self._durable.set_indexed_upto(scope, thread_id, entries[-1][1])
        logger.info("Dal thread %s: %d ricordi indicizzati.", thread_id, len(entries))

    async def search_memories(self, scope: str, query: str, limit: int) -> list[Memory]:
        """Cerca nei ricordi dello scope per significato, non per parole."""
        if self._embedder is None or self._memories is None:
            logger.warning("Ricerca semantica non configurata: nessun ricordo restituito.")
            return []

        vectors = await self._embedder.embed([query])
        if not vectors:
            return []
        return await self._memories.search(scope, vectors[0], limit)

    async def _learn_facts(self, scope: str, thread_id: str) -> None:
        """Distilla i fatti duraturi dai turni che stanno uscendo.

        Stesso momento della compattazione, e non e' un caso: quello e' il
        punto in cui dei messaggi stanno per smettere di essere leggibili dal
        modello. Se qualcosa li' dentro vale anche domani, va estratto adesso o
        mai piu'.
        """
        if self._extractor is None or not self._policy.max_messages:
            return

        history = await self._durable.history(scope, thread_id)
        payloads = [_payload_of(message) for message in history]
        cut = window_start(payloads, self._policy.max_messages)
        if cut == 0:
            return

        try:
            raw = await self._extractor.extract_facts(payloads[:cut])
        except Exception:
            logger.error(
                "Fatti NON estratti dal thread %s: quei turni escono dal contesto "
                "senza lasciare nulla di duraturo.",
                thread_id,
                exc_info=True,
            )
            return

        facts = parse_facts(raw)
        if not facts:
            return

        changed = await self._durable.upsert_facts(
            scope, [(fact.chiave, fact.valore) for fact in facts], thread_id=thread_id
        )
        logger.info(
            "Dal thread %s: %d fatti duraturi, %d nuovi o cambiati.",
            thread_id,
            len(facts),
            changed,
        )

    async def _compact(self, scope: str, thread_id: str) -> None:
        """Il lavoro vero: taglio, riassunto, scrittura."""
        if self._summarizer is None:
            return
        history = await self._durable.history(scope, thread_id)
        payloads = [_payload_of(message) for message in history]
        cut = window_start(payloads, self._policy.max_messages)
        if cut == 0:
            return

        covers_to_seq = history[cut - 1].seq
        existing = await self._durable.latest_summary(scope, thread_id)
        if existing and int(existing.get("covers_to_seq", 0)) >= covers_to_seq:
            return

        try:
            text = await self._summarizer.summarize(payloads[:cut])
        except Exception:
            logger.error(
                "Riassunto NON prodotto per il thread %s: i turni fuori finestra "
                "restano fuori dal contesto.",
                thread_id,
                exc_info=True,
            )
            return

        if not text:
            return

        await self._durable.save_summary(
            scope,
            thread_id,
            text=text,
            covers_to_seq=covers_to_seq,
            message_count=cut,
            model=getattr(self._summarizer, "_model", "?"),
        )
        logger.info(
            "Thread %s compattato: %d messaggi fino a seq %d in %d caratteri di riassunto.",
            thread_id,
            cut,
            covers_to_seq,
            len(text),
        )

    async def read_snapshot(
        self,
        scope: str,
        thread_id: str,
        *,
        raw: bool = False,
    ) -> Snapshot | None:
        """Ricompone lo snapshot, o None se di quel thread non si sa nulla.

        Di default il contesto e' **potato**: fuori il ragionamento dei turni
        passati, svuotati i risultati di tool piu' vecchi. Con `raw=True` torna
        il transcript integrale, che e' quello che serve per riassumere e per
        capire cosa e' successo davvero.
        """
        head = await self._durable.read_head(scope, thread_id)
        messages = await self._durable.history(scope, thread_id)
        if head is None and not messages:
            if raw:
                return None
            facts = await self._durable.facts_of(scope, self._max_facts)
            if not facts:
                return None
            return Snapshot(
                messages=[facts_message(facts)],
                curation={
                    "conservati": 1,
                    "ragionamenti_tolti": 0,
                    "risultati_svuotati": 0,
                    "messaggi_scartati": 0,
                    "riassunti": 0,
                    "fatti": len(facts),
                },
            )

        head = head or {}
        payloads = [_payload_of(message) for message in messages]

        report = None
        if not raw:
            stored_summary = await self._durable.latest_summary(scope, thread_id)
            summary = (
                summary_message(stored_summary["text"], stored_summary["covers_to_seq"])
                if stored_summary
                else None
            )
            policy = self._policy
            if summary is None and self._summarizer is not None:
                logger.info(
                    "Finestra sospesa sul thread %s: riassunto non ancora pronto.", thread_id
                )
                policy = replace(policy, max_messages=None)
            payloads, curation = curate(payloads, policy, summary)
            report = curation.as_dict()

            facts = await self._durable.facts_of(scope, self._max_facts)
            report["fatti"] = len(facts)
            if facts:
                payloads = [facts_message(facts), *payloads]
            logger.info(
                "Contesto del thread %s ricomposto: %d messaggi su %d "
                "(%d ragionamenti tolti, %d risultati svuotati, %d scartati).",
                thread_id,
                curation.conservati,
                len(messages),
                curation.ragionamenti_tolti,
                curation.risultati_svuotati,
                curation.messaggi_scartati,
            )

        return Snapshot(
            messages=payloads,
            state=head.get("state"),
            interrupt=head.get("interrupt"),
            session_state=head.get("session_state"),
            curation=report,
        )

    async def forget_scope(self, scope: str) -> int:
        """Dimentica tutti i thread di uno scope. Restituisce quanti erano."""
        threads = await self._durable.threads_of(scope)
        for thread_id in threads:
            await self.forget(scope, thread_id)
        await self._durable.forget_facts(scope)
        if self._memories:
            await self._memories.forget_scope(scope)
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
        """Cancella davvero: durevole prima, cache e indice dopo."""
        seqs = await self._durable.seqs_of(scope, thread_id) if self._memories else []
        removed = await self._durable.forget(scope, thread_id)
        if self._memories and seqs:
            try:
                await self._memories.forget_thread(scope, thread_id, seqs)
            except Exception:
                logger.error(
                    "Ricordi NON rimossi dall'indice per il thread %s: restano cercabili.",
                    thread_id,
                    exc_info=True,
                )
                raise
        try:
            await self._hot.forget(scope, thread_id)
        except Exception:
            logger.error("Coda calda NON cancellata per il thread %s.", thread_id, exc_info=True)
            raise
        return removed
