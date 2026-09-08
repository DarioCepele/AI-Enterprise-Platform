"""Collection of application logs for the frontend's LOG tab.

It does not touch AG-UI: the protocol's CUSTOM events are reserved to the
framework (usage, oauth_consent_request, function_approval_request,
PredictState) and there is no Content factory producing an arbitrary one. The
logs therefore travel over an HTTP endpoint of their own, which reads this
cursor-based collector.
"""
from __future__ import annotations

import logging
import threading
from collections import deque
from datetime import datetime, timezone
from typing import Any

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

    The cursor is the sequence number of the last line seen. Reading again from
    the same cursor returns the same lines: a client retrying after a network
    error loses nothing.
    """

    def __init__(self) -> None:
        self._entries: deque[dict[str, Any]] = deque(maxlen=MAX_LOG_EVENTS)
        self._handler: _CollectingHandler | None = None
        self._previous_level: int = logging.NOTSET
        self._next_seq = 1

        self._lock = threading.Lock()

    def append(self, entry: dict[str, Any]) -> None:
        with self._lock:
            entry["seq"] = self._next_seq
            self._next_seq += 1
            self._entries.append(entry)

    def attach(self) -> None:
        """Attaches the collector to the `demo` logger. Calling it twice does not duplicate."""
        if self._handler is not None:
            return
        self._handler = _CollectingHandler(self)
        self._handler.setFormatter(logging.Formatter())
        logger = logging.getLogger(APP_LOGGER)

        self._previous_level = logger.level
        logger.addHandler(self._handler)

        logger.setLevel(logging.INFO)

    def detach(self) -> None:
        if self._handler is None:
            return
        logger = logging.getLogger(APP_LOGGER)
        logger.removeHandler(self._handler)
        logger.setLevel(self._previous_level)
        self._handler = None

    def since(self, cursor: int) -> dict[str, Any]:
        """The lines with seq > cursor, the new cursor, and how many were lost."""
        with self._lock:
            entries = [dict(e) for e in self._entries if e["seq"] > cursor]
            oldest_kept = self._entries[0]["seq"] if self._entries else self._next_seq

            dropped = max(0, oldest_kept - cursor - 1)
            newest = entries[-1]["seq"] if entries else cursor
            return {"entries": entries, "cursor": newest, "dropped": dropped}

    def __enter__(self) -> LogCollector:
        self.attach()
        return self

    def __exit__(self, *exc: object) -> None:
        self.detach()
