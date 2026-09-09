"""HTTP API for the internal memory service. Callers supply a verified scope. Deploy on a private network with service authentication; never expose it directly to browsers or accept an end-user scope."""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query
from pymongo import AsyncMongoClient
from redis.asyncio import Redis

from .config import Settings, get_settings
from .curation import ContextPolicy
from .migrations import run_migrations
from .observability import configure_logging, configure_tracing
from .models import NewMessage, ReindexRequest, RetentionRequest, SearchQuery, Snapshot, StoredMessage, Transcript
from .service import ThreadMemory
from .stores.hot import HotTail
from .stores.locks import RedisLock
from .embedder import OpenAICompatibleEmbedder
from .stores.mongo import MongoTranscripts, build_client
from .stores.vectors import RedisMemories
from .summarizer import OpenAICompatibleSummarizer

logger = logging.getLogger(__name__)

SERVICE_NAME = "memory-service"


def create_app(memory: ThreadMemory | None = None, settings: Settings | None = None) -> FastAPI:
    """Build the application. Pass memory explicitly in tests."""
    config = settings or get_settings()
    configure_logging(SERVICE_NAME, as_json=config.json_logs)
    state: dict[str, object] = {"memory": memory} if memory is not None else {}

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if memory is not None:
            yield
            return

        client: AsyncMongoClient = build_client(config.mongo_uri)
        redis = Redis.from_url(config.redis_uri, decode_responses=True)
        memories = RedisMemories(redis) if config.embedding_model else None
        embedder = (
            OpenAICompatibleEmbedder(
                config.summary_base_url, config.summary_api_key, config.embedding_model
            )
            if config.embedding_model
            else None
        )
        if memories is None:
            logger.warning(
                "No embedding model (MEMORY_EMBEDDING_MODEL): semantic search over "
                "memories will not be available."
            )
        durable = MongoTranscripts(client, config.mongo_database, config.bucket_size)
        await run_migrations(client[config.mongo_database])
        await durable.ensure_indexes()
        summarizer = None
        if config.summary_model:
            summarizer = OpenAICompatibleSummarizer(
                config.summary_base_url, config.summary_api_key, config.summary_model
            )
            logger.info(
                "Compaction and durable facts active with model %s.", config.summary_model
            )
        else:
            logger.warning(
                "No model for summaries (MEMORY_SUMMARY_MODEL): turns leaving the "
                "window will drop out of the context unsummarized, and no "
                "durable fact will be learned."
            )
        state["memory"] = ThreadMemory(
            durable,
            HotTail(redis, config.hot_tail_seconds, config.hot_tail_messages),
            ContextPolicy(
                drop_reasoning=config.drop_reasoning,
                keep_tool_results=config.keep_tool_results,
                max_messages=config.max_context_messages,
            ),
            summarizer,
            summarizer,
            config.max_facts,
            embedder,
            memories,
            RedisLock(redis),
        )
        try:
            yield
        finally:
            await client.close()
            await redis.aclose()

    app = FastAPI(title="Memoria conversazionale", lifespan=lifespan)
    configure_tracing(SERVICE_NAME, app)

    def current_memory() -> ThreadMemory:
        instance = state.get("memory")
        if instance is None:
            raise HTTPException(status_code=503, detail="service not initialized")
        return instance

    def current_scope(
        scope: str = Header(
            ...,
            alias="X-Memory-Scope",
            description="Authorization boundary: the identity verified by the caller.",
        ),
    ) -> str:
        if not scope.strip():
            raise HTTPException(status_code=400, detail="empty scope")
        return scope

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        """Whether this process is stuck.

        It asks nothing of the databases on purpose: a liveness probe that did
        would restart a healthy process because a database went away, which
        turns an outage into a restart loop.
        """
        return {"status": "alive"}

    @app.get("/health")
    @app.get("/health/ready")
    async def ready(
        memory_instance: ThreadMemory = Depends(current_memory),
    ) -> dict[str, str]:
        """Whether this process can serve: Mongo is required, Redis is not."""
        try:
            return await memory_instance.check()
        except Exception as error:
            raise HTTPException(
                status_code=503, detail=f"durable memory unreachable: {error}"
            ) from error

    @app.post("/threads/{thread_id}/messages", status_code=201)
    async def append_message(
        thread_id: str,
        message: NewMessage,
        scope: str = Depends(current_scope),
        memory_instance: ThreadMemory = Depends(current_memory),
    ) -> StoredMessage:
        return await memory_instance.append(scope, thread_id, message)

    @app.get("/threads/{thread_id}/messages")
    async def read_tail(
        thread_id: str,
        limit: int = Query(default=50, ge=1, le=500),
        scope: str = Depends(current_scope),
        memory_instance: ThreadMemory = Depends(current_memory),
    ) -> Transcript:
        return await memory_instance.tail(scope, thread_id, limit)

    @app.put("/threads/{thread_id}/snapshot")
    async def save_snapshot(
        thread_id: str,
        snapshot: Snapshot,
        background: BackgroundTasks,
        scope: str = Depends(current_scope),
        memory_instance: ThreadMemory = Depends(current_memory),
    ) -> dict[str, int]:
        """Store the full thread state without duplicating turns. Run compaction after responding: model latency can otherwise time out the client, and the summary is needed only on the next turn."""
        written = await memory_instance.save_snapshot(scope, thread_id, snapshot)
        background.add_task(memory_instance.compact_if_needed, scope, thread_id)
        return {"new_turns": written}

    @app.get("/threads/{thread_id}/snapshot")
    async def read_snapshot(
        thread_id: str,
        raw: bool = Query(
            default=False,
            description="The full transcript instead of the pruned context.",
        ),
        scope: str = Depends(current_scope),
        memory_instance: ThreadMemory = Depends(current_memory),
    ) -> Snapshot:
        snapshot = await memory_instance.read_snapshot(scope, thread_id, raw=raw)
        if snapshot is None:
            raise HTTPException(status_code=404, detail="unknown thread")
        return snapshot

    @app.delete("/threads/{thread_id}")
    async def forget_thread(
        thread_id: str,
        scope: str = Depends(current_scope),
        memory_instance: ThreadMemory = Depends(current_memory),
    ) -> dict[str, int]:
        return {"buckets_removed": await memory_instance.forget(scope, thread_id)}

    @app.post("/search")
    async def search(
        query: SearchQuery,
        scope: str = Depends(current_scope),
        memory_instance: ThreadMemory = Depends(current_memory),
    ) -> dict[str, list[dict[str, object]]]:
        """Search memories within the scope by meaning. Use POST to keep conversation content out of proxy URL access logs."""
        found = await memory_instance.search_memories(scope, query.query, query.limit)
        return {
            "memories": [
                {
                    "thread_id": memory.thread_id,
                    "seq": memory.seq,
                    "text": memory.text,
                    "similarity": round(memory.similarity, 4),
                }
                for memory in found
            ]
        }

    @app.post("/admin/retention")
    async def apply_retention(
        request: RetentionRequest,
        scope: str = Depends(current_scope),
        memory_instance: ThreadMemory = Depends(current_memory),
    ) -> dict[str, int]:
        """Forget the threads older than the retention. Meant for a scheduled job, not for a request path."""
        days = request.days if request.days is not None else config.retention_days
        forgotten = await memory_instance.apply_retention(days, scope)
        return {"threads_forgotten": len(forgotten)}

    @app.post("/admin/reindex")
    async def reindex(
        request: ReindexRequest,
        scope: str = Depends(current_scope),
        memory_instance: ThreadMemory = Depends(current_memory),
    ) -> dict[str, int]:
        """Rebuild the semantic index of a scope, or of one thread, from the transcripts."""
        return {"memories_indexed": await memory_instance.reindex(scope, request.thread_id)}

    @app.delete("/scope")
    async def forget_scope(
        scope: str = Depends(current_scope),
        memory_instance: ThreadMemory = Depends(current_memory),
    ) -> dict[str, int]:
        """Forget every thread in one scope. Cross-scope deletion is intentionally unavailable to preserve authorization boundaries."""
        return {"threads_removed": await memory_instance.forget_scope(scope)}

    return app
