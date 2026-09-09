"""The HTTP surface: definitions to read, instances to start and to follow."""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

from dbos import DBOS
from fastapi import Depends, FastAPI, Header, HTTPException, Query

from .catalog import Catalog, load_catalog
from .config import Settings, get_settings
from .definitions import DefinitionError
from .engine import Engine, current_engine, use_engine
from .migrations import run_migrations
from .models import Instance, StartRequest
from .observability import configure_logging, configure_tracing
from .store import InstanceStore, build_pool

logger = logging.getLogger(__name__)

SERVICE_NAME = "process-service"


def create_app(
    catalog: Catalog | None = None,
    store: InstanceStore | None = None,
    settings: Settings | None = None,
) -> FastAPI:
    """Builds the app. `catalog` and `store` are passed in tests."""
    config = settings or get_settings()
    configure_logging(SERVICE_NAME, as_json=config.json_logs)

    # Loaded here, not on the first request: a broken definition has to stop the
    # boot, not surface in front of whoever is using the process.
    definitions = catalog if catalog is not None else load_catalog(config.definitions_path)
    state: dict[str, Any] = {"store": store} if store is not None else {}

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if store is not None:
            use_engine(Engine(definitions, store))
            yield
            return

        pool = build_pool(config.postgres_dsn, config.pool_min_size, config.pool_max_size)
        await pool.open(wait=True)
        async with pool.connection() as connection:
            await run_migrations(connection)
        running_store = InstanceStore(pool)
        state["store"] = running_store
        use_engine(Engine(definitions, running_store))

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
    configure_tracing(SERVICE_NAME, app)

    def current_store() -> InstanceStore:
        instance = state.get("store")
        if instance is None:
            raise HTTPException(status_code=503, detail="service not initialized")
        return instance

    def current_scope(scope: str | None = Header(default=None, alias=config.scope_header)) -> str:
        """The authorization boundary, with the same seam as the other services."""
        return (scope or "").strip() or config.default_scope

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        """Whether this process is stuck. It asks the database nothing."""
        return {"status": "alive"}

    @app.get("/health")
    @app.get("/health/ready")
    async def ready(store: InstanceStore = Depends(current_store)) -> dict[str, object]:
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
    async def read_process(process_id: str, version: int | None = None) -> dict[str, Any]:
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
