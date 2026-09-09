"""FastAPI app: exposes the master agent over AG-UI on SSE, plus the operational logs."""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from redis.asyncio import Redis
from agent_framework import Agent
from agent_framework.ag_ui import (
    AGUIThreadSnapshotStore,
    InMemoryAGUIThreadSnapshotStore,
    add_agent_framework_fastapi_endpoint,
)
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from ..agents.master import build_master_agent
from ..config import get_settings
from ..logging_bridge import LogCollector, RedisLogStream
from ..observability import configure_logging, configure_tracing
from ..a2a.client import A2AClient, fetch_agent_card
from ..a2a.push import HEADER, SeenNotifications, is_terminal, summary_of, token_is_valid
from ..memory.remote_store import MemoryServiceSnapshotStore
from .run_context import LabRunner
from .scope import ScopeResolver, scope_of_request

logger = logging.getLogger(__name__)

SERVICE_NAME = "master-agent"

DEFAULT_STATE = {"artifacts": [], "plan": {"status": "idle", "steps": []}}

MAX_REQUEST_BYTES = 1_000_000

def _default_snapshot_store() -> AGUIThreadSnapshotStore:
    """The thread store: the memory service when configured, otherwise RAM.

    The in-memory fallback is not laziness: it keeps the laboratory startable
    with the master agent alone, without bringing up Mongo and Redis to ask two
    questions. Which of the two is active is written in the logs at startup,
    because the difference -- the conversation survives a restart, or it does
    not -- only becomes visible when it is too late to notice.
    """
    url = get_settings().memory_service_url
    if not url:
        logger.info("Thread memory in RAM: it is lost when the process restarts.")
        return InMemoryAGUIThreadSnapshotStore()
    logger.info("Thread memory in the memory service: %s", url)
    return MemoryServiceSnapshotStore(url)

def _shared_redis() -> Redis | None:
    uri = get_settings().redis_uri
    return Redis.from_url(uri, decode_responses=True) if uri else None


def _shared_log_stream(collector: LogCollector) -> RedisLogStream | None:
    """The operational logs of every replica in one stream, when Redis is there.

    Without it each replica answers with its own buffer, and the LOG tab shows
    the half of the story that belongs to whoever picked up the request.
    """
    uri = get_settings().redis_uri
    if not uri:
        logger.info("Operational logs kept in this process: no DEMO_REDIS_URI configured.")
        return None
    stream = RedisLogStream(Redis.from_url(uri, decode_responses=True))
    stream.attach(collector)
    logger.info("Operational logs published to the shared stream on %s.", uri.split("@")[-1])
    return stream

