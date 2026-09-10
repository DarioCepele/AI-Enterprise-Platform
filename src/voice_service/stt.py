"""Speech-to-text: turn an audio file into text.

Uses faster-whisper's `small` model on CPU with `int8` compute -- no GPU
required, matching the scaffold's no-GPU-by-default posture (see
`demo-voice-service`'s README). The model is downloaded from Hugging Face on
first use and cached under the user's home directory (faster-whisper's
default `download_root`); after that first run it loads from local disk.

This module only answers "what did this audio file say?". It is deliberately
not wired to a WebSocket, Pipecat, or the master-agent yet -- that is the
next contract's job.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

MODEL_SIZE = "small"
COMPUTE_TYPE = "int8"
DEVICE = "cpu"


@lru_cache(maxsize=1)
def _model():
    """Loads the faster-whisper model once and reuses it for the process lifetime."""
    from faster_whisper import WhisperModel

    return WhisperModel(MODEL_SIZE, device=DEVICE, compute_type=COMPUTE_TYPE)


def transcribe(audio_path: str | Path, language: str | None = None) -> str:
    """Transcribes an audio file to text.

    `audio_path` can be any format faster-whisper's bundled decoder (PyAV)
    reads -- wav, mp3, etc. -- resampling to 16kHz happens internally.
    `language` is an ISO 639-1 code (e.g. "en"); pass it when known to skip
    language auto-detection, or leave it `None` to let Whisper detect it.
    """
    segments, _info = _model().transcribe(str(audio_path), language=language)
    return "".join(segment.text for segment in segments).strip()
