"""Durable conversation transcripts in MongoDB. Redis data must be reconstructible from this store. Deployment uses a single node without a replica set, so atomic operations must fit within one document and never rely on multi-document transactions."""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from pymongo import ASCENDING, DESCENDING, AsyncMongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError

from ..models import NewMessage, StoredMessage

logger = logging.getLogger(__name__)

TURNS = "thread_turns"
THREADS = "threads"
SUMMARIES = "thread_summaries"
FACTS = "scope_facts"


def build_client(uri: str) -> AsyncMongoClient:
    """Build one shared Mongo client per process. Pool settings target a single laboratory instance with short operations and low concurrency; increase them for replicated or production workloads."""
    return AsyncMongoClient(
        uri,
        maxPoolSize=20,
        minPoolSize=2,
        maxIdleTimeMS=300_000,
        connectTimeoutMS=5_000,
        serverSelectionTimeoutMS=5_000,
        socketTimeoutMS=30_000,
        tz_aware=True,
    )


class MongoTranscripts:
    """Store transcripts in buckets of bucket_size messages."""

    def __init__(self, client: AsyncMongoClient, database: str, bucket_size: int) -> None:
        self._db = client[database]
        self._bucket_size = bucket_size

    async def ensure_indexes(self) -> None:
        """Create indexes idempotently on startup. Unique scope/thread/bucket keys allow safe concurrent bucket creation without transactions; losing writers retry."""
        await self._db[TURNS].create_index(
            [("scope", ASCENDING), ("thread_id", ASCENDING), ("bucket", ASCENDING)],
            unique=True,
            name="unique_thread_bucket",
        )
        await self._db[TURNS].create_index(
            [("scope", ASCENDING), ("thread_id", ASCENDING), ("last_seq", DESCENDING)],
            name="thread_tail",
        )
        await self._db[THREADS].create_index(
            [("scope", ASCENDING), ("thread_id", ASCENDING)],
            unique=True,
            name="unique_thread",
        )
        await self._db[FACTS].create_index(
            [("scope", ASCENDING), ("key", ASCENDING)],
            unique=True,
            name="unique_fact",
        )
        await self._db[SUMMARIES].create_index(
            [("scope", ASCENDING), ("thread_id", ASCENDING), ("covers_to_seq", DESCENDING)],
            unique=True,
            name="unique_summary",
        )

    async def _next_seq(self, scope: str, thread_id: str) -> int:
        """Allocate the next position with atomic single-document $inc. A later failed append may leave a numbering gap, but never a lost or duplicated message."""
        now = datetime.now(UTC)
        document = await self._db[THREADS].find_one_and_update(
            {"scope": scope, "thread_id": thread_id},
            {
                "$inc": {"next_seq": 1},
                "$set": {"updated_at": now},
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        return int(document["next_seq"])

    async def append(self, scope: str, thread_id: str, message: NewMessage) -> StoredMessage:
        """Append a turn and return its stored representation."""
        seq = await self._next_seq(scope, thread_id)
        stored = StoredMessage(
            **message.model_dump(),
            seq=seq,
            ts=datetime.now(UTC),
        )
        entry = stored.model_dump()

        updated = await self._db[TURNS].find_one_and_update(
            {
                "scope": scope,
                "thread_id": thread_id,
                "count": {"$lt": self._bucket_size},
            },
            {
                "$push": {"messages": entry},
                "$inc": {"count": 1},
                "$set": {"last_seq": seq, "updated_at": stored.ts},
            },
            sort=[("bucket", DESCENDING)],
            return_document=ReturnDocument.AFTER,
        )
        if updated is not None:
            return stored

        last = await self._db[TURNS].find_one(
            {"scope": scope, "thread_id": thread_id},
            sort=[("bucket", DESCENDING)],
            projection={"bucket": 1},
        )
        bucket = 0 if last is None else int(last["bucket"]) + 1
        try:
            await self._db[TURNS].insert_one(
                {
                    "scope": scope,
                    "thread_id": thread_id,
                    "bucket": bucket,
                    "count": 1,
                    "first_seq": seq,
                    "last_seq": seq,
                    "created_at": stored.ts,
                    "updated_at": stored.ts,
                    "messages": [entry],
                }
            )
        except DuplicateKeyError:
            logger.info("Bucket %d already created by another write, retrying at the tail.", bucket)
            await self._db[TURNS].update_one(
                {"scope": scope, "thread_id": thread_id, "bucket": bucket},
                {
                    "$push": {"messages": entry},
                    "$inc": {"count": 1},
                    "$set": {"last_seq": seq, "updated_at": stored.ts},
                },
            )
        return stored

    async def save_head(
        self,
        scope: str,
        thread_id: str,
        *,
        state: dict[str, Any] | None,
        interrupt: list[dict[str, Any]] | None,
        session_state: dict[str, Any] | None,
    ) -> None:
        """Store non-message thread state in the counter document without rewriting conversation buckets."""
        await self._db[THREADS].update_one(
            {"scope": scope, "thread_id": thread_id},
            {
                "$set": {
                    "state": state,
                    "interrupt": interrupt,
                    "session_state": session_state,
                    "updated_at": datetime.now(UTC),
                },
                "$setOnInsert": {"created_at": datetime.now(UTC), "next_seq": 0},
            },
            upsert=True,
        )

    async def read_head(self, scope: str, thread_id: str) -> dict[str, Any] | None:
        return await self._db[THREADS].find_one(
            {"scope": scope, "thread_id": thread_id},
            projection={"state": 1, "interrupt": 1, "session_state": 1, "_id": 0},
        )

    async def history(self, scope: str, thread_id: str) -> list[StoredMessage]:
        """Return the entire conversation in chronological order for snapshot reconstruction and compaction."""
        messages: list[StoredMessage] = []
        cursor = (
            self._db[TURNS]
            .find({"scope": scope, "thread_id": thread_id}, projection={"messages": 1})
            .sort("bucket", ASCENDING)
        )
        async for document in cursor:
            messages.extend(StoredMessage(**entry) for entry in document.get("messages", []))
        return messages

    async def tail(self, scope: str, thread_id: str, limit: int) -> list[StoredMessage]:
        """Return the latest limit messages, oldest first. Read buckets backwards until enough messages are available."""
        collected: list[dict[str, Any]] = []
        cursor = (
            self._db[TURNS]
            .find({"scope": scope, "thread_id": thread_id}, projection={"messages": 1})
            .sort("bucket", DESCENDING)
        )
        async for document in cursor:
            collected = list(document.get("messages", [])) + collected
            if len(collected) >= limit:
                break
        return [StoredMessage(**entry) for entry in collected[-limit:]]

    async def save_summary(
        self,
        scope: str,
        thread_id: str,
        *,
        text: str,
        covers_to_seq: int,
        message_count: int,
        model: str,
    ) -> None:
        """Store a summary through covers_to_seq. Preserve earlier summaries to trace what the agent knew and diagnose information lost during compaction."""
        await self._db[SUMMARIES].update_one(
            {"scope": scope, "thread_id": thread_id, "covers_to_seq": covers_to_seq},
            {
                "$set": {
                    "text": text,
                    "message_count": message_count,
                    "model": model,
                    "created_at": datetime.now(UTC),
                }
            },
            upsert=True,
        )

    async def latest_summary(self, scope: str, thread_id: str) -> dict[str, Any] | None:
        """Return the most advanced summary for a thread, if available."""
        return await self._db[SUMMARIES].find_one(
            {"scope": scope, "thread_id": thread_id},
            sort=[("covers_to_seq", DESCENDING)],
            projection={"text": 1, "covers_to_seq": 1, "_id": 0},
        )

    async def upsert_facts(
        self,
        scope: str,
        facts: list[tuple[str, str]],
        *,
        thread_id: str,
    ) -> int:
        """Upsert scope facts and return the number changed. A stable key updates an existing fact instead of creating conflicting duplicates."""
        changed = 0
        for key, value in facts:
            result = await self._db[FACTS].update_one(
                {"scope": scope, "key": key},
                {
                    "$set": {
                        "value": value,
                        "updated_at": datetime.now(UTC),
                        "thread_id": thread_id,
                    },
                    "$setOnInsert": {"created_at": datetime.now(UTC)},
                },
                upsert=True,
            )
            if result.upserted_id is not None or result.modified_count:
                changed += 1
        return changed

    async def facts_of(self, scope: str, limit: int) -> list[dict[str, Any]]:
        """Return recent scope facts, bounded to prevent unbounded context growth."""
        cursor = (
            self._db[FACTS]
            .find({"scope": scope}, projection={"key": 1, "value": 1, "_id": 0})
            .sort("updated_at", DESCENDING)
            .limit(limit)
        )
        return [document async for document in cursor]

    async def forget_facts(self, scope: str) -> int:
        result = await self._db[FACTS].delete_many({"scope": scope})
        return int(result.deleted_count)

    async def indexed_upto(self, scope: str, thread_id: str) -> int:
        """Read the durable indexing checkpoint. The vector index is reconstructible and may disappear, but progress is stored durably."""
        document = await self._db[THREADS].find_one(
            {"scope": scope, "thread_id": thread_id}, projection={"indexed_upto": 1, "_id": 0}
        )
        return int((document or {}).get("indexed_upto") or 0)

    async def set_indexed_upto(self, scope: str, thread_id: str, seq: int) -> None:
        await self._db[THREADS].update_one(
            {"scope": scope, "thread_id": thread_id}, {"$set": {"indexed_upto": seq}}
        )

    async def seqs_of(self, scope: str, thread_id: str) -> list[int]:
        """Return message positions for removing a thread from the index."""
        return [message.seq for message in await self.history(scope, thread_id)]

    async def threads_of(self, scope: str) -> list[str]:
        """Return thread identifiers within a scope."""
        return [str(value) for value in await self._db[THREADS].distinct('thread_id', {'scope': scope})]

    async def forget(self, scope: str, thread_id: str) -> int:
        """Delete a conversation and return the number of removed buckets."""
        result = await self._db[TURNS].delete_many({"scope": scope, "thread_id": thread_id})
        await self._db[THREADS].delete_one({"scope": scope, "thread_id": thread_id})
        await self._db[SUMMARIES].delete_many({"scope": scope, "thread_id": thread_id})
        return int(result.deleted_count)

    async def ping(self) -> None:
        """Raise if the database does not respond."""
        await self._db.command("ping")
