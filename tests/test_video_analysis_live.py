"""Live end-to-end test for Tappa 3 Step 4 of the plan
(`piani/2026-09-10-audio-video.md`): a real video with a spoken phrase and a
single-frame visual detail, analyzed by the real `analyze_video` tool
(`tools/video_tools.py`) against two real dependencies -- not the fakes
`tests/test_video_tools.py` uses for its own (fast, free, deterministic)
coverage of the tool's plumbing:

  - a real `demo-voice-service` subprocess, started from its own already-
    synced `.venv` (a sibling repo), for real speech-to-text -- the mirror
    image of what `demo-voice-service/tests/test_agui_bridge.py` already does
    to start a real `demo-master-agent` from *its* venv;
  - the real `vision.HttpVisionClient`, calling the OpenRouter/Claude Sonnet 5
    endpoint named by `config.Settings` (`base_url`/`api_key`/`model`).

Skips the whole module (rather than failing) when `OPENAI_API_KEY` is empty,
the same "skip on missing infrastructure, don't fail" idiom
`tests/test_log_stream.py` already uses for `DEMO_POSTGRES_DSN` -- this test
makes one real, billed call to a vision model, so absent credentials must be
a skip. It also skips on anything but Windows, since the audio fixture below
depends on Windows SAPI.

Exactly one call reaches the paid model: `HttpVisionClient.describe_frames`
sends every extracted frame in a single chat-completions request, regardless
of how many frames a clip yields (see that method) -- `analyze_video` calls
it once per video. Nothing in this test retries that call.

The audio -- a short sentence containing one distinctive, unambiguous word
("elephant") -- is synthesized offline with Windows SAPI
(`System.Speech.Synthesis`), the same engine that recorded
`demo-voice-service/tests/fixtures/speech_en.wav` (see that repo's
`tests/test_stt.py` docstring). It is rendered at runtime here, not checked
in as a binary, to keep this contract's diff to one file. The video is a
single solid-red (RGB 255, 0, 0) frame, repeated with PyAV + Pillow for the
audio's duration: a detail visible in one still frame, not a motion cue --
`tools/video_tools.py`'s `_extract_frames` only ever hands the vision model
isolated frames, never the clip as a sequence.
"""
from __future__ import annotations

import asyncio
import io
import json
import math
import socket
import subprocess
import sys
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import av
import httpx
import pytest
from PIL import Image

from demo.config import get_settings
from demo.tools.ui_tools import DISPLAY_KEY
from demo.tools.video_tools import build_video_tools
from demo.vision import HttpVisionClient

needs_windows = pytest.mark.skipif(
    sys.platform != "win32",
    reason="the audio fixture is synthesized with Windows SAPI (System.Speech.Synthesis)",
)
needs_real_model = pytest.mark.skipif(
    not get_settings().api_key,
    reason="OPENAI_API_KEY is empty: this test makes one real, billed call to a vision model",
)
pytestmark = [needs_windows, needs_real_model, pytest.mark.asyncio]

SPOKEN_PHRASE = "An elephant walked across the yard."
DISTINCTIVE_WORD = "elephant"
BACKGROUND_COLOR = (255, 0, 0)  # solid red -- visible in a single still frame
COLOR_WORDS = ("red", "crimson", "scarlet")

STARTUP_TIMEOUT_S = 60.0
REPO_ROOT = Path(__file__).resolve().parents[2]


def _voice_service_python() -> Path:
    """The interpreter `demo-voice-service` is already synced into (a sibling repo).

    Its dependencies (faster-whisper, silero-vad, kokoro, pipecat, ...) live
    only in that virtualenv -- not this project's -- so a subprocess using
    that interpreter is the only way to run its real STT without adding all
    of that as a dependency here.
    """
    repo = REPO_ROOT / "demo-voice-service"
    windows_python = repo / ".venv" / "Scripts" / "python.exe"
    posix_python = repo / ".venv" / "bin" / "python"
    return windows_python if windows_python.exists() else posix_python


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


