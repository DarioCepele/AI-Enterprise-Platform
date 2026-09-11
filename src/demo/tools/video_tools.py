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
  - a few frames are described by a `vision.VisionClient` (default:
    `vision.HttpVisionClient`, the same OpenAI-compatible endpoint the master
    agent's own chat client talks to) -- substitutable, so a test uses
    `vision.FakeVisionClient` instead of a real model, the same shape
    `chat_clients.fake` already gives the agent's own conversation.

Frame and audio extraction both go through PyAV (`av`): it reads and encodes
without a system `ffmpeg` binary to install (its wheel bundles the codec
libraries), which is why it was picked over shelling out to an external
`ffmpeg` process.
"""
from __future__ import annotations

import io
import logging
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

# "Some frames at intervals", not every frame: enough to describe a clip
# without asking the vision model to look at hundreds of near-duplicates.
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
        """Downloads a video, transcribes what is said, and describes what a few frames show.

        Use this whenever the user attaches or refers to a video and asks
        what it says or what it shows: nothing about a video's content is
        visible any other way. Both halves -- what was said (from the audio)
        and what appears in the frames (from vision) -- are always produced,
        even when one of the two turned out empty, and are returned as a
        structured `video-analysis` artifact for the frontend to render, the
        same way `ui_table` returns a `ui-table` artifact.
        """
        try:
            video_path = await _download(video_url)
        except Exception as error:
            logger.warning("Video not downloaded from %s: %s", video_url, error)
            return Content.from_text(f"I could not download the video: {error}")

        try:
            transcript = await _safe_transcribe(video_path, transcribe_url)
            frames = _safe_extract_frames(video_path, frame_interval_seconds, max_frames)
            description = await _safe_describe(vision, frames, question)
        finally:
            video_path.unlink(missing_ok=True)

        logger.info(
            "Video %s analyzed: %d characters said, %d frames, %d characters described.",
            video_url,
            len(transcript),
            len(frames),
            len(description),
        )

        artifact_id = f"art_{uuid.uuid4().hex[:8]}"
        heard = "speech" if transcript else "no speech"
        seen = "a description" if description else "no description"
        return state_update(
            text=f"I analyzed the video: {heard} understood, {seen} produced from the frames.",
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
        async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT, follow_redirects=True) as http:
            async with http.stream("GET", url) as response:
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
        logger.warning("%s did not transcribe the audio.", transcribe_url, exc_info=True)
        return ""
    return str(response.json().get("text", "")).strip()


def _safe_extract_frames(video_path: Path, interval_seconds: float, max_frames: int) -> list[bytes]:
    try:
        return _extract_frames(video_path, interval_seconds, max_frames)
    except Exception:
        logger.warning("Frames not extracted from %s.", video_path, exc_info=True)
        return []


async def _safe_describe(vision: VisionClient, frames: list[bytes], question: str) -> str:
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


def _extract_frames(video_path: Path, interval_seconds: float, max_frames: int) -> list[bytes]:
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
            timestamp = float(frame.pts * stream.time_base) if frame.pts is not None else next_at
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
