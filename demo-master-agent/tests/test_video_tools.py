"""`analyze_video`: downloads a video, transcribes its speech via
`demo-voice-service`'s `POST /transcribe`, and describes what it shows via a
substitutable vision client -- natively (the whole video, `describe_video`)
when the configured model supports it, falling back to a handful of sampled
frames (`describe_frames`) when it does not.

The video fixture is synthesized with PyAV (`av`) rather than checked in as a
binary: a handful of solid-red frames plus a silent audio track, muxed into a
small mp4 -- enough to exercise real frame/audio extraction without a
recorded clip. Reusing `demo-voice-service`'s own SAPI-recorded fixture is
not an option here (a second repo, a second `.venv`); instead the HTTP call
to `/transcribe` is mocked, the same way `test_process_tools.py` mocks calls
to `process-service` -- this test is about the tool's own plumbing (it
downloads, it extracts, it calls out, it reports both halves), not about
whether faster-whisper transcribes correctly (that is `demo-voice-service`'s
own test suite's job).
"""
from __future__ import annotations

import io
import json

import av
import httpx
import pytest
from contracts import assert_shape
from PIL import Image

from demo.tools.ui_tools import DISPLAY_KEY
from demo.tools.video_tools import build_video_tools
from demo.vision import FakeVisionClient

KNOWN_PHRASE = "the quick brown fox"
KNOWN_DESCRIPTION = "a solid red background"
FRAME_COLOR = (200, 30, 30)


def _build_video(seconds: int = 9, fps: int = 2, sample_rate: int = 16_000) -> bytes:
    """A tiny mp4: `seconds * fps` solid-red frames, plus a silent audio track."""
    buffer = io.BytesIO()
    container = av.open(buffer, mode="w", format="mp4")
    try:
        video_stream = container.add_stream("mpeg4", rate=fps)
        video_stream.width = 32
        video_stream.height = 32
        video_stream.pix_fmt = "yuv420p"

        audio_stream = container.add_stream("aac", rate=sample_rate)
        audio_stream.layout = "mono"

        for _ in range(seconds * fps):
            frame = av.VideoFrame.from_image(
                Image.new("RGB", (32, 32), color=FRAME_COLOR)
            )
            for packet in video_stream.encode(frame):
                container.mux(packet)
        for packet in video_stream.encode():
            container.mux(packet)

        frame_size = audio_stream.codec_context.frame_size or 1024
        samples_needed = sample_rate * seconds
        written, pts = 0, 0
        while written < samples_needed:
            count = min(frame_size, samples_needed - written)
            audio_frame = av.AudioFrame(format="s16", layout="mono", samples=count)
            audio_frame.sample_rate = sample_rate
            audio_frame.pts = pts
            audio_frame.planes[0].update(b"\x00\x00" * count)
            for packet in audio_stream.encode(audio_frame):
                container.mux(packet)
            written += count
            pts += count
        for packet in audio_stream.encode():
            container.mux(packet)
    finally:
        container.close()
    return buffer.getvalue()


def _video_without_audio() -> bytes:
    buffer = io.BytesIO()
    container = av.open(buffer, mode="w", format="mp4")
    try:
        video_stream = container.add_stream("mpeg4", rate=2)
        video_stream.width = 16
        video_stream.height = 16
        video_stream.pix_fmt = "yuv420p"
        frame = av.VideoFrame.from_image(Image.new("RGB", (16, 16), color=FRAME_COLOR))
        for packet in video_stream.encode(frame):
            container.mux(packet)
        for packet in video_stream.encode():
            container.mux(packet)
    finally:
        container.close()
    return buffer.getvalue()


VIDEO_BYTES = _build_video()


