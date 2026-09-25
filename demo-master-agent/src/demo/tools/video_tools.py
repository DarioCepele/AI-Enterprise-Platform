"""The tool that analyzes an attached video: what is said, and what is shown.

`server/attachments.py` already turns an AG-UI video/audio part into a plain
text note next to the message (`[allegato video: http://.../uploads/xyz]`),
so the model can see the URL and pass it here as a string argument -- this
module is where that URL is finally read.

The video itself never touches a datastore: it is downloaded to a short-lived
temp file, its audio and a few frames are pulled from it, and the file is
removed before the tool returns, win or lose -- consistent with
`server/uploads.py`'s own "ephemeral, not a datastore" posture for the same
media.

Two remote dependencies, both already built elsewhere and deliberately not
duplicated here:
  - the audio track is transcribed by **`demo-voice-service`'s `POST
    /transcribe`** (see that service's `voice_service/api.py`) -- this module
    does not embed a second speech-to-text engine;
  - the picture is described by a `vision.VisionClient` (default:
    `vision.HttpVisionClient`, the same OpenAI-compatible endpoint the master
    agent's own chat client talks to) -- substitutable, so a test uses
    `vision.FakeVisionClient` instead of a real model, the same shape
    `chat_clients.fake` already gives the agent's own conversation.

The vision half prefers sending the whole downloaded video natively
(`VisionClient.describe_video`, OpenRouter's `video_url` content type: a
video-capable model samples across the clip's full length itself, the
current standard -- see e.g. Gemini's and Qwen3-VL's own internal frame
sampling). A model configured without video support raises there, and only
then do we fall back to describing a handful of JPEG frames pulled out with
PyAV (`_extract_frames`) -- coarser (a fixed, short window), but works with
any vision-capable model regardless of whether it accepts video input. This
is a template: whoever forks it picks their own model, so the tool cannot
assume either capability.

Frame and audio extraction both go through PyAV (`av`): it reads and encodes
without a system `ffmpeg` binary to install (its wheel bundles the codec
libraries), which is why it was picked over shelling out to an external
`ffmpeg` process.
"""

from __future__ import annotations

import io
import logging
import mimetypes
import tempfile
import uuid
from pathlib import Path
from typing import Annotated

import httpx
from agent_framework import Content, FunctionTool, tool
from agent_framework.ag_ui import state_update

from ..config import get_settings
from ..vision import HttpVisionClient, VisionClient

logger = logging.getLogger(__name__)

DOWNLOAD_TIMEOUT = 60.0
TRANSCRIBE_TIMEOUT = 120.0

# One video, not a datastore -- the same ceiling `server/uploads.py` applies
# to the upload this URL most often points back at.
MAX_VIDEO_BYTES = 200 * 1024 * 1024

# Fallback-only: how densely to sample frames when the configured vision
# model has no native video support (see `_safe_describe_video`). "Some
# frames at intervals", not every frame -- enough to describe a clip without
# asking the vision model to look at hundreds of near-duplicates, at the
# cost of only covering the clip's first `MAX_FRAMES * FRAME_INTERVAL_SECONDS`
# seconds.
FRAME_INTERVAL_SECONDS = 4.0
MAX_FRAMES = 4


