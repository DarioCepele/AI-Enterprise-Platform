"""Kokoro TTS, exercised in isolation -- no WebSocket, no Pipecat, no
`agui_client` involved (same principle `tests/test_stt.py` already uses for
STT: test the synthesis function directly).

Kokoro is not deterministic: two `synthesize()` calls with the same text and
voice were measured (outside this test, while writing it) to produce
different audio sample-by-sample -- `np.array_equal` on two runs of the
fixed sentence below returned `False`, with a max absolute sample
difference around 0.09 (samples are floats in [-1, 1] before quantizing to
int16). So this module does not assert exact bytes, only that the output is
non-empty and its duration is plausible for the input text -- the same
choice this contract's instructions call for.
"""
from __future__ import annotations

from voice_service.tts import SAMPLE_RATE, synthesize

FIXED_TEXT = "The quick brown fox jumps over the lazy dog."

# Kokoro speaks at a natural pace -- roughly 2.5-3.5 seconds for this
# nine-word sentence was observed while writing this test. The bounds below
# are deliberately wide (a plausibility check, not a timing regression test)
# so ordinary machine-to-machine speed variance never flakes this test.
MIN_PLAUSIBLE_DURATION_S = 1.0
MAX_PLAUSIBLE_DURATION_S = 15.0


def test_synthesize_produces_nonempty_audio():
    audio_bytes = synthesize(FIXED_TEXT)
    assert isinstance(audio_bytes, bytes)
    assert len(audio_bytes) > 0


def test_synthesize_produces_a_plausible_duration():
    audio_bytes = synthesize(FIXED_TEXT)
    # 16-bit PCM mono: 2 bytes per sample.
    sample_count = len(audio_bytes) // 2
    duration_s = sample_count / SAMPLE_RATE
    assert MIN_PLAUSIBLE_DURATION_S < duration_s < MAX_PLAUSIBLE_DURATION_S


def test_synthesize_is_not_byte_identical_across_calls():
    """Documents Kokoro's non-determinism (see module docstring) so an
    assumption of exact reproducibility is never silently reintroduced here.
    """
    first = synthesize(FIXED_TEXT)
    second = synthesize(FIXED_TEXT)
    assert first != second


def test_synthesize_of_empty_text_is_empty_audio():
    assert synthesize("") == b""
    assert synthesize("   ") == b""