@pytest.fixture
def voice_service(monkeypatch):
    """A fake `demo-voice-service` and a fake video host behind one transport.

    Records every request in `seen`, the same recording-`MockTransport`
    pattern `test_process_tools.py` uses for `process-service`.
    """
    seen: list[httpx.Request] = []
    state = {
        "video_bytes": VIDEO_BYTES,
        "transcript": KNOWN_PHRASE,
        "transcribe_status": 200,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        url = str(request.url)
        if request.method == "GET" and "video.mp4" in url:
            return httpx.Response(200, content=state["video_bytes"])
        if request.method == "GET" and "missing.mp4" in url:
            return httpx.Response(404, json={"detail": "not found"})
        if request.method == "POST" and url.endswith("/transcribe"):
            if state["transcribe_status"] != 200:
                return httpx.Response(
                    state["transcribe_status"], json={"detail": "stt down"}
                )
            return httpx.Response(200, json={"text": state["transcript"]})
        if request.method == "POST" and url.endswith("/chat/completions"):
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": KNOWN_DESCRIPTION}}]},
            )
        return httpx.Response(404, json={"detail": f"no route for {url}"})

    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient

    class Patched(original):  # type: ignore[misc]
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", Patched)
    return {"seen": seen, "state": state}


def tools(vision=None, **kwargs):
    vision = vision or FakeVisionClient(description=KNOWN_DESCRIPTION)
    built = build_video_tools("http://voice.test", vision_client=vision, **kwargs)
    return {t.name: t for t in built}, vision


def _payload(answer) -> dict:
    """The structured `video-analysis` artifact carried in `answer`'s display
    payload.
    """
    return json.loads(answer.additional_properties[DISPLAY_KEY])


def test_without_a_voice_service_there_is_no_tool():
    # The model must not be shown a tool that cannot work: it would call it.
    assert build_video_tools("", vision_client=FakeVisionClient()) == []


@pytest.mark.asyncio
async def test_the_default_vision_client_uses_the_dedicated_vision_model(
    voice_service, monkeypatch
):
    # No `vision_client` passed: `build_video_tools` must build its own
    # `HttpVisionClient`, pointed at `Settings.vision_model` -- not
    # `Settings.model`, which is the conversation's own model.
    monkeypatch.setenv("DEMO_VISION_MODEL", "some/dedicated-vision-model")
    monkeypatch.setenv("OPENAI_CHAT_COMPLETION_MODEL", "some/conversation-model")

    built = {t.name: t for t in build_video_tools("http://voice.test")}
    answer = await built["analyze_video"].func(video_url="http://files.test/video.mp4")

    payload = _payload(answer)
    assert payload["description"] == KNOWN_DESCRIPTION

    vision_request = next(
        r for r in voice_service["seen"] if r.url.path.endswith("/chat/completions")
    )
    body = json.loads(vision_request.content)
    assert body["model"] == "some/dedicated-vision-model"
    assert body["model"] != "some/conversation-model"
    # The real `HttpVisionClient` goes through the native `video_url` path by
    # default too, not just the fake used elsewhere in this file.
    content = body["messages"][0]["content"]
    assert any(part["type"] == "video_url" for part in content)


@pytest.mark.asyncio
async def test_native_video_is_preferred_and_receives_the_whole_clip(voice_service):
    # The default (a model that accepts video): `analyze_video` must not
    # fall back to sampling a handful of frames when it doesn't have to.
    built, vision = tools()

    await built["analyze_video"].func(video_url="http://files.test/video.mp4")

    assert vision.calls == []
    assert len(vision.video_calls) == 1
    video_bytes, mime_type, question = vision.video_calls[0]
    assert video_bytes == VIDEO_BYTES
    assert mime_type == "video/mp4"
    assert question == ""


@pytest.mark.asyncio
async def test_falls_back_to_sampled_frames_when_the_model_has_no_video_support(
    voice_service,
):
    # A model configured without video input (`describe_video` raises, the
    # same way a real endpoint would answer 4xx to an unsupported modality)
    # must not break the turn -- it must still get a real answer, just from
    # sampled frames instead of the whole clip.
    vision = FakeVisionClient(description=KNOWN_DESCRIPTION, supports_video=False)
    built, vision = tools(vision=vision, frame_interval_seconds=4.0, max_frames=4)

    answer = await built["analyze_video"].func(video_url="http://files.test/video.mp4")

    payload = _payload(answer)
    assert payload["description"] == KNOWN_DESCRIPTION
    assert vision.video_calls == []
    assert len(vision.calls) == 1


@pytest.mark.asyncio
async def test_the_answer_cites_both_what_was_said_and_what_was_seen(voice_service):
    built, _vision = tools()

    answer = await built["analyze_video"].func(video_url="http://files.test/video.mp4")

    payload = _payload(answer)
    assert payload["transcript"] == KNOWN_PHRASE
    assert payload["description"] == KNOWN_DESCRIPTION


