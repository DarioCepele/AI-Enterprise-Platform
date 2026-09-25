"""The Pipecat pipeline: audio in, a transcribed user turn out.

Two custom `FrameProcessor` stages, linked through a real Pipecat `Pipeline`
and driven by Pipecat's own `PipelineWorker` + `WorkerRunner` (the same
execution path `pipecat`'s project templates use -- see
`pipecat.cli.templates.server.bot_cascade.py.jinja2`). Frames flowing between
stages are Pipecat's own dataclasses (`InputAudioRawFrame`,
`TranscriptionFrame`, plus one local addition, `EndOfTurnAudioFrame`), so the
pipeline/frame model does the orchestration -- this module does not invent a
parallel one.

The two stages:

- `TurnEndpointingProcessor`: accumulates incoming PCM16 audio for the
  current turn and asks `voice_service.vad.contains_speech` about each
  ~300ms slice as it arrives. Once speech has been seen in the turn and a
  configurable amount of trailing silence follows, the turn is over: the
  whole turn's audio is pushed downstream as one `EndOfTurnAudioFrame` and
  the buffer resets for the next turn. This is a batch VAD call (Silero's
  `get_speech_timestamps`) run repeatedly on small windows, not Pipecat's own
  streaming `VADAnalyzer` interface -- reusing the already-built,
  already-tested `voice_service.vad` module (per contract) takes priority
  over Pipecat's native per-chunk VAD hook.
- `TranscriptionProcessor`: on `EndOfTurnAudioFrame`, writes the turn's audio
  to a temporary wav file and calls `voice_service.stt.transcribe` on it --
  the same faster-whisper engine already built and tested, no other
  provider -- then pushes a `TranscriptionFrame` with the result.

Nothing here talks to a WebSocket; `voice_service.api` wires this pipeline's
input/output to `/ws/voice`.
"""
from __future__ import annotations

