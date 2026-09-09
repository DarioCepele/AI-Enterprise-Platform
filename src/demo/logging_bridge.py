"""Collection of application logs for the frontend's LOG tab.

It does not touch AG-UI: the protocol's CUSTOM events are reserved to the
framework (usage, oauth_consent_request, function_approval_request,
PredictState) and there is no Content factory producing an arbitrary one. The
logs therefore travel over an HTTP endpoint of their own, which reads this
cursor-based collector.

The collector is per process. With more than one replica that is half a story,
and which half depends on who answered the request: `RedisLogStream` publishes
the same lines to a shared stream, and the endpoint reads from there when it is
configured. The local buffer stays as the fallback -- a replica that cannot
reach Redis still shows its own lines.
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

LOG_STREAM_KEY = "logs:stream"
LOG_SEQUENCE_KEY = "logs:seq"

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

class RedisLogStream:
    """The same lines, in a stream every replica writes to and reads from.

    Writing happens off the logging call: `emit` runs wherever the log line was
    produced -- including threads without an event loop -- so it only buffers,
    and a drain task publishes in batches. A log line is not worth blocking the
    code that emitted it.

    Reading flushes first, so a replica always sees what it has just logged:
    without it the LOG tab would trail the drain interval, and a line produced
    while answering the very request that asks for it would arrive next time.
    """

    def __init__(
        self,
        redis: Any,
        key: str = LOG_STREAM_KEY,
        sequence_key: str = LOG_SEQUENCE_KEY,
        maxlen: int = MAX_LOG_EVENTS,
        flush_seconds: float = 0.2,
    ) -> None:
        self._redis = redis
        self._key = key
        self._sequence_key = sequence_key
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
            last = int(await self._redis.incrby(self._sequence_key, len(batch)))
            pipe = self._redis.pipeline()
            for offset, entry in enumerate(batch):
                pipe.xadd(
                    self._key,
                    {
                        "seq": last - len(batch) + 1 + offset,
                        "ts": str(entry.get("ts", "")),
                        "level": str(entry.get("level", "")),
                        "source": str(entry.get("source", "")),
                        "message": str(entry.get("message", "")),
                    },
                    maxlen=self._maxlen,
                    approximate=False,
                )
            await pipe.execute()
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
        await self.flush()
        last_id, seen = _split_cursor(cursor)
        start = f"({last_id}" if last_id else "-"
        try:
            rows = await self._redis.xrange(self._key, min=start, max="+", count=self._maxlen)
            oldest = await self._redis.xrange(self._key, count=1)
        except Exception:
            logger.warning("Shared logs unreadable: falling back to this replica.", exc_info=True)
            raise

        entries = [_entry_of(row) for row in rows]
        dropped = 0
        if seen >= 0 and oldest:
            oldest_seq = int(oldest[0][1]["seq"])
            dropped = max(0, oldest_seq - seen - 1)

        newest = rows[-1] if rows else None
        return {
            "entries": entries,
            "cursor": f"{newest[0]}|{entries[-1]['seq']}" if newest else cursor,
            "dropped": dropped,
        }


def _split_cursor(cursor: str) -> tuple[str, int]:
    if not cursor:
        return "", -1
    stream_id, _, seq = cursor.partition("|")
    return stream_id, int(seq) if seq.isdigit() else -1


def _entry_of(row: tuple[str, dict[str, str]]) -> dict[str, Any]:
    _, fields = row
    return {
        "seq": int(fields.get("seq", 0)),
        "ts": fields.get("ts", ""),
        "level": fields.get("level", ""),
        "source": fields.get("source", ""),
        "message": fields.get("message", ""),
    }
