"""How the edges behave when what is on the other side is not well."""
from __future__ import annotations

import logging

import httpx
import pytest

from demo.resilience import Breaker, with_retries


class Flaky:
    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.calls = 0

    async def __call__(self) -> str:
        self.calls += 1
        if self.calls <= self.failures:
            raise ConnectionError("not yet")
        return "answered"


@pytest.mark.asyncio
async def test_a_call_that_recovers_is_retried():
    flaky = Flaky(failures=2)

    assert await with_retries(flaky, attempts=3, backoff=0) == "answered"
    assert flaky.calls == 3


@pytest.mark.asyncio
async def test_after_the_last_attempt_the_error_comes_back():
    flaky = Flaky(failures=5)

    with pytest.raises(ConnectionError):
        await with_retries(flaky, attempts=3, backoff=0)

    assert flaky.calls == 3


# The breaker raises what the call raised until it opens, and BreakerOpen after:
# the tests below wait for `Exception` because both are the expected outcome.
@pytest.mark.asyncio
async def test_the_breaker_stops_calling_after_enough_failures(caplog):
    breaker = Breaker(
        name="memory", threshold=2, cooldown_seconds=60, now=lambda: 1000.0
    )
    flaky = Flaky(failures=99)

    with caplog.at_level(logging.WARNING, logger="demo.resilience"):
        for _ in range(4):
            with pytest.raises(Exception):  # noqa: B017
                await breaker.call(flaky)

    # Two failures open it; what comes after is refused without a round trip,
    # and the log says it once instead of on every call.
    assert flaky.calls == 2
    assert caplog.text.count("stops calling") == 1


@pytest.mark.asyncio
async def test_the_breaker_tries_again_after_the_cooldown():
    now = [1000.0]
    breaker = Breaker(
        name="memory", threshold=1, cooldown_seconds=30, now=lambda: now[0]
    )
    flaky = Flaky(failures=1)

    with pytest.raises(Exception):  # noqa: B017
        await breaker.call(flaky)
    now[0] += 31

    assert await breaker.call(flaky) == "answered"


@pytest.mark.asyncio
async def test_the_breaker_reopens_if_the_probe_after_cooldown_fails_again():
    now = [1000.0]
    breaker = Breaker(
        name="memory", threshold=1, cooldown_seconds=30, now=lambda: now[0]
    )
    flaky = Flaky(failures=99)

    with pytest.raises(Exception):  # noqa: B017
        await breaker.call(flaky)
    now[0] += 31

    # The probe fails again: the breaker must reopen right away instead of
    # waiting for a fresh cooldown window measured from the stale timestamp.
    with pytest.raises(Exception):  # noqa: B017
        await breaker.call(flaky)

    assert breaker.is_open is True


@pytest.mark.asyncio
async def test_a_success_closes_the_breaker_again():
    breaker = Breaker(
        name="memory", threshold=2, cooldown_seconds=0, now=lambda: 1000.0
    )
    flaky = Flaky(failures=1)

    with pytest.raises(Exception):  # noqa: B017
        await breaker.call(flaky)
    assert await breaker.call(flaky) == "answered"

    flaky.failures = 99
    with pytest.raises(Exception):  # noqa: B017
        await breaker.call(flaky)

    # The count starts over on success: an old failure plus a new one is not
    # the same thing as two in a row.
    assert flaky.calls == 3


@pytest.mark.asyncio
async def test_the_memory_store_retries_a_failing_read(monkeypatch):
    from demo.memory.remote_store import MemoryServiceSnapshotStore

    attempts = []

    def answer(request: httpx.Request) -> httpx.Response:
        attempts.append(request)
        if len(attempts) < 2:
            raise httpx.ConnectError("down")
        return httpx.Response(200, json={"messages": [], "state": None})

    client = httpx.AsyncClient(transport=httpx.MockTransport(answer), base_url="http://memory")
    store = MemoryServiceSnapshotStore("http://memory", client=client, backoff=0)

    snapshot = await store.get(scope="tenant-a", thread_id="t1")

    assert snapshot is not None
    assert len(attempts) == 2
