"""Barge-in over `/ws/voice`: talking over the assistant stops its reply.

The scenario: the user starts a second turn (talks over the assistant)
before the first turn's reply has finished being sent -- text and audio
both. `voice_service.api`'s `/ws/voice` handler tracks which turn is
`current_turn` (a counter bumped by every `TranscriptionFrame`) and, before
sending each `assistant_text_chunk`/`assistant_audio_chunk`, checks whether
its own turn is still the current one; once superseded it stops sending
anything further for that turn and emits `{"type":
"assistant_turn_cancelled"}` once.

Why the race needs a helping hand: `demo-master-agent`'s `FakeStreamingChatClient`
(the offline client `MASTER_FAKE_CLIENT=true` selects -- see
`test_agui_bridge.py`'s docstring) defaults to `delay=0.0` between chunks,
and its default chunks ("I am ", "working ", "on the ", "answer.") only
contain one sentence-ending period, at the very end -- so
`AGUIBridgeClient.stream_turn` yields exactly *one* sentence for the whole
reply, and it can arrive fast enough that there is no real wall-clock window
for a second turn's genuine VAD+STT to land inside. Rather than lean on
uncontrolled timing (flaky depending on how fast a CPU happens to run
Whisper), `delayed_stream_turn` below wraps `AGUIBridgeClient.stream_turn`
with a deliberate `asyncio.sleep` before it yields its sentence -- a
generous, reproducible stand-in for the decoupling the service relies on
(TTS/AG-UI I/O happen off the event loop, per `voice_service.api`'s own
docstring, which is what lets a second turn's audio keep being processed
while a first turn's reply is still being generated). This does not touch
`demo-master-agent` -- only this test's own monkeypatch of
`voice_service.agui_client.AGUIBridgeClient`.
"""
from __future__ import annotations

import asyncio
import wave
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from voice_service.agui_client import AGUIBridgeClient
from voice_service.api import create_app
from voice_service.config import Settings

FIXTURES = Path(__file__).parent / "fixtures"
SPEECH_FIXTURE = FIXTURES / "speech_en.wav"

# 100ms of 16kHz mono 16-bit PCM -- same slice size as the other ws tests
CHUNK_BYTES = 3_200
RECEIVE_TIMEOUT_S = 90  # generous: first call loads Silero + faster-whisper on CPU
STREAM_DELAY_S = 4.0  # a wide, deliberate window -- see module docstring
# generous ceiling so a stuck test fails fast instead of hanging forever
MAX_MESSAGES = 12


def _load_pcm16_bytes(path: Path) -> bytes:
    """Reads a 16-bit PCM wav's raw frames, no resampling
    (fixture is already 16kHz mono)."""
    with wave.open(str(path), "rb") as wav_file:
        assert wav_file.getsampwidth() == 2, "fixture must be 16-bit PCM"
        assert wav_file.getnchannels() == 1, "fixture must be mono"
        assert wav_file.getframerate() == 16_000, "fixture must be 16kHz"
        return wav_file.readframes(wav_file.getnframes())


def _send_audio(websocket, audio: bytes) -> None:
    for offset in range(0, len(audio), CHUNK_BYTES):
        websocket.send_bytes(audio[offset : offset + CHUNK_BYTES])


def _recv_json_with_timeout(websocket, timeout: float = RECEIVE_TIMEOUT_S) -> dict:
    """`receive_json()` has no timeout of its own -- run it off-thread so a
    pipeline bug (no message ever sent) fails the test instead of hanging it."""
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(websocket.receive_json)
        try:
            return future.result(timeout=timeout)
        except FutureTimeoutError as err:
            raise AssertionError(f"no message received within {timeout}s") from err


@pytest.fixture
def delayed_stream_turn(monkeypatch):
    """Wraps `AGUIBridgeClient.stream_turn` with `STREAM_DELAY_S` of sleep
    before it yields each sentence -- see the module docstring for why this
    stands in for uncontrolled real-world timing instead of relying on it.
    """
    original_stream_turn = AGUIBridgeClient.stream_turn

    async def _delayed(self, text):
        async for sentence in original_stream_turn(self, text):
            await asyncio.sleep(STREAM_DELAY_S)
            yield sentence

    monkeypatch.setattr(AGUIBridgeClient, "stream_turn", _delayed)


def test_a_second_turn_cancels_the_first_turns_unsent_reply(
    master_agent_url,
    delayed_stream_turn,
):
    """Turn 1's audio is sent and transcribed; before its (deliberately
    delayed) reply is ready to send, turn 2's audio is sent, transcribed,
    and becomes `current_turn`. Turn 1 must then stop -- no
    `assistant_text_chunk`/`assistant_audio_chunk` of its own reply may
    arrive once turn 2's transcript has -- and an `assistant_turn_cancelled`
    must be sent for it. Turn 2 itself must still get its own reply: barge-in
    stops the superseded turn, it does not wedge the connection.
    """
    audio = _load_pcm16_bytes(SPEECH_FIXTURE)

    settings = Settings(master_agent_url=master_agent_url)
    app = create_app(settings)
    client = TestClient(app)

    with client.websocket_connect("/ws/voice") as websocket:
        _send_audio(websocket, audio)
        first_transcript = _recv_json_with_timeout(websocket)
        assert first_transcript["type"] == "user_transcript"

        # Barge in: the second turn's audio arrives right away, while turn
        # 1's reply is still (deliberately) being generated.
        _send_audio(websocket, audio)

        second_transcript_seen = False
        turn_cancelled_seen = False
        text_chunks_after_second_transcript = 0
        audio_chunks_after_second_transcript = 0
        seen_types: list[str] = []

        for _ in range(MAX_MESSAGES):
            message = _recv_json_with_timeout(websocket)
            seen_types.append(message["type"])

            if message["type"] == "user_transcript":
                assert not second_transcript_seen, (
                    f"a third user_transcript arrived -- unexpected; got {seen_types}"
                )
                second_transcript_seen = True
            elif message["type"] == "assistant_turn_cancelled":
                turn_cancelled_seen = True
            elif message["type"] == "assistant_text_chunk":
                if second_transcript_seen:
                    text_chunks_after_second_transcript += 1
            elif message["type"] == "assistant_audio_chunk":
                if second_transcript_seen:
                    audio_chunks_after_second_transcript += 1
                websocket.receive_bytes()  # drain the binary frame that follows

            if (
                turn_cancelled_seen
                and second_transcript_seen
                and text_chunks_after_second_transcript >= 1
                and audio_chunks_after_second_transcript >= 1
            ):
                break

    assert second_transcript_seen, (
        f"never saw the second turn's transcript; got {seen_types}"
    )
    assert turn_cancelled_seen, (
        "never saw assistant_turn_cancelled for the superseded first turn; "
        f"got {seen_types}"
    )
    # Turn 1 was superseded before it ever reached a text/audio send (the
    # staleness check runs before each one, and turn 1's delayed sentence
    # only becomes available *after* turn 2 has already become current) --
    # so whatever text/audio chunk arrives after the second transcript can
    # only be turn 2's own reply, not a leftover from turn 1.
    assert text_chunks_after_second_transcript >= 1, (
        f"turn 2 never got its own assistant_text_chunk; got {seen_types}"
    )
    assert audio_chunks_after_second_transcript >= 1, (
        f"turn 2 never got its own assistant_audio_chunk; got {seen_types}"
    )
