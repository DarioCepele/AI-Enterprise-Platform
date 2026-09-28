"""What the voice service refuses: foreign pages, oversized audio.

Fast tests: no model is loaded. A refused WebSocket is closed before the
pipeline exists, and an oversized upload is stopped before it is read.
"""

from __future__ import annotations

import httpx
import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from voice_service.api import create_app
from voice_service.config import Settings


def test_a_page_from_another_origin_cannot_open_the_microphone():
    # Browsers do not apply CORS to WebSockets: without this check any page
    # open in the same browser could talk to the agent through this endpoint.
    client = TestClient(create_app(settings=Settings()))

    with (
        pytest.raises(WebSocketDisconnect) as refused,
        client.websocket_connect(
            "/ws/voice", headers={"origin": "https://evil.example"}
        ) as socket,
    ):
        socket.receive_text()

    assert refused.value.code == 1008


def test_the_allowed_origins_are_read_from_the_environment(monkeypatch):
    monkeypatch.setenv(
        "VOICE_ALLOWED_ORIGINS", "https://app.example/, https://b.example"
    )

    assert Settings().origins() == frozenset(
        {"https://app.example", "https://b.example"}
    )


async def test_audio_over_the_ceiling_is_refused_before_it_is_read():
    app = create_app(settings=Settings(transcribe_max_bytes=1_000_000))
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        response = await http.post(
            "/transcribe",
            files={"file": ("big.wav", b"\0" * 1_200_000, "audio/wav")},
        )

    assert response.status_code == 413


async def test_an_empty_file_is_a_client_error():
    transport = httpx.ASGITransport(app=create_app(settings=Settings()))

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        response = await http.post(
            "/transcribe", files={"file": ("empty.wav", b"", "audio/wav")}
        )

    assert response.status_code == 422


def test_the_voice_is_configuration(monkeypatch):
    monkeypatch.setenv("VOICE_TTS_LANG_CODE", "i")
    monkeypatch.setenv("VOICE_TTS_VOICE", "if_sara")
    monkeypatch.setenv("VOICE_STT_LANGUAGE", "it")

    settings = Settings()

    assert (settings.tts_lang_code, settings.tts_voice, settings.stt_language) == (
        "i",
        "if_sara",
        "it",
    )
