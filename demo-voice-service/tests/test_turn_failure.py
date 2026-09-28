"""A turn the master agent does not answer is reported, not left silent.

The transcript reaches the browser; if the reply then fails (the agent is
down, the run errors), the browser is told with `assistant_turn_failed` --
without the cause, which stays in the service's logs. Silence would read as
"still thinking". The session itself stays open for the next turn.

Real speech in, like `test_barge_in.py` (Silero VAD and faster-whisper run on
the fixture): only the bridge to the agent is replaced, by one that fails.
"""

from __future__ import annotations

import wave
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from pathlib import Path

import httpx
from starlette.testclient import TestClient

from voice_service.agui_client import AGUIBridgeClient
from voice_service.api import create_app
from voice_service.config import Settings

SPEECH_FIXTURE = Path(__file__).parent / "fixtures" / "speech_en.wav"
CHUNK_BYTES = 3_200  # 100ms of 16kHz mono PCM16, as in the other ws tests
RECEIVE_TIMEOUT_S = 90  # the first call loads Silero and faster-whisper


def _speech() -> bytes:
    with wave.open(str(SPEECH_FIXTURE), "rb") as wav_file:
        return wav_file.readframes(wav_file.getnframes())


def _receive(websocket) -> dict:
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(websocket.receive_json)
        try:
            return future.result(timeout=RECEIVE_TIMEOUT_S)
        except FutureTimeoutError as err:
            raise AssertionError(f"nothing received in {RECEIVE_TIMEOUT_S}s") from err


def test_a_turn_the_agent_does_not_answer_is_reported(monkeypatch):
    async def unreachable(self, text):
        raise httpx.ConnectError("master agent unreachable")
        yield  # an async generator, like the real one

    monkeypatch.setattr(AGUIBridgeClient, "stream_turn", unreachable)
    app = create_app(Settings(master_agent_url="http://127.0.0.1:9"))
    audio = _speech()

    with TestClient(app).websocket_connect("/ws/voice") as websocket:
        for offset in range(0, len(audio), CHUNK_BYTES):
            websocket.send_bytes(audio[offset : offset + CHUNK_BYTES])

        assert _receive(websocket)["type"] == "user_transcript"
        failed = _receive(websocket)

    assert failed == {"type": "assistant_turn_failed"}