def create_app(
    agent: Agent | None = None,
    collector: LogCollector | None = None,
    snapshot_store: AGUIThreadSnapshotStore | None = None,
    scope_resolver: ScopeResolver | None = None,
) -> FastAPI:
    """Builds the app. `agent`, `collector`, the store and the resolver are passed in tests."""
    resolve_scope: ScopeResolver = scope_resolver or scope_of_request
    # Uvicorn configures only its own loggers: without this, `demo.*` ends up in
    # the handler of last resort, which prints only WARNING and above and leaves
    # the container mute exactly when it needs to be read.
    configure_logging(SERVICE_NAME, as_json=get_settings().json_logs)

    log_collector = collector if collector is not None else LogCollector()
    log_collector.attach()
    log_stream = _shared_log_stream(log_collector)
    seen = SeenNotifications(_shared_redis())

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if log_stream is None:
            try:
                yield
            finally:
                log_collector.detach()
            return
        async with log_stream.running():
            try:
                yield
            finally:
                log_collector.detach()

    app = FastAPI(title=get_settings().product_name, lifespan=lifespan)

    @app.middleware("http")
    async def refuse_oversized_bodies(request: Request, call_next):
        """A ceiling on what a client may send.

        Without one, the process holds whatever arrives: the limit is generous
        enough for a long conversation and small enough that nobody can pin the
        agent with a single request.
        """
        declared = request.headers.get("content-length")
        if declared and int(declared) > MAX_REQUEST_BYTES:
            logger.warning("Request refused: %s bytes declared.", declared)
            return JSONResponse(
                {"detail": f"request too large: over {MAX_REQUEST_BYTES} bytes"},
                status_code=413,
            )
        return await call_next(request)
    app.state.scope_resolver = resolve_scope

    allowed_origins = list(get_settings().allowed_origins)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,

        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    @app.get("/health")
    @app.get("/health/live")
    async def live() -> dict[str, str]:
        """Whether this process is stuck.

        It asks nothing of anyone: a liveness probe that called the memory
        service would restart a healthy agent because a dependency went away,
        turning an outage into a restart loop.
        """
        return {"status": "alive"}

    @app.get("/health/ready")
    async def ready() -> dict[str, object]:
        """Whether this process can serve, and which dependencies were asked.

        The subagents are not among them: one that is down degrades a turn, and
        the tool says so. The memory service is, because without it every thread
        starts from scratch without anyone noticing.
        """
        settings = get_settings()
        checked: list[str] = []
        if settings.memory_service_url:
            checked.append("memory")
            try:
                async with httpx.AsyncClient(timeout=2.0) as http:
                    answer = await http.get(f"{settings.memory_service_url}/health/ready")
                    answer.raise_for_status()
            except Exception as error:
                raise HTTPException(
                    status_code=503, detail=f"memory service unreachable: {error}"
                ) from error
        if settings.redis_uri:
            checked.append("redis")
            try:
                client = Redis.from_url(settings.redis_uri, decode_responses=True)
                try:
                    await client.ping()
                finally:
                    await client.aclose()
            except Exception as error:
                # Redis carries the shared logs, not the conversation: it is
                # reported, and it does not take the agent out of service.
                logger.warning("Redis unreachable: shared logs degraded.", exc_info=True)
                return {"status": "degraded", "checked": checked, "detail": str(error)}
        return {"status": "ok", "checked": checked}

    async def outcome_of_task(task_id: str) -> str:
        """Fetches the result: the notification says it is done, not what it says."""
        url = get_settings().knowledge_agent_url
        if not url or not task_id:
            return ""
        try:
            client = A2AClient(await fetch_agent_card(url))
            try:
                return (await client.outcome(task_id)).text
            finally:
                await client.aclose()
        except Exception:
            logger.error("Outcome of task %s not recoverable.", task_id[:8], exc_info=True)
            return ""

    async def note_in_memory(
        scope: str, thread_id: str, task_id: str, state: str, text: str
    ) -> None:
        """Writes the outcome into the thread memory, so the next turn sees it.

        With no memory service only the log line remains: the notification is
        not lost silently, but it enters no conversation.
        """
        url = get_settings().memory_service_url
        if not url:
            logger.warning("No memory service: the outcome of task %s stays in the logs.", task_id[:8])
            return
        message = (
            f"The subagent completed task {task_id[:8]} ({state}).\n{text}"
            if text
            else f"The subagent closed task {task_id[:8]} with state {state}, with no answer."
        )
        try:
            async with httpx.AsyncClient(base_url=url, timeout=5.0) as http:
                response = await http.post(
                    f"/threads/{thread_id}/messages",
                    json={"role": "assistant", "content": message, "meta": {"task_id": task_id}},
                    headers={"X-Memory-Scope": scope},
                )
                response.raise_for_status()
        except Exception:
            logger.error("Outcome of task %s NOT noted in memory.", task_id[:8], exc_info=True)

    @app.post("/a2a/push/{scope}/{thread_id}")
    async def subagent_notification(
        scope: str,
        thread_id: str,
        request: Request,
        token: str | None = Header(default=None, alias=HEADER),
    ) -> dict[str, str]:
        """Receives the outcome of a task the subagent finished after the run.

        The correlation to the thread lives in the URL, the authenticity in the
        signed token. Without a valid token it is refused: an open webhook is a
        way to let anyone write into a conversation's memory.
        """
        notification = await request.json()
        task_id, state, text = summary_of(notification)
        if not token_is_valid(thread_id, token):
            logger.warning("Push notification refused for task %s: invalid token.", task_id)
            raise HTTPException(status_code=403, detail="invalid token")

        if not is_terminal(state):
            # The subagent notifies every event, not only the end: writing to
            # memory on every progress step would fill the conversation with noise.
            return {"state": "progress ignored"}

        if not await seen.first_time(thread_id, task_id, state):
            logger.info("Notification for task %s already seen: ignored.", task_id[:8] or "?")
            return {"state": "already seen"}

        if not text:
            text = await outcome_of_task(task_id)

        logger.info(
            "The subagent completed task %s (%s) on thread %s: %d characters.",
            task_id[:8] or "?",
            state or "unknown state",
            thread_id,
            len(text),
        )
        await note_in_memory(scope, thread_id, task_id, state, text)
        return {"state": "received"}

    @app.get("/logs")
    async def logs(cursor: str = "") -> dict[str, object]:
        """The application logs after `cursor`.

        A channel separate from the AG-UI stream: the protocol's CUSTOM events
        are reserved to the framework and application code cannot emit them.

        The cursor is opaque: with a shared stream it carries a position in it,
        without one it carries a sequence number local to this replica. A client
        that hands back what it received works with either.
        """
        if log_stream is not None:
            try:
                return await log_stream.since(cursor)
            except Exception:
                logger.warning("Shared logs unreadable: answering with this replica's own.")
        return log_collector.since(cursor)

    store = snapshot_store or _default_snapshot_store()

    async def state_of_thread(thread_id: str) -> dict | None:
        snapshot = await store.get(scope=get_settings().default_scope, thread_id=thread_id)
        state = getattr(snapshot, "state", None)
        return state if isinstance(state, dict) else None

    runner = LabRunner(agent=agent or build_master_agent(), state_loader=state_of_thread)
    add_agent_framework_fastapi_endpoint(
        app,
        runner,
        "/agui",
        allow_origins=allowed_origins,
        default_state=DEFAULT_STATE,
        snapshot_store=store,
        snapshot_scope_resolver=resolve_scope,
    )
    configure_tracing(SERVICE_NAME, app)
    return app
