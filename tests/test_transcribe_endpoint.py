"""`POST /transcribe`: one whole audio file in, one transcript out.

Independent of `/ws/voice` -- no WebSocket, no VAD, no turn-taking, no
Pipecat pipeline. Reuses the same fixture and expected phrase as
`tests/test_stt.py` because this route is a thin HTTP wrapper around
`voice_service.stt.transcribe`, not a second STT engine.
"""
from __future__ import annotations

from pathlib import Path

import httpx

from voice_service.api import create_app

FIXTURES = Path(__file__).parent / "fixtures"
SPEECH_FIXTURE = FIXTURES / "speech_en.wav"
EXPECTED_PHRASE = "quick brown fox"


async def client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=create_app())
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def test_transcribe_endpoint_recovers_the_known_phrase():
    audio_bytes = SPEECH_FIXTURE.read_bytes()

    async with await client() as http:
        response = await http.post(
            "/transcribe",
            files={"file": ("speech_en.wav", audio_bytes, "audio/wav")},
        )

    assert response.status_code == 200
    assert EXPECTED_PHRASE in response.json()["text"].lower()


async def test_transcribe_endpoint_rejects_a_non_multipart_body():
    async with await client() as http:
        response = await http.post(
            "/transcribe",
            content=b"not multipart data",
            headers={"content-type": "application/octet-stream"},
        )

    assert response.status_code == 422