@pytest.mark.asyncio
async def test_the_transcript_and_description_reach_the_model_too_not_just_the_artifact(
    voice_service,
):
    # So a follow-up question about the same video can be answered from the
    # model's own context, without calling `analyze_video` again.
    built, _vision = tools()

    answer = await built["analyze_video"].func(video_url="http://files.test/video.mp4")

    assert KNOWN_PHRASE in answer.text
    assert KNOWN_DESCRIPTION in answer.text


@pytest.mark.asyncio
async def test_the_answer_is_a_structured_artifact_matching_the_shared_contract(
    voice_service,
):
    built, _vision = tools()

    answer = await built["analyze_video"].func(video_url="http://files.test/video.mp4")

    payload = _payload(answer)
    assert payload["component"] == "video-analysis"
    assert payload["video_url"] == "http://files.test/video.mp4"
    assert payload["id"]
    assert_shape("agui/tool-result-video-analysis", payload)


@pytest.mark.asyncio
async def test_real_frames_reach_the_vision_client_in_the_fallback_path(voice_service):
    vision = FakeVisionClient(description=KNOWN_DESCRIPTION, supports_video=False)
    built, vision = tools(vision=vision, frame_interval_seconds=4.0, max_frames=4)

    await built["analyze_video"].func(video_url="http://files.test/video.mp4")

    assert len(vision.calls) == 1
    images, question = vision.calls[0]
    # A 9s/2fps clip at a 4s interval: frames at t=0, 4, 8 -- three, not one.
    assert len(images) == 3
    # Each frame is a real, distinct JPEG -- not an empty placeholder.
    assert all(image.startswith(b"\xff\xd8\xff") for image in images)
    assert question == ""


@pytest.mark.asyncio
async def test_the_question_is_passed_to_the_vision_client(voice_service):
    built, vision = tools()

    await built["analyze_video"].func(
        video_url="http://files.test/video.mp4",
        question="what color is the background?",
    )

    assert vision.video_calls[0][2] == "what color is the background?"


@pytest.mark.asyncio
async def test_the_transcribe_call_carries_a_real_audio_file(voice_service):
    built, _vision = tools()

    await built["analyze_video"].func(video_url="http://files.test/video.mp4")

    transcribe_request = next(
        r for r in voice_service["seen"] if r.url.path == "/transcribe"
    )
    assert b"audio.wav" in transcribe_request.content
    assert b"RIFF" in transcribe_request.content  # a real WAV, muxed from the track


@pytest.mark.asyncio
async def test_a_video_with_no_audio_track_still_describes_what_is_shown(voice_service):
    voice_service["state"]["video_bytes"] = _video_without_audio()
    built, vision = tools()

    answer = await built["analyze_video"].func(video_url="http://files.test/video.mp4")

    payload = _payload(answer)
    assert payload["transcript"] == ""
    assert payload["description"] == KNOWN_DESCRIPTION
    assert len(vision.video_calls) == 1
    assert not any(r.url.path == "/transcribe" for r in voice_service["seen"])


@pytest.mark.asyncio
async def test_an_unreachable_transcription_service_still_reports_what_was_seen(
    voice_service,
):
    voice_service["state"]["transcribe_status"] = 503
    built, _vision = tools()

    answer = await built["analyze_video"].func(video_url="http://files.test/video.mp4")

    payload = _payload(answer)
    assert payload["transcript"] == ""
    assert payload["description"] == KNOWN_DESCRIPTION


@pytest.mark.asyncio
async def test_a_video_that_cannot_be_downloaded_does_not_break_the_turn(voice_service):
    built, vision = tools()

    answer = await built["analyze_video"].func(video_url="http://files.test/missing.mp4")

    assert "could not download" in answer.text
    assert vision.calls == []
    assert vision.video_calls == []


@pytest.mark.asyncio
async def test_nothing_is_left_on_disk_after_the_call(
    voice_service, tmp_path, monkeypatch
):
    import tempfile

    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    built, _vision = tools()

    await built["analyze_video"].func(video_url="http://files.test/video.mp4")

    assert list(tmp_path.iterdir()) == []
