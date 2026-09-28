"""Speech-to-text: turn an audio file into text.

Uses faster-whisper's `small` model on CPU with `int8` compute -- no GPU
required, matching the scaffold's no-GPU-by-default posture (see
`demo-voice-service`'s README). The model is downloaded from Hugging Face on
first use and cached under the user's home directory (faster-whisper's
default `download_root`); after that first run it loads from local disk.

This module only answers "what did this audio file say?": the WebSocket
pipeline and `/transcribe` both call it. The model size comes from
`VOICE_STT_MODEL`; each size is loaded once and reused.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

MODEL_SIZE = "small"
COMPUTE_TYPE = "int8"
DEVICE = "cpu"


@lru_cache(maxsize=2)
def _model(size: str = MODEL_SIZE) -> Any:
    """Loads a faster-whisper model once and reuses it for the process lifetime."""
    from faster_whisper import WhisperModel

    return WhisperModel(size, device=DEVICE, compute_type=COMPUTE_TYPE)


def transcribe(
    audio_path: str | Path, language: str | None = None, model_size: str | None = None
) -> str:
    """Transcribes an audio file to text.

    `audio_path` can be any format faster-whisper's bundled decoder (PyAV)
    reads -- wav, mp3, etc. -- resampling to 16kHz happens internally.
    `language` is an ISO 639-1 code (e.g. "en"); pass it when known to skip
    language auto-detection, or leave it `None` to let Whisper detect it.

    Not every caller's audio has speech in it: `analyze_video` transcribes
    whatever audio track a video happens to have, including a silent screen
    recording. Whisper was trained on subtitled audio, so on silence or
    near-silence it tends to hallucinate plausible-sounding filler instead of
    recognizing there is nothing to say -- `vad_filter` runs Whisper's own
    bundled Silero VAD pass first and skips the segments it drops, and
    `condition_on_previous_text=False` stops one such hallucinated segment
    from seeding a repeating loop in the next one. (The project's own
    `voice_service.vad.contains_speech` is not used here instead: it is
    fixed to 8kHz/16kHz, while a video's audio track can be extracted at any
    original sample rate -- see `demo-master-agent/tools/video_tools.py`.)
    """
    from .config import get_settings

    settings = get_settings()
    segments, _info = _model(model_size or settings.stt_model).transcribe(
        str(audio_path),
        language=language or settings.stt_language or None,
        vad_filter=True,
        condition_on_previous_text=False,
    )
    return "".join(segment.text for segment in segments).strip()
