"""The Pipecat pipeline, end to end, over a real WebSocket connection.

Sends the same `speech_en.wav` fixture `tests/test_stt.py` already uses,
but in small chunks with no gap between them removed -- one `send_bytes` per
~100ms slice, simulating a live microphone feeding `/ws/voice` rather than a
file uploaded in one shot. The VAD endpointing stage (see
`voice_service.pipeline.TurnEndpointingProcessor`) is expected to notice the
trailing silence already present in the fixture (~0.9s, per its own
recording) and end the turn before all audio has even been sent -- exactly
the "turn ends mid-stream" behaviour a live conversation needs.
"""
from __future__ import annotations

import wave
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from pathlib import Path

from starlette.testclient import TestClient

from voice_service.api import create_app

FIXTURES = Path(__file__).parent / "fixtures"
SPEECH_FIXTURE = FIXTURES / "speech_en.wav"
EXPECTED_PHRASE = "quick brown fox"

CHUNK_BYTES = 3_200  # 100ms of 16kHz mono 16-bit PCM -- a plausible mic packet size
RECEIVE_TIMEOUT_S = 90  # generous: first call loads Silero + faster-whisper on CPU


def _load_pcm16_bytes(path: Path) -> bytes:
    """Reads a 16-bit PCM wav's raw frames, no resampling (fixture is already 16kHz mono)."""
    with wave.open(str(path), "rb") as wav_file:
        assert wav_file.getsampwidth() == 2, "fixture must be 16-bit PCM"
        assert wav_file.getnchannels() == 1, "fixture must be mono"
        assert wav_file.getframerate() == 16_000, "fixture must be 16kHz"
        return wav_file.readframes(wav_file.getnframes())


def test_streamed_turn_produces_a_user_transcript():
    audio = _load_pcm16_bytes(SPEECH_FIXTURE)
    app = create_app()
    client = TestClient(app)

    with client.websocket_connect("/ws/voice") as websocket:
        for offset in range(0, len(audio), CHUNK_BYTES):
            websocket.send_bytes(audio[offset : offset + CHUNK_BYTES])

        # receive_json() has no timeout of its own; run it off-thread so a
        # pipeline bug (no message ever sent) fails the test instead of
        # hanging it.
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(websocket.receive_json)
            try:
                message = future.result(timeout=RECEIVE_TIMEOUT_S)
            except FutureTimeoutError:
                raise AssertionError(
                    f"no user_transcript message received within {RECEIVE_TIMEOUT_S}s"
                )

    assert message["type"] == "user_transcript"
    assert EXPECTED_PHRASE in message["text"].lower()
