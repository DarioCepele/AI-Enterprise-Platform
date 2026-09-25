"""The HTTP surface: definitions to read, instances to start and to follow."""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

from dbos import DBOS
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware

from .agents import (
    NEEDS_INPUT,
    TERMINAL,
    TOKEN_HEADER,
    AgentGateway,
    summary_of,
    token_is_valid,
)
from .catalog import Catalog, load_catalog
from .config import Settings, get_settings
from .engine import (
    Engine,
    approval_topic,
    current_engine,
    human_topic,
    step_workflow_id,
    use_engine,
)
from .migrations import run_migrations
from .models import AnswerRequest, ApprovalRequest, Event, Instance, StartRequest
from .observability import configure_logging, configure_tracing
from .replay import ReplayDiverged, replay
from .store import InstanceStore, build_pool

logger = logging.getLogger(__name__)

SERVICE_NAME = "process-service"


def create_app(
    catalog: Catalog | None = None,
    store: InstanceStore | None = None,
    settings: Settings | None = None,
    agents: AgentGateway | None = None,
) -> FastAPI:
    """Builds the app. `catalog` and `store` are passed in tests."""
    config = settings or get_settings()
    configure_logging(SERVICE_NAME, as_json=config.json_logs)

    # Loaded here, not on the first request: a broken definition has to stop the
    # boot, not surface in front of whoever is using the process.
    definitions = (
        catalog if catalog is not None else load_catalog(config.definitions_path)
    )
    gateway = (
        agents if agents is not None else AgentGateway(config.agents, config.public_url)
    )
    state: dict[str, InstanceStore] = {"store": store} if store is not None else {}

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if store is not None:
            use_engine(Engine(definitions, store, gateway))
            yield
            return

        pool = build_pool(
            config.postgres_dsn, config.pool_min_size, config.pool_max_size
        )
        await pool.open(wait=True)
        async with pool.connection() as connection:
            await run_migrations(connection)
        running_store = InstanceStore(pool)
        state["store"] = running_store
        use_engine(Engine(definitions, running_store, gateway))

        # DBOS owns the durability: it keeps the ledger of what each instance has
        # already done, and on launch it picks up the workflows this process --
        # or the one it replaces -- left half-finished.
        DBOS(
            config={
                "name": SERVICE_NAME,
                "system_database_url": config.postgres_dsn,
                "run_admin_server": False,
                "enable_otlp": False,
            }
        )
        DBOS.launch()
        try:
            yield
        finally:
            DBOS.destroy()
            await pool.close()

    app = FastAPI(title="Process service", lifespan=lifespan)

    # The interface reads the instances straight from here: it is a browser
    # talking to this service, so the origins that may do it are named. A
    # service that answered everybody would let any page somebody has open read
    # what is running.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(config.allowed_origins),
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-Process-Scope"],
    )
    configure_tracing(SERVICE_NAME, app)

    def current_store() -> InstanceStore:
        instance = state.get("store")
        if instance is None:
            raise HTTPException(status_code=503, detail="service not initialized")
        return instance

    def current_scope(
        scope: str | None = Header(default=None, alias=config.scope_header),
    ) -> str:
        """The authorization boundary, with the same seam as the other services."""
        return (scope or "").strip() or config.default_scope

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        """Whether this process is stuck. It asks the database nothing."""
        return {"status": "alive"}

    @app.get("/health")
    @app.get("/health/ready")
    async def ready(
        # FastAPI's dependency-injection pattern: not evaluated at definition
        # time, so B008's "called once at import" concern does not apply here.
        store: InstanceStore = Depends(current_store),
    ) -> dict[str, object]:
        """Whether it can serve: without Postgres an instance cannot be written."""
        try:
            await store.ping()
        except Exception as error:
            raise HTTPException(
                status_code=503, detail=f"instance store unreachable: {error}"
            ) from error
        return {"status": "ok", "processes": len(definitions.all())}

    @app.get("/processes")
    async def list_processes() -> dict[str, list[dict[str, object]]]:
        """What can be started, and in which versions."""
        return {
            "processes": [
                {
                    "id": definition.id,
                    "version": definition.version,
                    "name": definition.name,
                    "steps": len(definition.steps),
                }
                for definition in definitions.all()
            ]
        }

    @app.get("/processes/{process_id}")
    async def read_process(
        process_id: str, version: int | None = None
    ) -> dict[str, Any]:
        try:
            definition = (
                definitions.get(process_id, version)
                if version is not None
                else definitions.latest(process_id)
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return definition.model_dump()

    @app.post("/processes/{process_id}/instances", status_code=201)
    async def start_instance(
        process_id: str,
        request: StartRequest,
        scope: str = Depends(current_scope),
        store: InstanceStore = Depends(current_store),
    ) -> Instance:
        """Starts an instance on the latest version, or on the one asked for."""
        try:
            definition = (
                definitions.get(process_id, request.version)
                if request.version is not None
                else definitions.latest(process_id)
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        instance = await store.create(
            scope=scope, definition=definition, payload=request.input
        )
        await current_engine().start(instance.id, scope)
        return instance


    @app.post("/a2a/push/{scope}/{instance_id}/{step_id}")
    async def agent_notification(
        scope: str,
        instance_id: UUID,
        step_id: str,
        request: Request,
        token: str | None = Header(default=None, alias=TOKEN_HEADER),
    ) -> dict[str, str]:
        """The remote agent says a task moved, and the instance wakes up.

        The correlation is in the URL and the authenticity in the signed token:
        an open webhook here would let anyone push an outcome into somebody
        else's process. Only terminal states and questions wake an instance;
        progress is noise.
        """
        if not token_is_valid(str(instance_id), step_id, token):
            logger.warning(
                "Notification refused for instance %s step %s: invalid token.",
                instance_id,
                step_id,
            )
            raise HTTPException(status_code=403, detail="invalid token")

        task_id, state, text = summary_of(await request.json())

        if state not in TERMINAL and state != NEEDS_INPUT:
            return {"state": "progress ignored"}

        logger.info(
            "Instance %s step %s: task %s is %s (%d characters).",
            instance_id,
            step_id,
            task_id[:8] or "?",
            state,
            len(text),
        )
        await DBOS.send_async(
            destination_id=step_workflow_id(str(instance_id), step_id),
            message={"task_id": task_id, "state": state, "text": text},
            topic=step_id,
            idempotency_key=f"{instance_id}:{step_id}:{task_id}:{state}",
        )
        return {"state": "received"}

    @app.get("/instances/{instance_id}")
    async def read_instance(
        instance_id: UUID,
        scope: str = Depends(current_scope),
        store: InstanceStore = Depends(current_store),
    ) -> Instance:
        instance = await store.get(scope=scope, instance_id=instance_id)
        if instance is None:
            raise HTTPException(status_code=404, detail="unknown instance")
        return instance

    @app.post("/instances/{instance_id}/steps/{step_id}/answer")
    async def answer_step(
        instance_id: UUID,
        step_id: str,
        request: AnswerRequest,
        scope: str = Depends(current_scope),
        store: InstanceStore = Depends(current_store),
    ) -> dict[str, str]:
        """A person answers what the agent stopped to ask.

        The answer belongs to the step that is waiting for it, so it is refused
        anywhere else: an answer sent to a step nobody asked about would sit in
        the mailbox and be read by the next question.
        """
        instance = await store.get(scope=scope, instance_id=instance_id)
        if instance is None:
            raise HTTPException(status_code=404, detail="unknown instance")

        waiting = next(
            (step for step in instance.steps if step.step_id == step_id), None
        )
        if waiting is None:
            raise HTTPException(status_code=404, detail=f"unknown step '{step_id}'")
        if waiting.status != "waiting_human":
            raise HTTPException(
                status_code=409,
                detail=(
                    f"step '{step_id}' is {waiting.status}, it is not waiting for an "
                    "answer"
                ),
            )

        await DBOS.send_async(
            destination_id=step_workflow_id(str(instance_id), step_id),
            message={"text": request.text},
            topic=human_topic(step_id),
        )
        return {"state": "answered"}

    @app.post("/instances/{instance_id}/steps/{step_id}/decision")
    async def decide_step(
        instance_id: UUID,
        step_id: str,
        request: ApprovalRequest,
        scope: str = Depends(current_scope),
        store: InstanceStore = Depends(current_store),
    ) -> dict[str, str]:
        """A person approves or refuses a step that is waiting for a decision.

        Deciding twice does nothing: the second decision arrives when the step
        is no longer waiting, and is refused instead of being kept for the next
        thing that waits. Who decided goes on the row, because "the process
        stopped here and somebody let it through" is the question this answers.
        """
        instance = await store.get(scope=scope, instance_id=instance_id)
        if instance is None:
            raise HTTPException(status_code=404, detail="unknown instance")

        waiting = next(
            (step for step in instance.steps if step.step_id == step_id), None
        )
        if waiting is None:
            raise HTTPException(status_code=404, detail=f"unknown step '{step_id}'")
        if waiting.status != "waiting_approval":
            raise HTTPException(
                status_code=409,
                detail=(
                    f"step '{step_id}' is {waiting.status}, it is not waiting for a "
                    "decision"
                ),
            )

        definition = definitions.get(instance.process_id, instance.process_version)
        step = definition.step(step_id)
        approvers = list(step.approvers) if step else []
        if approvers and request.by not in approvers:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"'{request.by}' cannot decide '{step_id}'. "
                    f"Approvers: {', '.join(approvers)}"
                ),
            )

        await DBOS.send_async(
            destination_id=step_workflow_id(str(instance_id), step_id),
            message={
                "decision": request.decision,
                "by": request.by,
                "note": request.note,
            },
            topic=approval_topic(step_id),
        )
        logger.info(
            "Instance %s step %s: %s by %s.",
            instance_id,
            step_id,
            request.decision,
            request.by,
        )
        return {"state": request.decision}

    @app.get("/instances/{instance_id}/events")
    async def read_events(
        instance_id: UUID,
        after: int = Query(default=0, ge=0),
        scope: str = Depends(current_scope),
        store: InstanceStore = Depends(current_store),
    ) -> dict[str, list[Event]]:
        """Everything that happened to this instance, in order.

        `after` is the id of the last event already read: the history only ever
        grows at the end, so following an instance is asking for what came after
        what you have.
        """
        instance = await store.get(scope=scope, instance_id=instance_id)
        if instance is None:
            raise HTTPException(status_code=404, detail="unknown instance")
        return {"events": await store.events_of(instance_id=instance_id, after=after)}

    @app.get("/instances/{instance_id}/replay")
    async def replay_instance(
        instance_id: UUID,
        scope: str = Depends(current_scope),
        store: InstanceStore = Depends(current_store),
    ) -> dict[str, Any]:
        """Walks the history again, and says which way the instance went.

        Nothing is called: the tools, the agents and the model are read back
        from the events. When the answer disagrees with what was recorded, that
        is a 409 and not a path -- the history has stopped explaining the
        instance, and saying so is the useful answer.
        """
        instance = await store.get(scope=scope, instance_id=instance_id)
        if instance is None:
            raise HTTPException(status_code=404, detail="unknown instance")

        definition = definitions.get(instance.process_id, instance.process_version)
        events = await store.events_of(instance_id=instance_id)
        try:
            walked = replay(definition, events)
        except ReplayDiverged as divergence:
            raise HTTPException(status_code=409, detail=str(divergence)) from divergence
        return {
            "path": walked.path,
            "decisions": walked.decisions,
            "outputs": walked.outputs,
            "context": walked.context,
            "status": walked.status,
            "undone": walked.undone,
        }

    @app.get("/instances")
    async def list_instances(
        status: str | None = None,
        limit: int = Query(default=50, ge=1, le=500),
        scope: str = Depends(current_scope),
        store: InstanceStore = Depends(current_store),
    ) -> dict[str, list[Instance]]:
        """The instances of this scope, newest first, filtered by state."""
        return {"instances": await store.list(scope=scope, status=status, limit=limit)}

    logger.info(
        "Process service ready: %d definitions, scope header %s.",
        len(definitions.all()),
        config.scope_header,
    )
    return app