import asyncio
import tempfile
import uuid
import wave
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import numpy as np
from pipecat.frames.frames import (
    DataFrame,
    Frame,
    InputAudioRawFrame,
    TranscriptionFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from .stt import transcribe
from .vad import contains_speech

if TYPE_CHECKING:
    from pipecat.transcriptions.language import Language

SAMPLE_RATE = 16_000
"""PCM16 mono sample rate this pipeline (and `/ws/voice`) expects."""

# Endpointing heuristic: how much audio to feed `contains_speech` at a time,
# and how much trailing silence after speech ends a turn. 320ms keeps each
# analysis window comfortably above Silero's own 250ms minimum speech
# duration (shorter windows risk a real word being discarded as too short to
# count). 600ms of trailing silence is a common quick-endpointing default
# (see the plan's Tappa 2 note that pure-silence endpointing is a known-rough
# first cut, refined later with measured conversations); it is exposed as a
# constructor argument so a caller can tune it without editing this module.
# These are also the values `voice_service.config.Settings` defaults its
# `analysis_window_s`/`silence_threshold_s` fields to (Tappa 2 Step 3 of the
# plan: configurable thresholds, same defaults) -- `build_voice_pipeline`
# below is the seam `voice_service.api` uses to pass the configured values in.
ANALYSIS_WINDOW_S = 0.32
SILENCE_THRESHOLD_S = 0.6


@dataclass
class EndOfTurnAudioFrame(DataFrame):
    """The full audio of one user turn, ready to be transcribed.

    Not a Pipecat built-in frame -- Pipecat has no "batch VAD decided this
    turn is over" frame type, so this is the local seam between the
    endpointing stage and the transcription stage.
    """

    audio: bytes
    sample_rate: int


class TurnEndpointingProcessor(FrameProcessor):
    """Buffers a user turn's audio and decides, via VAD, when it has ended."""

    def __init__(
        self,
        *,
        sample_rate: int = SAMPLE_RATE,
        analysis_window_s: float = ANALYSIS_WINDOW_S,
        silence_threshold_s: float = SILENCE_THRESHOLD_S,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._sample_rate = sample_rate
        self._analysis_window_samples = max(1, int(analysis_window_s * sample_rate))
        self._silence_threshold_s = silence_threshold_s

        self._turn_buffer = bytearray()
        self._pending = bytearray()
        self._speech_seen = False
        self._silence_run_s = 0.0

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, InputAudioRawFrame):
            await self._on_audio(frame)
        else:
            await self.push_frame(frame, direction)

    async def _on_audio(self, frame: InputAudioRawFrame) -> None:
        self._turn_buffer.extend(frame.audio)
        self._pending.extend(frame.audio)

        window_bytes = self._analysis_window_samples * 2  # 16-bit PCM
        while len(self._pending) >= window_bytes:
            chunk = bytes(self._pending[:window_bytes])
            del self._pending[:window_bytes]
            await self._analyze_chunk(chunk)

    async def _analyze_chunk(self, chunk_bytes: bytes) -> None:
        samples = (
            np.frombuffer(chunk_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        )
        # contains_speech() runs Silero inference -- keep it off the event loop.
        has_speech = await asyncio.to_thread(
            contains_speech, samples, self._sample_rate
        )
        chunk_duration_s = len(samples) / self._sample_rate

        if has_speech:
            self._speech_seen = True
            self._silence_run_s = 0.0
        else:
            self._silence_run_s += chunk_duration_s

        if self._speech_seen and self._silence_run_s >= self._silence_threshold_s:
            await self._end_turn()

    async def _end_turn(self) -> None:
        turn_audio = bytes(self._turn_buffer)
        self._turn_buffer.clear()
        self._pending.clear()
        self._speech_seen = False
        self._silence_run_s = 0.0

        await self.push_frame(
            EndOfTurnAudioFrame(audio=turn_audio, sample_rate=self._sample_rate)
        )


class TranscriptionProcessor(FrameProcessor):
    """Transcribes a finished turn's audio and emits a `TranscriptionFrame`."""

    def __init__(
        self, *, language: str | None = None, user_id: str = "user", **kwargs: Any
    ) -> None:
        super().__init__(**kwargs)
        self._language = language
        self._user_id = user_id

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, EndOfTurnAudioFrame):
            await self._transcribe_turn(frame)
        else:
            await self.push_frame(frame, direction)

    async def _transcribe_turn(self, frame: EndOfTurnAudioFrame) -> None:
        if not frame.audio:
            return

        tmp_name = f"voice-service-turn-{uuid.uuid4().hex}.wav"
        tmp_path = Path(tempfile.gettempdir()) / tmp_name
        try:
            _write_pcm16_wav(tmp_path, frame.audio, frame.sample_rate)
            # faster-whisper inference -- keep it off the event loop too.
            text = await asyncio.to_thread(transcribe, tmp_path, self._language)
        finally:
            tmp_path.unlink(missing_ok=True)

        if text:
            await self.push_frame(
                TranscriptionFrame(
                    text=text,
                    user_id=self._user_id,
                    timestamp=datetime.now(UTC).isoformat(),
                    # Pipecat types this as its `Language` StrEnum; what we
                    # hold is the same ISO 639-1 string (or None), passed
                    # through unchanged as it always has been.
                    language=cast("Language | None", self._language),
                    finalized=True,
                )
            )


def _write_pcm16_wav(path: Path, pcm_bytes: bytes, sample_rate: int) -> None:
    """Writes raw 16-bit PCM mono audio as a wav file `voice_service.stt.transcribe`
    can read.
    """
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm_bytes)


def build_voice_pipeline(
    *,
    language: str | None = None,
    analysis_window_s: float = ANALYSIS_WINDOW_S,
    silence_threshold_s: float = SILENCE_THRESHOLD_S,
) -> Pipeline:
    """Builds the VAD -> STT pipeline used by `/ws/voice`.

    `language` is forwarded to `voice_service.stt.transcribe` (an ISO 639-1
    code, or `None` to auto-detect); exposed here so a caller with known
    context can skip auto-detection the way `tests/test_stt.py` does.

    `analysis_window_s`/`silence_threshold_s` are forwarded to
    `TurnEndpointingProcessor` (see its docstring, and the module-level
    constants above); defaulting to those same constants keeps every
    existing caller -- including every test that calls this function with no
    arguments -- on the exact same behaviour as before Tappa 2 Step 3 made
    them configurable. `voice_service.api` passes in
    `voice_service.config.Settings.analysis_window_s`/`silence_threshold_s`
    (environment-configurable, same defaults).
    """
    return Pipeline(
        [
            TurnEndpointingProcessor(
                analysis_window_s=analysis_window_s,
                silence_threshold_s=silence_threshold_s,
            ),
            TranscriptionProcessor(language=language),
        ]
    )
