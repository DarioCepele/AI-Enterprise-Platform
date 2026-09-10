"""VAD and STT, exercised in isolation -- no WebSocket, no Pipecat, no
master-agent involved (that wiring is a later contract).

`speech_en.wav` is a synthetic fixture: the sentence "The quick brown fox
jumps over the lazy dog." spoken by the Windows SAPI text-to-speech engine
(`System.Speech.Synthesis`), rendered offline as 16kHz mono 16-bit PCM. Its
transcript is therefore known in advance, which is what makes the STT
assertion below meaningful rather than a smoke test.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from voice_service.stt import transcribe
from voice_service.vad import contains_speech

FIXTURES = Path(__file__).parent / "fixtures"
SPEECH_FIXTURE = FIXTURES / "speech_en.wav"
EXPECTED_PHRASE = "quick brown fox"


def _load_pcm16_mono(path: Path) -> np.ndarray:
    """Reads a 16-bit PCM wav into a float32 array in [-1, 1], no extra deps."""
    import wave

    with wave.open(str(path), "rb") as wav_file:
        assert wav_file.getsampwidth() == 2, "fixture must be 16-bit PCM"
        assert wav_file.getnchannels() == 1, "fixture must be mono"
        raw = wav_file.readframes(wav_file.getnframes())

    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    return samples


def test_speech_fixture_is_a_few_seconds_long():
    """Sanity check on the fixture itself, so a bad recording fails loudly."""
    audio = _load_pcm16_mono(SPEECH_FIXTURE)
    duration_s = len(audio) / 16_000
    assert 1.0 < duration_s < 15.0


def test_vad_detects_speech_in_the_speech_fixture():
    audio = _load_pcm16_mono(SPEECH_FIXTURE)
    assert contains_speech(audio, sample_rate=16_000) is True


def test_vad_detects_no_speech_in_silence():
    silence = np.zeros(16_000 * 2, dtype=np.float32)  # 2s of digital silence
    assert contains_speech(silence, sample_rate=16_000) is False


def test_vad_detects_no_speech_in_quiet_noise():
    # Quasi-silent: tiny amplitude noise, well below anything Silero should
    # classify as a voice.
    rng = np.random.default_rng(seed=0)
    quiet_noise = (rng.standard_normal(16_000 * 2) * 0.001).astype(np.float32)
    assert contains_speech(quiet_noise, sample_rate=16_000) is False


def test_transcribe_recovers_the_known_phrase():
    text = transcribe(SPEECH_FIXTURE, language="en")
    assert EXPECTED_PHRASE in text.lower()
