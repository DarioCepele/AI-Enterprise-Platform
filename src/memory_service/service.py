"""Combine durable storage and short-term memory."""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from dataclasses import replace
from typing import Any

from .curation import ContextPolicy, curate, summary_message, window_start
from .embedder import Embedder
from .facts import facts_message, parse_facts
from .models import NewMessage, Snapshot, StoredMessage, Transcript
from .snapshots import new_messages
from .stores.hot import HotTail
from .stores.locks import InProcessLock
from .stores.postgres import PostgresTranscripts
from .stores.vectors import Memory, RedisMemories
from .summarizer import FactExtractor, Summarizer

logger = logging.getLogger(__name__)


def _searchable_text(payload: dict) -> str:
    """Return searchable message text. Exclude reasoning and tool results: reasoning is internal, and tools can be called again."""
    if payload.get("role") not in {"user", "assistant"}:
        return ""
    content = payload.get("content")
    return content.strip() if isinstance(content, str) else ""


def _payload_of(message: StoredMessage) -> dict:
    """Return the original message payload, or a minimal representation."""
    if message.payload is not None:
        return message.payload
    return {"role": message.role, "content": message.content}


class ThreadMemory:
    """Always write to Postgres first, then update Redis. Reading from Redis is optional; no cache entry may be the only copy of a message."""

    def __init__(
        self,
        durable: PostgresTranscripts,
        hot: HotTail,
        policy: ContextPolicy | None = None,
        summarizer: Summarizer | None = None,
        extractor: FactExtractor | None = None,
        max_facts: int = 30,
        embedder: Embedder | None = None,
        memories: RedisMemories | None = None,
        lock: Any | None = None,
    ) -> None:
        self._durable = durable
        self._hot = hot
        self._policy = policy or ContextPolicy()
        self._summarizer = summarizer
        self._extractor = extractor
        self._max_facts = max_facts
        self._embedder = embedder
        self._memories = memories
        self._lock = lock or InProcessLock()

    async def append(self, scope: str, thread_id: str, message: NewMessage) -> StoredMessage:
        stored = await self._durable.append(scope, thread_id, message)
        try:
            await self._hot.append(scope, thread_id, stored)
        except Exception:
            logger.warning("Hot tail not updated for thread %s.", thread_id, exc_info=True)
        return stored

    async def tail(self, scope: str, thread_id: str, limit: int) -> Transcript:
        try:
            cached = await self._hot.tail(scope, thread_id, limit)
        except Exception:
            logger.warning("Hot tail unreadable, falling back to Postgres.", exc_info=True)
            cached = None

        if cached is not None:
            return Transcript(thread_id=thread_id, messages=cached, source="hot")

        messages = await self._durable.tail(scope, thread_id, limit)
        return Transcript(thread_id=thread_id, messages=messages, source="durable")

    async def save_snapshot(self, scope: str, thread_id: str, snapshot: Snapshot) -> int:
        """Store a snapshot and return the number of new turns. Messages are append-only, so repeated full snapshots add no duplicates."""
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
        """Summarize turns leaving the window after the response, never inside a request. Model inference can take tens of seconds and time out snapshot PUT requests; the summary is needed on the next turn."""
        nothing_to_do = (
            self._summarizer is None and self._extractor is None and self._memories is None
        )
        if nothing_to_do or not self._policy.max_messages:
            return

        async with self._lock.hold(f"compaction:{scope}:{thread_id}") as taken:
            if not taken:
                logger.info("Compaction of thread %s already running, skipping.", thread_id)
                return
            await self._compact(scope, thread_id)
            await self._learn_facts(scope, thread_id)
            await self._index_memories(scope, thread_id)

    async def _index_memories(self, scope: str, thread_id: str) -> None:
        """Index only turns leaving the context window. Messages still visible to the model do not need semantic retrieval."""
        if self._embedder is None or self._memories is None or not self._policy.max_messages:
            return

        history = await self._durable.history(scope, thread_id)
        payloads = [_payload_of(message) for message in history]
        cut = window_start(payloads, self._policy.max_messages)
        if cut == 0:
            return

        indexed = await self._durable.indexed_upto(scope, thread_id)
        entries = [
            (thread_id, message.seq, _searchable_text(payload))
            for message, payload in zip(history[:cut], payloads[:cut], strict=True)
            if message.seq > indexed and _searchable_text(payload)
        ]
        if not entries:
            return

        try:
            vectors = await self._embedder.embed([text for _, _, text in entries])
            await self._memories.index(scope, entries, vectors)
        except Exception:
            logger.error(
                "Memories NOT indexed for thread %s: semantic search will not "
                "find them.",
                thread_id,
                exc_info=True,
            )
            return

        await self._durable.set_indexed_upto(scope, thread_id, entries[-1][1])
        logger.info("From thread %s: %d memories indexed.", thread_id, len(entries))

    async def search_memories(self, scope: str, query: str, limit: int) -> list[Memory]:
        """Search memories within a scope by semantic similarity."""
        if self._embedder is None or self._memories is None:
            logger.warning("Semantic search not configured: no memory returned.")
            return []

        vectors = await self._embedder.embed([query])
        if not vectors:
            return []
        return await self._memories.search(scope, vectors[0], limit)

    async def _learn_facts(self, scope: str, thread_id: str) -> None:
        """Extract durable facts as turns leave the context window, alongside compaction, before the model loses access to them."""
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
                "Facts NOT extracted from thread %s: those turns leave the context "
                "without leaving anything durable.",
                thread_id,
                exc_info=True,
            )
            return

        facts = parse_facts(raw)
        if not facts:
            return

        changed = await self._durable.upsert_facts(
            scope, [(fact.key, fact.value) for fact in facts], thread_id=thread_id
        )
        logger.info(
            "From thread %s: %d durable facts, %d new or changed.",
            thread_id,
            len(facts),
            changed,
        )

    async def _compact(self, scope: str, thread_id: str) -> None:
        """Select turns, summarize them, and persist the result."""
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
                "Summary NOT produced for thread %s: the turns outside the window "
                "stay outside the context.",
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
            "Thread %s compacted: %d messages up to seq %d into %d characters of summary.",
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
        """Reconstruct the snapshot, or return None for an unknown thread. By default remove past reasoning and clear older tool results; raw=True returns the full transcript for summaries and diagnostics."""
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
                    "kept": 1,
                    "reasoning_removed": 0,
                    "results_emptied": 0,
                    "messages_dropped": 0,
                    "summarized": 0,
                    "facts": len(facts),
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
                    "Window suspended on thread %s: the summary is not ready yet.", thread_id
                )
                policy = replace(policy, max_messages=None)
            payloads, curation = curate(payloads, policy, summary)
            report = curation.as_dict()

            facts = await self._durable.facts_of(scope, self._max_facts)
            report["facts"] = len(facts)
            if facts:
                payloads = [facts_message(facts), *payloads]
            logger.info(
                "Context of thread %s rebuilt: %d messages out of %d "
                "(%d reasonings removed, %d results emptied, %d dropped).",
                thread_id,
                curation.kept,
                len(messages),
                curation.reasoning_removed,
                curation.results_emptied,
                curation.messages_dropped,
            )

        return Snapshot(
            messages=payloads,
            state=head.get("state"),
            interrupt=head.get("interrupt"),
            session_state=head.get("session_state"),
            curation=report,
        )

    async def apply_retention(
        self, days: int, scope: str | None = None
    ) -> list[tuple[str, str]]:
        """Forgets the threads untouched for longer than `days`.

        Off by default, and never silent: deleting conversations is a product
        decision, and the line that says which ones went is the only trace left.
        Bounded to one scope unless a caller with no scope asks for all of them:
        a maintenance call inside one tenant must not reach into another.
        """
        if days <= 0:
            return []
        cutoff = datetime.now(UTC) - timedelta(days=days)
        expired = await self._durable.threads_older_than(cutoff, scope)
        for scope, thread_id in expired:
            await self.forget(scope, thread_id)
        if expired:
            logger.info(
                "Retention of %d days: %d threads forgotten (%s).",
                days,
                len(expired),
                ", ".join(thread_id for _, thread_id in expired[:10]),
            )
        return expired

    async def reindex(self, scope: str, thread_id: str | None = None) -> int:
        """Rebuilds the semantic index from the transcripts, which are the source.

        The vector index is reconstructible by design: losing Redis has to cost
        a rebuild, not the memories. Without this command that design claim was
        true and unusable.
        """
        if self._embedder is None or self._memories is None:
            logger.warning("Reindex asked for, but semantic search is not configured.")
            return 0

        threads = [thread_id] if thread_id else await self._durable.threads_of(scope)
        indexed = 0
        for thread in threads:
            seqs = await self._durable.seqs_of(scope, thread)
            if seqs:
                await self._memories.forget_thread(scope, thread, seqs)
            await self._durable.set_indexed_upto(scope, thread, 0)
            before = await self._memories.count(scope)
            await self._index_memories(scope, thread)
            indexed += await self._memories.count(scope) - before
        logger.info("Reindex of %s: %d memories rebuilt.", thread_id or scope, indexed)
        return indexed

    async def forget_scope(self, scope: str) -> int:
        """Forget all threads in a scope and return their count."""
        threads = await self._durable.threads_of(scope)
        for thread_id in threads:
            await self.forget(scope, thread_id)
        await self._durable.forget_facts(scope)
        if self._memories:
            await self._memories.forget_scope(scope)
        return len(threads)

    async def check(self) -> dict[str, str]:
        """Report storage health. A Postgres failure prevents durable writes; a Redis failure degrades to durable reads."""
        await self._durable.ping()
        try:
            await self._hot.ping()
        except Exception:
            logger.warning("Redis unreachable: degraded service.", exc_info=True)
            return {"status": "degraded", "durable": "ok", "hot": "down"}
        return {"status": "ok", "durable": "ok", "hot": "ok"}

    async def forget(self, scope: str, thread_id: str) -> int:
        """Delete durable records first, then cache and index entries."""
        seqs = await self._durable.seqs_of(scope, thread_id) if self._memories else []
        removed = await self._durable.forget(scope, thread_id)
        if self._memories and seqs:
            try:
                await self._memories.forget_thread(scope, thread_id, seqs)
            except Exception:
                logger.error(
                    "Memories NOT removed from the index for thread %s: they stay searchable.",
                    thread_id,
                    exc_info=True,
                )
                raise
        try:
            await self._hot.forget(scope, thread_id)
        except Exception:
            logger.error("Hot tail NOT cleared for thread %s.", thread_id, exc_info=True)
            raise
        return removed