def build_video_tools(
    voice_service_url: str,
    vision_client: VisionClient | None = None,
    frame_interval_seconds: float = FRAME_INTERVAL_SECONDS,
    max_frames: int = MAX_FRAMES,
) -> list[FunctionTool]:
    """The `analyze_video` tool, bound to a transcription and a vision service.

    No `voice_service_url` means no tool: same reasoning as
    `build_process_tools`/`build_memory_tools` -- a tool that can only ever
    answer "unreachable" should not be shown to the model at all.
    """
    if not voice_service_url:
        return []

    transcribe_url = voice_service_url.rstrip("/") + "/transcribe"
    # Its own model, distinct from `Settings.model`: the conversation's model
    # may only see separate frames, not the video itself.
    vision = vision_client or HttpVisionClient(model=get_settings().vision_model)

    @tool
    async def analyze_video(
        video_url: Annotated[
            str,
            "The video's URL, e.g. from a '[allegato video: ...]' note next to the "
            "user's message, or any other direct HTTP(S) URL to a video file",
        ],
        question: Annotated[
            str,
            "What to pay attention to in the frames (a person, an object, on-screen "
            "text...); leave empty for a general description",
        ] = "",
    ) -> Content:
        """Downloads a video, transcribes what is said, and describes what it shows.

        Use this whenever the user attaches or refers to a video and asks
        what it says or what it shows: nothing about a video's content is
        visible any other way. Both halves -- what was said (from the audio)
        and what appears on screen (from vision, across the whole video when
        the configured model supports it) -- are always produced, even when
        one of the two turned out empty, are returned in full so the answer
        can be grounded in them, and are also returned as a structured
        `video-analysis` artifact for the frontend to render, the same way
        `ui_table` returns a `ui-table` artifact.
        """
        try:
            video_path = await _download(video_url)
        except Exception as error:
            logger.warning("Video not downloaded from %s: %s", video_url, error)
            return Content.from_text(f"I could not download the video: {error}")

        try:
            transcript = await _safe_transcribe(video_path, transcribe_url)
            description = await _safe_describe_video(
                vision,
                video_path,
                video_url,
                question,
                frame_interval_seconds,
                max_frames,
            )
        finally:
            video_path.unlink(missing_ok=True)

        logger.info(
            "Video %s analyzed: %d characters said, %d characters described.",
            video_url,
            len(transcript),
            len(description),
        )

        artifact_id = f"art_{uuid.uuid4().hex[:8]}"
        # Unlike `ui_table`'s artifact, this one's real content also goes into
        # `text` -- not just a confirmation stub -- so the model actually has
        # the transcript and description in its own context on later turns
        # and can answer a follow-up question about the same video without
        # calling this tool again (see the instructions in `agents/master.py`).
        text_for_model = (
            f"What was said: {transcript or '(no speech detected)'}\n"
            f"What is shown: {description or '(no description produced)'}"
        )
        return state_update(
            text=text_for_model,
            tool_result={
                "component": "video-analysis",
                "id": artifact_id,
                "video_url": video_url,
                "transcript": transcript,
                "description": description,
            },
        )

    return [analyze_video]


async def _download(url: str) -> Path:
    """Streams `url` to a temp file and returns its path, enforcing `MAX_VIDEO_BYTES`.

    Streamed rather than read whole into memory first, the same reason
    `UploadStore.save` streams an upload to disk: a body over the limit is
    caught -- and the partial file removed -- without ever holding the whole
    video in memory.
    """
    path = Path(tempfile.gettempdir()) / f"demo-video-analysis-{uuid.uuid4().hex}.bin"
    written = 0
    try:
        async with (
            httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT, follow_redirects=True) as http,
            http.stream("GET", url) as response,
        ):
            response.raise_for_status()
            with path.open("wb") as handle:
                async for chunk in response.aiter_bytes():
                    written += len(chunk)
                    if written > MAX_VIDEO_BYTES:
                        raise ValueError(f"video over {MAX_VIDEO_BYTES} bytes")
                    handle.write(chunk)
        if written == 0:
            raise ValueError("empty video")
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path


async def _safe_transcribe(video_path: Path, transcribe_url: str) -> str:
    """The transcript, or "" -- a bad audio track or an unreachable service
    still lets the tool answer with what it could see."""
    try:
        wav_bytes = _extract_audio_wav(video_path)
    except Exception:
        logger.warning("Audio not extracted from %s.", video_path, exc_info=True)
        return ""
    if wav_bytes is None:
        return ""

    try:
        async with httpx.AsyncClient(timeout=TRANSCRIBE_TIMEOUT) as http:
            response = await http.post(
                transcribe_url,
                files={"file": ("audio.wav", wav_bytes, "audio/wav")},
            )
        response.raise_for_status()
    except Exception:
        logger.warning(
            "%s did not transcribe the audio.", transcribe_url, exc_info=True
        )
        return ""
    return str(response.json().get("text", "")).strip()


