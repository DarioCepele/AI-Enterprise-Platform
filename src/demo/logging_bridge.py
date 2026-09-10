"""Collection of application logs for the frontend's LOG tab.

It does not touch AG-UI: the protocol's CUSTOM events are reserved to the
framework (usage, oauth_consent_request, function_approval_request,
PredictState) and there is no Content factory producing an arbitrary one. The
logs therefore travel over an HTTP endpoint of their own, which reads this
cursor-based collector.

The collector is per process. With more than one replica that is half a story,
and which half depends on who answered the request: `SharedLogStream` writes the
same lines to a table every replica reads, and the endpoint uses it when it is
configured. The local buffer stays as the fallback -- a replica that cannot
reach the database still shows its own lines.

A word on where these lines belong. In a deployment with a collector they go to
the collector, and this endpoint is a convenience of the laboratory: it exists
so that the LOG tab has something to show without asking anybody to run Loki
first. The table is bounded on purpose.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

APP_LOGGER = "demo"

MAX_LOG_EVENTS = 500


def _timestamp(record: logging.LogRecord) -> str:
    return datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(
        timespec="milliseconds"
    )

class _CollectingHandler(logging.Handler):
    def __init__(self, collector: LogCollector) -> None:
        super().__init__(level=logging.INFO)
        self._collector = collector

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        if record.exc_info:

            message = f"{message}\n{self.formatter.formatException(record.exc_info)}"

        self._collector.append(
            {
                "ts": _timestamp(record),
                "level": record.levelname,
                "source": record.name.removeprefix(f"{APP_LOGGER}."),
                "message": message,
            }
        )

class LogCollector:
    """A ring buffer of `demo.*` logs, read by cursor.

    The cursor is opaque to whoever holds it: this one puts a sequence number in
    it, the shared stream puts an id. Reading again from the same cursor returns
    the same lines, so a client retrying after a network error loses nothing.
    """

    def __init__(self) -> None:
        self._entries: deque[dict[str, Any]] = deque(maxlen=MAX_LOG_EVENTS)
        self._handler: _CollectingHandler | None = None
        self._previous_level: int = logging.NOTSET
        self._next_seq = 1

        self._lock = threading.Lock()
        self._forward: Any = None

    def append(self, entry: dict[str, Any]) -> None:
        with self._lock:
            entry["seq"] = self._next_seq
            self._next_seq += 1
            self._entries.append(entry)
            forward = self._forward
        if forward is not None:
            forward(dict(entry))

    def forward_to(self, sink: Any) -> None:
        with self._lock:
            self._forward = sink

    def attach(self) -> None:
        """Attaches the collector to the `demo` logger. Calling it twice does not duplicate."""
        if self._handler is not None:
            return
        self._handler = _CollectingHandler(self)
        self._handler.setFormatter(logging.Formatter())
        app_logger = logging.getLogger(APP_LOGGER)

        self._previous_level = app_logger.level
        app_logger.addHandler(self._handler)

        app_logger.setLevel(logging.INFO)

    def detach(self) -> None:
        if self._handler is None:
            return
        app_logger = logging.getLogger(APP_LOGGER)
        app_logger.removeHandler(self._handler)
        app_logger.setLevel(self._previous_level)
        self._handler = None

    def since(self, cursor: str) -> dict[str, Any]:
        """The lines after `cursor`, the new cursor, and how many were lost."""
        seen = int(cursor) if cursor else 0
        with self._lock:
            entries = [dict(e) for e in self._entries if e["seq"] > seen]
            oldest_kept = self._entries[0]["seq"] if self._entries else self._next_seq

            dropped = max(0, oldest_kept - seen - 1)
            newest = entries[-1]["seq"] if entries else seen
            return {"entries": entries, "cursor": str(newest), "dropped": dropped}

    def __enter__(self) -> LogCollector:
        self.attach()
        return self

    def __exit__(self, *exc: object) -> None:
        self.detach()

class SharedLogStream:
    """The same lines, in a table every replica writes to and reads from.

    Writing happens off the logging call: `emit` runs wherever the log line was
    produced -- including threads without an event loop -- so it only buffers,
    and a drain task writes in batches. A log line is not worth blocking the
    code that emitted it.

    Reading flushes first, so a replica always sees what it has just logged:
    without it the LOG tab would trail the drain interval, and a line produced
    while answering the very request that asks for it would arrive next time.
    """

    def __init__(
        self,
        pool: Any,
        maxlen: int = MAX_LOG_EVENTS,
        flush_seconds: float = 0.2,
    ) -> None:
        self._pool = pool
        self._maxlen = maxlen
        self._flush_seconds = flush_seconds
        self._pending: deque[dict[str, Any]] = deque()

    def attach(self, collector: LogCollector) -> None:
        collector.forward_to(self._pending.append)

    async def flush(self) -> None:
        batch = [self._pending.popleft() for _ in range(len(self._pending))]
        if not batch:
            return
        try:
            await _ready(self._pool)
            async with self._pool.connection() as connection:
                async with connection.cursor() as cursor:
                    for entry in batch:
                        await cursor.execute(
                            "INSERT INTO operational_logs (ts, level, source, message) "
                            "VALUES (%s, %s, %s, %s)",
                            (
                                str(entry.get("ts", "")),
                                str(entry.get("level", "")),
                                str(entry.get("source", "")),
                                str(entry.get("message", "")),
                            ),
                        )
                # Bounded like the ring buffer it replaces: these are the recent
                # lines of a laboratory, not an audit log. Whoever needs those
                # sends them to a collector.
                await connection.execute(
                    "DELETE FROM operational_logs WHERE seq <= "
                    "(SELECT max(seq) - %s FROM operational_logs)",
                    (self._maxlen,),
                )
        except Exception:
            logger.warning(
                "%d logs not published to the shared stream: they stay in this replica.",
                len(batch),
                exc_info=True,
            )

    @asynccontextmanager
    async def running(self):
        task = asyncio.create_task(self._drain())
        try:
            yield self
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await self.flush()

    async def _drain(self) -> None:
        while True:
            await asyncio.sleep(self._flush_seconds)
            await self.flush()

    async def since(self, cursor: str) -> dict[str, Any]:
        """The lines after `cursor`, the new cursor, and how many were lost."""
        await self.flush()
        await _ready(self._pool)
        seen = int(cursor) if cursor.isdigit() else 0
        async with self._pool.connection() as connection, connection.cursor() as reader:
            await reader.execute(
                "SELECT seq, ts, level, source, message FROM operational_logs "
                "WHERE seq > %s ORDER BY seq LIMIT %s",
                (seen, self._maxlen),
            )
            rows = await reader.fetchall()
            await reader.execute("SELECT min(seq) FROM operational_logs")
            oldest = (await reader.fetchone())[0]

        entries = [
            {"seq": int(row[0]), "ts": row[1], "level": row[2], "source": row[3], "message": row[4]}
            for row in rows
        ]
        # What the reader asked for and will never see: the lines trimmed away
        # between one read and the next.
        dropped = max(0, int(oldest) - seen - 1) if oldest is not None and seen else 0
        newest = entries[-1]["seq"] if entries else seen
        return {"entries": entries, "cursor": str(newest), "dropped": dropped}


async def _ready(pool: Any) -> None:
    """Opens the pool if whoever built it has not.

    The lifespan opens it; a test that talks to the app without one would
    otherwise find a closed pool and read a fallback instead of the thing it
    means to test. `open()` on an open pool does nothing.
    """
    opener = getattr(pool, "open", None)
    if opener is not None:
        await opener()
