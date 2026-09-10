"""Two questions, two answers: is the process alive, and can it serve.

This scaffold has no backend yet (no Pipecat, no STT/TTS, no store), so both
probes currently answer the same way. The test still pins the contract these
endpoints have to keep once a real dependency lands behind `/health/ready`.
"""
from __future__ import annotations

import httpx

from voice_service.api import create_app


async def client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=create_app())
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def test_liveness_answers_ok():
    async with await client() as http:
        response = await http.get("/health/live")

    assert response.status_code == 200
    assert response.json()["status"] == "alive"


async def test_readiness_answers_ok():
    async with await client() as http:
        response = await http.get("/health/ready")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_the_old_health_path_still_answers():
    async with await client() as http:
        response = await http.get("/health")

    assert response.status_code == 200