async def _safe_describe_video(
    vision: VisionClient,
    video_path: Path,
    video_url: str,
    question: str,
    frame_interval_seconds: float,
    max_frames: int,
) -> str:
    """Native video first, a handful of sampled frames as the fallback.

    `VisionClient.describe_video` raises when the configured model has no
    video modality (or the call otherwise fails) -- caught here rather than
    left to `analyze_video` itself, so a model without video support still
    gets a real answer instead of an error.
    """
    try:
        video_bytes = video_path.read_bytes()
        mime_type = mimetypes.guess_type(video_url)[0] or "video/mp4"
        return (await vision.describe_video(video_bytes, mime_type, question)).strip()
    except Exception:
        logger.info(
            "%s has no usable native video support; falling back to %d sampled frames.",
            type(vision).__name__,
            max_frames,
            exc_info=True,
        )

    frames = _safe_extract_frames(video_path, frame_interval_seconds, max_frames)
    return await _safe_describe(vision, frames, question)


def _safe_extract_frames(
    video_path: Path, interval_seconds: float, max_frames: int
) -> list[bytes]:
    try:
        return _extract_frames(video_path, interval_seconds, max_frames)
    except Exception:
        logger.warning("Frames not extracted from %s.", video_path, exc_info=True)
        return []


async def _safe_describe(
    vision: VisionClient, frames: list[bytes], question: str
) -> str:
    if not frames:
        return ""
    try:
        return (await vision.describe_frames(frames, question)).strip()
    except Exception:
        logger.warning("Frames not described by the vision client.", exc_info=True)
        return ""


def _extract_audio_wav(video_path: Path) -> bytes | None:
    """The video's audio track, re-encoded as a WAV `bytes`, or `None` when
    there is no audio stream to read.

    `voice_service`'s `/transcribe` writes whatever bytes it is given to a
    temp file and hands the *path* to `faster-whisper`, which decodes it with
    its own bundled PyAV -- so any container/codec it understands would do;
    WAV/PCM is chosen here only because it is trivial to mux to without
    picking a lossy audio codec's bitrate.
    """
    import av

    container = av.open(str(video_path))
    try:
        stream = next(iter(container.streams.audio), None)
        if stream is None:
            return None

        out_buffer = io.BytesIO()
        out_container = av.open(out_buffer, mode="w", format="wav")
        try:
            sample_rate = stream.codec_context.sample_rate or 16_000
            out_stream = out_container.add_stream("pcm_s16le", rate=sample_rate)
            resampler = av.AudioResampler(format="s16", layout="mono", rate=sample_rate)

            for packet in container.demux(stream):
                for frame in packet.decode():
                    for resampled in resampler.resample(frame):
                        for out_packet in out_stream.encode(resampled):
                            out_container.mux(out_packet)
            for out_packet in out_stream.encode():
                out_container.mux(out_packet)
        finally:
            out_container.close()
        return out_buffer.getvalue()
    finally:
        container.close()


def _extract_frames(
    video_path: Path, interval_seconds: float, max_frames: int
) -> list[bytes]:
    """Up to `max_frames` JPEG-encoded frames, spaced `interval_seconds` apart.

    Spaced by timestamp rather than by frame count, so the same interval
    means the same thing regardless of the clip's frame rate; the first
    frame is always included. Requires no decoding library beyond PyAV +
    Pillow (`VideoFrame.to_image()`), so no `numpy` dependency either.
    """
    import av

    container = av.open(str(video_path))
    try:
        stream = next(iter(container.streams.video), None)
        if stream is None:
            return []

        frames: list[bytes] = []
        next_at = 0.0
        for frame in container.decode(stream):
            # `stream.time_base` is typed `Fraction | None` by PyAV because a
            # malformed or unusual container can omit it -- an upload from an
            # arbitrary user is exactly the kind of input that could, so this
            # falls back to `next_at` the same way a missing `frame.pts` does,
            # rather than assuming it is always set.
            timestamp = (
                float(frame.pts * stream.time_base)
                if frame.pts is not None and stream.time_base is not None
                else next_at
            )
            if timestamp < next_at:
                continue
            buffer = io.BytesIO()
            frame.to_image().save(buffer, format="JPEG")
            frames.append(buffer.getvalue())
            next_at = timestamp + interval_seconds
            if len(frames) >= max_frames:
                break
        return frames
    finally:
        container.close()
