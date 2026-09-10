"""What to do when the other side is not well.

Two pieces, and they answer different questions. Retries are for the failure
that goes away by itself -- a connection reset, a restarting pod. The breaker is
for the one that does not: when a service is down, keeping on calling it turns
every request into a timeout and fills the logs with the same line.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

DEFAULT_ATTEMPTS = 3
DEFAULT_BACKOFF = 0.2


class BreakerOpen(RuntimeError):
    """Raised instead of calling a service that has been failing."""


async def with_retries(
    call: Callable[[], Awaitable[T]],
    attempts: int = DEFAULT_ATTEMPTS,
    backoff: float = DEFAULT_BACKOFF,
) -> T:
    """Calls again, with a growing pause, and gives up saying why.

    The pause grows because retrying immediately against a service that is
    restarting is a way to be there exactly when it still cannot answer.
    """
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await call()
        except Exception as error:
            last = error
            if attempt == attempts:
                break
            await asyncio.sleep(backoff * attempt)
    raise last  # type: ignore[misc]


class Breaker:
    """Stops calling a service that keeps failing, and tries again later."""

    def __init__(
        self,
        name: str,
        threshold: int = 5,
        cooldown_seconds: float = 30.0,
        now: Callable[[], float] | None = None,
    ) -> None:
        self._name = name
        self._threshold = threshold
        self._cooldown = cooldown_seconds
        self._now = now or (lambda: asyncio.get_event_loop().time())
        self._failures = 0
        self._opened_at = 0.0

    @property
    def is_open(self) -> bool:
        if self._failures < self._threshold:
            return False
        return self._now() - self._opened_at < self._cooldown

    async def call(self, call: Callable[[], Awaitable[T]]) -> T:
        if self.is_open:
            raise BreakerOpen(f"{self._name} is not answering: not calling it for now")

        try:
            result = await call()
        except Exception:
            self._failures += 1
            if self._failures == self._threshold:
                self._opened_at = self._now()
                logger.warning(
                    "%s failed %d times in a row: stops calling it for %.0fs.",
                    self._name,
                    self._failures,
                    self._cooldown,
                )
            raise
        self._failures = 0
        return result
