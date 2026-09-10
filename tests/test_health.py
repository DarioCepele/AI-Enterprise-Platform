"""Two questions, two answers: is the process alive, and can it serve."""
from __future__ import annotations

import httpx
import pytest

from memory_service.api import create_app
from memory_service.service import ThreadMemory

from conftest import needs_backends

pytestmark = [needs_backends, pytest.mark.integration]


class Unreachable:
    async def ping(self) -> None:
        raise ConnectionError("postgres unreachable")


async def client_for(memory: ThreadMemory) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=create_app(memory=memory))
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def test_liveness_answers_without_touching_a_database():
    memory = ThreadMemory(Unreachable())

    async with await client_for(memory) as client:
        alive = await client.get("/health/live")

    # Liveness says "this process is not stuck". A probe that also asked the
    # database would restart a healthy process because a database went away.
    assert alive.status_code == 200
    assert alive.json()["status"] == "alive"


async def test_readiness_fails_when_the_durable_store_is_gone():
    memory = ThreadMemory(Unreachable())

    async with await client_for(memory) as client:
        ready = await client.get("/health/ready")

    # Without Postgres there is nowhere to write: taking traffic would mean
    # losing conversations quietly.
    assert ready.status_code == 503


async def test_readiness_is_green_with_the_database(transcripts):
    memory = ThreadMemory(transcripts)

    async with await client_for(memory) as client:
        ready = await client.get("/health/ready")

    assert ready.status_code == 200
    assert ready.json() == {"status": "ok", "durable": "ok"}


async def test_the_old_health_path_still_answers(transcripts):
    memory = ThreadMemory(transcripts)

    async with await client_for(memory) as client:
        health = await client.get("/health")

    assert health.status_code == 200
