"""Step 6 of the plan's Tappa 1: voice and text are the same run.

Proves that the same question, asked (a) through the voice pipeline
(`/ws/voice`, a fixed audio file standing in for a microphone) and (b) as
text sent directly to `demo-master-agent`'s AG-UI endpoint
(`voice_service.agui_client.AGUIBridgeClient.send_turn`, bypassing the voice
pipeline entirely), produces the *exact same* assistant reply string -- not
just "both answered something", but character-for-character identical text
coming out of both paths against one real, running `demo-master-agent`
subprocess.

Reuses the exact subprocess/fake-client pattern `tests/test_agui_bridge.py`
established (its docstring explains the choices in full): `demo-master-agent`
started from its own synced `.venv`, `DEMO_FAKE_CLIENT=true` so the reply is
deterministic (`FakeStreamingChatClient`'s default chunks, joined:
"I am working on the answer." -- see that module's `EXPECTED_REPLY`), no
Postgres/memory-service/knowledge-agent/subagents wired in. This module does
not duplicate those helpers/fixture -- it imports them, since both test
modules live in the same `tests` package on `pythonpath`
(`pyproject.toml`'s `pythonpath = ["src", "tests"]` puts `tests/` itself on
`sys.path`, so `import test_agui_bridge` resolves as a plain top-level
module, the same as `voice_service` does for `src/`).

Why the exact same transcript text is used for both paths: path (a) transcribes
the fixture audio for real (faster-whisper, same as `tests/test_stt.py` and
`tests/test_pipeline.py`); path (b) sends that literal transcribed string,
not a hand-typed guess at what the audio says. That way the two paths differ
only in "audio pipeline vs. direct text call", never in wording -- if they
still produced different replies, that would point at a real divergence
between the two paths (e.g. the bridge mangling the transcript before
sending it), not a difference in what was asked. With the fake chat client
the reply text does not actually depend on the input (see
`test_agui_bridge.py`'s own second-turn check), so this test's real
assertion is that both paths reach the same `demo-master-agent` run
mechanism and reproduce its output byte-for-byte -- no divergent formatting,
whitespace, or truncation introduced by either path.
"""
from __future__ import annotations

import asyncio
import wave
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from pathlib import Path

from starlette.testclient import TestClient

from voice_service.agui_client import AGUIBridgeClient
from voice_service.api import create_app
from voice_service.config import Settings

# Re-exported as a fixture by importing it: pytest resolves fixtures by name
# in the requesting module's namespace, so this makes `master_agent_url`
# available to the tests below exactly as it is in `test_agui_bridge.py`,
# with no copy of the subprocess-management code.
from test_agui_bridge import master_agent_url  # noqa: F401

FIXTURES = Path(__file__).parent / "fixtures"
SPEECH_FIXTURE = FIXTURES / "speech_en.wav"

CHUNK_BYTES = 3_200  # 100ms of 16kHz mono 16-bit PCM -- same slice size as test_pipeline.py
RECEIVE_TIMEOUT_S = 90  # generous: first call loads Silero + faster-whisper on CPU


def _load_pcm16_bytes(path: Path) -> bytes:
    """Reads a 16-bit PCM wav's raw frames, no resampling (fixture is already 16kHz mono)."""
    with wave.open(str(path), "rb") as wav_file:
        assert wav_file.getsampwidth() == 2, "fixture must be 16-bit PCM"
        assert wav_file.getnchannels() == 1, "fixture must be mono"
        assert wav_file.getframerate() == 16_000, "fixture must be 16kHz"
        return wav_file.readframes(wav_file.getnframes())


def _recv_json_with_timeout(websocket, timeout: float = RECEIVE_TIMEOUT_S) -> dict:
    """`receive_json()` has no timeout of its own -- run it off-thread so a pipeline
    bug (no message ever sent) fails the test instead of hanging it forever."""
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(websocket.receive_json)
        try:
            return future.result(timeout=timeout)
        except FutureTimeoutError:
            raise AssertionError(f"no message received within {timeout}s")


def test_voice_and_text_paths_produce_the_identical_assistant_reply(master_agent_url):
    """Path (a): the fixture audio through `/ws/voice`. Path (b): its transcript,
    sent as plain text straight to `demo-master-agent`'s AG-UI endpoint. Both
    must produce the exact same reply string.
    """
    audio = _load_pcm16_bytes(SPEECH_FIXTURE)

    # Point this service's own bridge at the *same* running demo-master-agent
    # subprocess the fixture just started (its URL is on a fixture-assigned
    # free port, not the production default `Settings()` would otherwise use).
    settings = Settings(master_agent_url=master_agent_url)
    app = create_app(settings)
    client = TestClient(app)

    with client.websocket_connect("/ws/voice") as websocket:
        for offset in range(0, len(audio), CHUNK_BYTES):
            websocket.send_bytes(audio[offset : offset + CHUNK_BYTES])

        transcript_message = _recv_json_with_timeout(websocket)
        assert transcript_message["type"] == "user_transcript"
        transcript_text = transcript_message["text"]
        assert transcript_text.strip(), "voice path produced an empty transcript"

        text_chunk_message = _recv_json_with_timeout(websocket)
        assert text_chunk_message["type"] == "assistant_text_chunk"
        voice_reply = text_chunk_message["text"]
        assert voice_reply, "voice path produced an empty assistant reply"

    # Path (b): the same words, as text, on their own separate AG-UI thread --
    # no audio, no VAD, no STT, no TTS, nothing shared with path (a) except
    # this literal string and the demo-master-agent subprocess answering both.
    bridge = AGUIBridgeClient(master_agent_url)
    text_reply = asyncio.run(bridge.send_turn(transcript_text))
    assert text_reply, "text path produced an empty assistant reply"

    assert voice_reply == text_reply, (
        f"voice path and text path diverged: voice={voice_reply!r} text={text_reply!r}"
    )
