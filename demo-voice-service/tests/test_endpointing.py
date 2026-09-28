"""Endpointing thresholds are configuration, and their known limit is documented.

`voice_service.pipeline.ANALYSIS_WINDOW_S` (0.32s) and `SILENCE_THRESHOLD_S`
(0.6s) used to be hardcoded. They are now `voice_service.config.Settings`
fields (`analysis_window_s`/`silence_threshold_s`, read from
`VOICE_ANALYSIS_WINDOW_S`/`VOICE_SILENCE_THRESHOLD_S`), forwarded by
`voice_service.api` into `voice_service.pipeline.build_voice_pipeline` --
with the *same* defaults as before, so nothing about existing behaviour
changes unless the environment says otherwise.

This module has two kinds of test:

- Settings/pipeline wiring: the new fields exist, keep the old defaults, read
  overrides from the environment, and actually reach
  `TurnEndpointingProcessor` through `build_voice_pipeline`. Fast -- no
  audio, no model loading.
- The known limitation, named ahead of time: semantic turn detection on
  top of the VAD is the fix the literature knows, and it is out of scope
  here. `speech_en_midpause.wav` is built the same way `speech_en.wav` was
  (`tests/test_stt.py`'s docstring: Windows SAPI, `System.Speech.Synthesis`,
  offline, rendered as 16kHz mono 16-bit PCM) -- via a `PromptBuilder` with
  an explicit ~900ms `AppendBreak` in the middle of what a person would call
  one utterance ("The quick brown fox jumps over the lazy dog." <pause>
  "Then it runs into the forest to hide from the hunter."), longer than the
  default 0.6s `SILENCE_THRESHOLD_S`. Feeding it through `/ws/voice` end to
  end documents -- not fixes -- that the pipeline really does cut the turn
  at that pause instead of treating it as one utterance.
"""
from __future__ import annotations

import wave
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from pathlib import Path

from starlette.testclient import TestClient

from voice_service.api import create_app
from voice_service.config import Settings
from voice_service.pipeline import (
    ANALYSIS_WINDOW_S,
    SAMPLE_RATE,
    SILENCE_THRESHOLD_S,
    TurnEndpointingProcessor,
    build_voice_pipeline,
)

FIXTURES = Path(__file__).parent / "fixtures"
MIDPAUSE_FIXTURE = FIXTURES / "speech_en_midpause.wav"

# 100ms of 16kHz mono 16-bit PCM -- same slice size as the other ws tests
CHUNK_BYTES = 3_200
RECEIVE_TIMEOUT_S = 90  # generous: first call loads Silero + faster-whisper on CPU


def _load_pcm16_bytes(path: Path) -> bytes:
    """Reads a 16-bit PCM wav's raw frames, no resampling
    (fixture is already 16kHz mono)."""
    with wave.open(str(path), "rb") as wav_file:
        assert wav_file.getsampwidth() == 2, "fixture must be 16-bit PCM"
        assert wav_file.getnchannels() == 1, "fixture must be mono"
        assert wav_file.getframerate() == 16_000, "fixture must be 16kHz"
        return wav_file.readframes(wav_file.getnframes())


def _recv_json_with_timeout(websocket, timeout: float = RECEIVE_TIMEOUT_S) -> dict:
    """`receive_json()` has no timeout of its own -- run it off-thread so a
    pipeline bug (no message ever sent) fails the test instead of hanging it."""
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(websocket.receive_json)
        try:
            return future.result(timeout=timeout)
        except FutureTimeoutError as err:
            raise AssertionError(f"no message received within {timeout}s") from err


def _next_transcript(websocket) -> dict:
    """The next `user_transcript`, past whatever the assistant said in between.

    No master agent runs here, so every turn also ends in an
    `assistant_turn_failed`: that is the reply side, and these tests are about
    where the turns are cut.
    """
    while True:
        message = _recv_json_with_timeout(websocket)
        if message["type"] == "user_transcript":
            return message


