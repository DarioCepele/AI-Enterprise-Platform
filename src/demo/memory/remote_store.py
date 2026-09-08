"""A snapshot store that lives in the memory service, not in this process.

It implements the AG-UI adapter's `AGUIThreadSnapshotStore` protocol by
speaking HTTP with `demo-memory-service`. The agent knows neither Mongo nor
Redis: it knows a service, and that service decides how and where to remember.

**Failure policy, stated because it is not obvious.** An unreachable memory
service must not fail the conversation: on reads it degrades to "unknown
thread" and the agent starts without history, on writes the error is logged.
In both cases the line ends up on `demo.*`, hence in the LOG tab: silent
amnesia is the worst defect this piece could have.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx
from agent_framework.ag_ui import AGUIThreadSnapshot

logger = logging.getLogger(__name__)

SCOPE_HEADER = "X-Memory-Scope"

class MemoryServiceSnapshotStore:
    """The threads' memory, held by the memory service."""

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

            logger.error("Memory NOT saved for thread %s.", thread_id, exc_info=True)
            return
        logger.info(
            "Memory of thread %s updated: %s new turns.",
            thread_id,
            response.json().get("new_turns", "?"),
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
                "Memory of thread %s unreadable: starting without history.",
                thread_id,
                exc_info=True,
            )
            return None

        messages = payload.get("messages") or []
        curation = payload.get("curation")
        if curation:

            logger.info(
                "Context from memory: %d messages (%d reasonings removed, "
                "%d results emptied, %d dropped).",
                len(messages),
                curation.get("reasoning_removed", 0),
                curation.get("results_emptied", 0),
                curation.get("messages_dropped", 0),
            )
        else:
            logger.info("Context from memory: %d messages, no pruning.", len(messages))

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
        return bool(response.json().get("buckets_removed", 0))

    async def clear(self, *, scope: str | None = None) -> None:
        """Empties a whole scope.

        Without a scope it does not happen: deleting "everything" across an
        authorization boundary is exactly the operation that boundary exists to
        prevent.
        """
        if scope is None:
            raise ValueError("clear() without a scope is not supported by the memory service")
        response = await self._client.delete("/scope", headers=self._headers(scope))
        response.raise_for_status()

    async def aclose(self) -> None:
        await self._client.aclose()