async def _wait_until_ready(base_url: str, process: subprocess.Popen) -> None:
    deadline = time.monotonic() + STARTUP_TIMEOUT_S
    async with httpx.AsyncClient(timeout=2.0) as client:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                output = process.stdout.read() if process.stdout else ""
                raise RuntimeError(
                    f"demo-voice-service exited early (code {process.returncode}):\n{output}"
                )
            try:
                response = await client.get(f"{base_url}/health/live")
                if response.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.3)
    process.kill()
    raise RuntimeError(f"demo-voice-service did not become ready within {STARTUP_TIMEOUT_S}s")


@pytest.fixture
async def voice_service_url():
    """Starts a real `demo-voice-service` (its own venv, its own process) and tears it down.

    Mirrors `demo-voice-service/tests/test_agui_bridge.py`'s
    `master_agent_url` fixture, in the opposite direction: that one starts a
    real `demo-master-agent` from this project's sibling venv; this one
    starts a real `demo-voice-service` the same way. Launched via
    `python -m voice_service` (its own `__main__.py`), not a bare
    `uvicorn ...` invocation -- that module sets
    `WindowsSelectorEventLoopPolicy` before touching uvicorn, the fix that
    same repo's README documents as required on Windows.
    """
    python = _voice_service_python()
    if not python.exists():
        pytest.skip(
            f"demo-voice-service has no synced .venv at {REPO_ROOT / 'demo-voice-service'} "
            "(expected a sibling repo with `uv sync` already run)."
        )

    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"

    import os

    env = dict(os.environ)
    env["VOICE_PORT"] = str(port)

    process = subprocess.Popen(
        [str(python), "-m", "voice_service"],
        cwd=str(REPO_ROOT / "demo-voice-service"),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        await _wait_until_ready(base_url, process)
        yield base_url
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


def _synthesize_speech(text: str, out_path: Path) -> None:
    """Renders `text` offline via Windows SAPI, as 16kHz mono 16-bit PCM WAV.

    `System.Speech.Synthesis.SpeechSynthesizer` is the same engine
    `demo-voice-service/tests/fixtures/speech_en.wav` was recorded with (see
    that repo's `tests/test_stt.py`); the format is forced explicitly here so
    the fixture's sample rate does not depend on whatever voice happens to be
    installed.

    The voice is selected explicitly to an installed en-US one when
    available: this machine's *default* SAPI voice turned out to be an
    Italian one ("Microsoft Elsa Desktop") -- speaking an English sentence
    with it mangled words badly enough that Whisper transcribed "elephant"
    as "Anela found" (verified directly against `demo-voice-service`'s own
    `stt.transcribe` while building this fixture). Selecting an en-US voice
    by name fixed it outright; if no en-US voice is installed, this falls
    back to whatever `SpeechSynthesizer` defaults to rather than failing the
    whole fixture.
    """
    script = f"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$enUs = $synth.GetInstalledVoices() |
    Where-Object {{ $_.VoiceInfo.Culture.Name -eq 'en-US' }} |
    Select-Object -First 1
if ($enUs) {{ $synth.SelectVoice($enUs.VoiceInfo.Name) }}
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(
    16000,
    [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,
    [System.Speech.AudioFormat.AudioChannel]::Mono)
$synth.SetOutputToWaveFile('{out_path}', $fmt)
$synth.Speak('{text}')
$synth.Dispose()
"""
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0 or not out_path.exists():
        raise RuntimeError(
            f"Windows SAPI did not render '{text}' to {out_path}: "
            f"exit {result.returncode}, stderr: {result.stderr}"
        )


def _build_test_video(speech_wav: Path) -> bytes:
    """A small mp4: the real spoken audio, plus solid-red frames covering its duration.

    Muxes real PCM samples (not silence) into an `aac` audio stream, and
    solid-color `mpeg4` video frames -- the same two-stream mp4-building
    approach `tests/test_video_tools.py`'s `_build_video` uses for its own
    (silent, synthetic) fixture.
    """
    with wave.open(str(speech_wav), "rb") as wav_file:
        assert wav_file.getsampwidth() == 2, "SAPI fixture must be 16-bit PCM"
        assert wav_file.getnchannels() == 1, "SAPI fixture must be mono"
        sample_rate = wav_file.getframerate()
        total_samples = wav_file.getnframes()
        pcm_bytes = wav_file.readframes(total_samples)

    duration_s = total_samples / sample_rate
    fps = 2
    frame_count = max(1, math.ceil(duration_s * fps))

    buffer = io.BytesIO()
    container = av.open(buffer, mode="w", format="mp4")
    try:
        # Both streams must be declared before any packet is muxed: muxing the
        # first packet writes the container header naming every stream that
        # exists at that point -- adding one afterward (as a first draft of
        # this fixture did) makes the mp4 muxer unable to place the new
        # stream's timestamps ("Cannot rebase to zero time").
        video_stream = container.add_stream("mpeg4", rate=fps)
        video_stream.width = 64
        video_stream.height = 64
        video_stream.pix_fmt = "yuv420p"

        audio_stream = container.add_stream("aac", rate=sample_rate)
        audio_stream.layout = "mono"

        red_image = Image.new("RGB", (64, 64), color=BACKGROUND_COLOR)
        for _ in range(frame_count):
            frame = av.VideoFrame.from_image(red_image)
            for packet in video_stream.encode(frame):
                container.mux(packet)
        for packet in video_stream.encode():
            container.mux(packet)

        frame_size = audio_stream.codec_context.frame_size or 1024
        written, pts = 0, 0
        while written < total_samples:
            count = min(frame_size, total_samples - written)
            chunk = pcm_bytes[written * 2 : (written + count) * 2]
            audio_frame = av.AudioFrame(format="s16", layout="mono", samples=count)
            audio_frame.sample_rate = sample_rate
            audio_frame.pts = pts
            audio_frame.planes[0].update(chunk)
            for packet in audio_stream.encode(audio_frame):
                container.mux(packet)
            written += count
            pts += count
        for packet in audio_stream.encode():
            container.mux(packet)
    finally:
        container.close()
    return buffer.getvalue()


class _VideoHandler(BaseHTTPRequestHandler):
    """Serves the same fixed video bytes to any GET -- a stand-in for `/uploads`."""

    video_bytes: bytes = b""

    def do_GET(self) -> None:  # noqa: N802 -- BaseHTTPRequestHandler's own naming
        self.send_response(200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(len(self.video_bytes)))
        self.end_headers()
        self.wfile.write(self.video_bytes)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass  # keep test output focused on the assertions, not access logs


@pytest.fixture
def video_url(tmp_path):
    """A tiny local HTTP server handing out the synthesized test video."""
    speech_path = tmp_path / "speech.wav"
    _synthesize_speech(SPOKEN_PHRASE, speech_path)
    video_bytes = _build_test_video(speech_path)

    handler = type("_BoundVideoHandler", (_VideoHandler,), {"video_bytes": video_bytes})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        yield f"http://127.0.0.1:{port}/video.mp4"
    finally:
        server.shutdown()
        server.server_close()


async def test_the_live_answer_cites_both_the_spoken_word_and_the_visual_detail(
    voice_service_url, video_url
):
    """The real tool, against the real voice service and the real vision model.

    Prints the exact transcript and description it got back (visible with
    `pytest -s`) -- a real model does not answer with predictable text, so
    what actually came back is worth seeing, not just asserting against.
    """
    tools = {t.name: t for t in build_video_tools(voice_service_url, vision_client=HttpVisionClient())}

    answer = await tools["analyze_video"].func(video_url=video_url)
    payload = json.loads(answer.additional_properties[DISPLAY_KEY])
    transcript = payload["transcript"]
    description = payload["description"]

    print(f"\nLIVE TRANSCRIPT: {transcript!r}")
    print(f"LIVE DESCRIPTION: {description!r}")

    assert DISTINCTIVE_WORD in transcript.lower(), (
        f"transcript did not mention '{DISTINCTIVE_WORD}': {transcript!r}"
    )
    assert any(word in description.lower() for word in COLOR_WORDS), (
        f"description did not mention the background color: {description!r}"
    )