# --- Settings/pipeline wiring -------------------------------------------------


def test_settings_default_endpointing_thresholds_match_the_pipeline_module(monkeypatch):
    """No env override: `Settings()` must reproduce the exact values that used
    to be hardcoded in `pipeline.py` -- the defaults do not change, they only
    become overridable."""
    monkeypatch.delenv("VOICE_ANALYSIS_WINDOW_S", raising=False)
    monkeypatch.delenv("VOICE_SILENCE_THRESHOLD_S", raising=False)

    settings = Settings()

    assert settings.analysis_window_s == ANALYSIS_WINDOW_S == 0.32
    assert settings.silence_threshold_s == SILENCE_THRESHOLD_S == 0.6


def test_settings_read_endpointing_thresholds_from_the_environment(monkeypatch):
    monkeypatch.setenv("VOICE_ANALYSIS_WINDOW_S", "0.5")
    monkeypatch.setenv("VOICE_SILENCE_THRESHOLD_S", "1.5")

    settings = Settings()

    assert settings.analysis_window_s == 0.5
    assert settings.silence_threshold_s == 1.5


def test_build_voice_pipeline_forwards_custom_thresholds_to_the_endpointing_stage():
    """White-box: `TurnEndpointingProcessor` accepts these constructor
    arguments; this checks that `build_voice_pipeline`
    -- the function `/ws/voice` actually calls -- forwards them instead of
    silently keeping its own defaults."""
    pipeline = build_voice_pipeline(analysis_window_s=0.5, silence_threshold_s=1.5)

    endpointing_stage = pipeline.processors[1]
    assert isinstance(endpointing_stage, TurnEndpointingProcessor)
    assert endpointing_stage._silence_threshold_s == 1.5
    assert endpointing_stage._analysis_window_samples == int(0.5 * SAMPLE_RATE)


def test_build_voice_pipeline_keeps_old_defaults_with_no_arguments():
    """Every existing caller (including every other test in this suite) calls
    `build_voice_pipeline()` with no threshold arguments -- confirms that
    still yields the documented defaults."""
    pipeline = build_voice_pipeline()

    endpointing_stage = pipeline.processors[1]
    assert endpointing_stage._silence_threshold_s == SILENCE_THRESHOLD_S == 0.6
    assert endpointing_stage._analysis_window_samples == int(
        ANALYSIS_WINDOW_S * SAMPLE_RATE
    )


# --- The known limitation: silence-only endpointing cuts a natural
#     mid-sentence pause ---


def test_a_midsentence_pause_longer_than_the_silence_threshold_splits_the_turn_in_two():
    """Documents, does not fix (see the module docstring -- semantic turn
    detection is out of scope here): with the default 0.6s
    `SILENCE_THRESHOLD_S`, a single spoken
    passage with a ~900ms pause in the middle is NOT treated as one turn --
    it is cut at the pause, exactly as a real trailing silence would be, and
    the second half surfaces as its own, independent second turn once its
    own trailing silence closes it.
    """
    audio = _load_pcm16_bytes(MIDPAUSE_FIXTURE)
    app = create_app()
    client = TestClient(app)

    with client.websocket_connect("/ws/voice") as websocket:
        for offset in range(0, len(audio), CHUNK_BYTES):
            websocket.send_bytes(audio[offset : offset + CHUNK_BYTES])

        first_turn = _next_transcript(websocket)
        second_turn = _next_transcript(websocket)

    assert first_turn["type"] == "user_transcript"
    assert second_turn["type"] == "user_transcript"

    # The pause cut the turn early: the first turn's transcript is only the
    # first phrase -- the second phrase's words are not in it.
    first_text = first_turn["text"].lower()
    assert "quick brown fox" in first_text
    assert "forest" not in first_text

    # ...and the second phrase surfaces as its own, separate turn instead of
    # being joined back with the first.
    assert "forest" in second_turn["text"].lower()
