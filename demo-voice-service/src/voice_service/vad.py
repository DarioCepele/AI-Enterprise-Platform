"""Voice activity detection: does this audio segment contain speech?

Uses Silero VAD (the `silero-vad` PyPI package), which bundles its model
weights inside the package itself -- `load_silero_vad()` reads a local
`.jit`/`.onnx` file from `site-packages`, it does not reach out to torch hub
or the network. The model is loaded once per process and reused.

This module only answers "voice or not" for an in-memory audio array. It is
deliberately not wired to a file format, a socket, or a streaming API yet --
that is the next contract's job.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any

import numpy as np
import torch

SAMPLE_RATE = 16_000


@lru_cache(maxsize=1)
def _model() -> Any:
    """Loads the Silero VAD model once and reuses it for the process lifetime."""
    from silero_vad import load_silero_vad

    return load_silero_vad(onnx=False)


def contains_speech(
    audio: np.ndarray,
    sample_rate: int = SAMPLE_RATE,
    threshold: float = 0.5,
) -> bool:
    """Whether `audio` (mono, float32 in [-1, 1]) has at least one speech segment.

    `sample_rate` must be 8000 or 16000 (Silero VAD's supported rates); pass
    audio already resampled if it comes from another source. `threshold` is
    Silero's speech-probability cutoff -- 0.5 is its documented default.
    """
    from silero_vad import get_speech_timestamps

    tensor = torch.from_numpy(np.asarray(audio, dtype=np.float32))
    timestamps = get_speech_timestamps(
        tensor,
        _model(),
        sampling_rate=sample_rate,
        threshold=threshold,
    )
    return len(timestamps) > 0
