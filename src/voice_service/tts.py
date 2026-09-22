"""Text-to-speech: turn text into audio.

Uses Kokoro-82M (`kokoro` on PyPI, https://pypi.org/project/kokoro/, by
hexgrad -- the model this repo's own plan names as the TTS default, see
`piani/2026-09-10-audio-video.md`, Tappa 1's "STT/TTS/VAD di default" row):
Apache-2.0 weights, ~82M parameters, fast enough on CPU to not need a GPU --
matching this repo's no-GPU-by-default posture (`voice_service.stt` makes the
same choice for STT). Runs on CPU by default through PyTorch; nothing here
pins a CUDA device.

System dependencies: none beyond what `uv sync` installs. Kokoro's G2P
(`misaki[en]`) normally needs eSpeak NG installed on the system for
out-of-dictionary words, but its `espeakng-loader` dependency ships eSpeak
NG's shared library inside a Python wheel -- confirmed by installing this
module's dependencies and running a synthesis on Windows with no system
package manager involved and no build toolchain invoked.

Kokoro's native output is 24kHz mono float32 in [-1, 1]
(`hexgrad/Kokoro-82M` on Hugging Face). This module converts that to 16-bit
PCM bytes -- the same sample *format* `voice_service.pipeline` uses for its
WebSocket input (see that module's `SAMPLE_RATE`) -- but does not resample
to 16kHz: that would need an extra dependency and would only throw away
quality, and nothing downstream needs a specific rate. `SAMPLE_RATE` below
is Kokoro's native rate; `voice_service.api` sends it alongside each audio
chunk so the receiving end always knows how to play the bytes back, the same
job a wav header would do.

Not deterministic: two `synthesize()` calls with the same text and voice
produce audio that differs sample-by-sample (measured directly -- see
`tests/test_tts.py`'s docstring), not just in the initial warm-up. That
looks like sampling noise internal to the model rather than a caching bug:
this module makes no attempt to seed or cache around it, and tests assert
non-empty, plausibly-timed audio, never exact bytes.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any

import numpy as np
import numpy.typing as npt

SAMPLE_RATE = 24_000
"""Kokoro's native output rate (mono). Not resampled -- see module docstring."""

VOICE = "af_heart"
"""Kokoro's American-English voice used throughout the project's own
README/Colab examples -- picked as a well-known default; nothing else in
this contract steers the choice of voice."""

LANG_CODE = "a"
"""Kokoro's language code for American English, matching `VOICE`."""


@lru_cache(maxsize=1)
def _pipeline() -> Any:
    """Loads the Kokoro pipeline once and reuses it for the process lifetime.

    Downloads `hexgrad/Kokoro-82M`'s weights from Hugging Face on first use
    (cached under the user's home directory, the same as
    `voice_service.stt`'s faster-whisper model); loads from local disk after
    that.
    """
    from kokoro import KPipeline

    return KPipeline(lang_code=LANG_CODE)


def synthesize(text: str, *, voice: str = VOICE) -> bytes:
    """Synthesizes `text` as speech.

    Returns 16-bit PCM mono bytes at `SAMPLE_RATE`, or `b""` for empty/
    whitespace-only text (Kokoro itself would otherwise raise on it).

    Kokoro generates its output in its own internal segments -- unrelated to
    `voice_service.agui_client.AGUIBridgeClient.stream_turn`'s sentence
    chunking, which is what decides how much text reaches one call of this
    function at a time. They are concatenated here into one clip since this
    function's contract is "text in, one clip out"; the streaming behaviour
    the plan asks for (Step 4/5 of Tappa 1) comes from calling this function
    once per `stream_turn` chunk, in `voice_service.api`, not from streaming
    inside a single call.
    """
    if not text.strip():
        return b""

    segments: list[npt.NDArray[np.float32]] = []
    for _graphemes, _phonemes, audio in _pipeline()(text, voice=voice):
        segments.append(np.asarray(audio, dtype=np.float32))

    if not segments:
        return b""

    samples = np.concatenate(segments)
    samples = np.clip(samples, -1.0, 1.0)
    pcm16 = (samples * 32767.0).astype(np.int16)
    return pcm16.tobytes()
