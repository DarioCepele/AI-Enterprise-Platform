"""Text-to-speech: turn text into audio.

Uses Kokoro-82M (`kokoro` on PyPI, https://pypi.org/project/kokoro/, by
hexgrad): Apache-2.0 weights, ~82M parameters, fast enough on CPU to not need a
GPU -- the same no-GPU-by-default posture `voice_service.stt` takes. The
language and the voice are configuration (`VOICE_TTS_LANG_CODE`,
`VOICE_TTS_VOICE`): an agent answering in Italian needs an Italian voice, or
every word is read with an English accent. Runs on CPU through PyTorch;
nothing here pins a CUDA device.

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
"""Kokoro's American-English default voice; `VOICE_TTS_VOICE` overrides it."""

LANG_CODE = "a"
"""Kokoro's language code for American English; `VOICE_TTS_LANG_CODE` overrides it."""


@lru_cache(maxsize=2)
def _pipeline(lang_code: str = LANG_CODE) -> Any:
    """Loads the Kokoro pipeline once and reuses it for the process lifetime.

    Downloads `hexgrad/Kokoro-82M`'s weights from Hugging Face on first use
    (cached under the user's home directory, the same as
    `voice_service.stt`'s faster-whisper model); loads from local disk after
    that.
    """
    from kokoro import KPipeline

    return KPipeline(lang_code=lang_code)


def synthesize(text: str, *, voice: str | None = None) -> bytes:
    """Synthesizes `text` as speech.

    Returns 16-bit PCM mono bytes at `SAMPLE_RATE`, or `b""` for empty/
    whitespace-only text (Kokoro itself would otherwise raise on it).

    Kokoro generates its output in its own internal segments -- unrelated to
    `voice_service.agui_client.AGUIBridgeClient.stream_turn`'s sentence
    chunking, which is what decides how much text reaches one call of this
    function at a time. They are concatenated here into one clip since this
    function's contract is "text in, one clip out"; the reply streams because
    `voice_service.api` calls it once per `stream_turn` sentence, not because
    a single call streams.
    """
    if not text.strip():
        return b""

    from .config import get_settings

    settings = get_settings()
    segments: list[npt.NDArray[np.float32]] = []
    speaking = _pipeline(settings.tts_lang_code)
    for _graphemes, _phonemes, audio in speaking(
        text, voice=voice or settings.tts_voice
    ):
        segments.append(np.asarray(audio, dtype=np.float32))

    if not segments:
        return b""

    samples = np.concatenate(segments)
    samples = np.clip(samples, -1.0, 1.0)
    pcm16 = (samples * 32767.0).astype(np.int16)
    return pcm16.tobytes()
