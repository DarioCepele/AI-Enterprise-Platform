"""The HTTP surface of the voice service: health probes, and the pipeline.

A Pipecat pipeline -- WebSocket audio in, VAD, speech-to-text, a turn handed to
the master agent's AG-UI endpoint, text-to-speech, audio out. Once a
`TranscriptionFrame` closes a turn, its text goes to the agent as a normal user
message, and the reply streams back over the same WebSocket, as captions and
as speech, sentence by sentence rather than buffered until the end.

`POST /transcribe` is a separate surface: one audio file in, one transcript
out, for tools that already hold a whole file (the master agent's video
analysis) -- no VAD, no streaming, the same speech-to-text engine.

Browsers do not apply CORS to WebSockets, so the `Origin` of every connection
is checked against `VOICE_ALLOWED_ORIGINS`: without it, any page open in the
same browser could talk to the agent through this endpoint.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import tempfile
import uuid
from pathlib import Path

from fastapi import (
    FastAPI,
    File,
    HTTPException,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from pipecat.frames.frames import (
    EndFrame,
    Frame,
    InputAudioRawFrame,
    TranscriptionFrame,
)
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.workers.runner import WorkerRunner
from platform_core.http import BodySizeLimit
from platform_core.observability import configure_logging, configure_telemetry

from .agui_client import AGUIBridgeClient
from .config import Settings, get_settings
from .pipeline import SAMPLE_RATE, build_voice_pipeline
from .stt import transcribe as transcribe_speech
from .tts import SAMPLE_RATE as TTS_SAMPLE_RATE
from .tts import synthesize as synthesize_speech

logger = logging.getLogger(__name__)

SERVICE_NAME = "voice-service"

# What a multipart envelope adds around the audio it carries.
MULTIPART_SLACK = 64 * 1024

CHUNK = 1024 * 1024


def create_app(settings: Settings | None = None) -> FastAPI:
    """Builds the app. `settings` is passed in tests."""
    config = settings or get_settings()
    configure_logging(SERVICE_NAME, as_json=config.json_logs)

    app = FastAPI(title="Voice service")
    configure_telemetry(SERVICE_NAME, app)
    app.add_middleware(
        BodySizeLimit,
        default=MULTIPART_SLACK,
        by_prefix={"/transcribe": config.transcribe_max_bytes + MULTIPART_SLACK},
    )
    allowed_origins = config.origins()

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        """Whether this process is stuck. Nothing behind this call can fail yet."""
        return {"status": "alive"}

    @app.get("/health")
    @app.get("/health/ready")
    async def ready() -> dict[str, str]:
        """Whether it can serve.

        The models load on first use and nothing else is a dependency: ready
        means the process answers. A dependency added later plugs in here.
        """
        return {"status": "ok"}

    @app.post("/transcribe")
    async def transcribe(
        file: UploadFile = File(...),  # noqa: B008 -- FastAPI's documented pattern for multipart uploads
    ) -> dict[str, str]:
        """Transcribes one whole audio file, uploaded as `multipart/form-data`.

        Distinct from `/ws/voice`: this is a batch, "one file in, one
        transcript out" path -- no streaming, no turn-taking, no VAD, no
        Pipecat pipeline. It is meant for a tool that already has a whole
        audio file (the master agent's video analysis, handing it a video's
        audio track) and wants text back.

        Calls `voice_service.stt.transcribe` directly -- the same
        faster-whisper engine `/ws/voice` uses per turn via
        `voice_service.pipeline` -- so there is exactly one transcription
        engine in this service, not two. The uploaded bytes are written to a
        short-lived temp file only because `stt.transcribe` reads from a
        path; that file is removed before this handler returns either way,
        so no uploaded audio persists past the request.

        `file: UploadFile = File(...)` is FastAPI's standard multipart
        upload handling -- it parses the `multipart/form-data` body (via
        `python-multipart`) and rejects anything else (missing/wrong
        content-type, no file part) with a 422 before this body even runs,
        so there is no hand-rolled boundary/part parser to maintain here.
        """
        suffix = Path(file.filename).suffix if file.filename else ".wav"
        tmp_name = f"voice-service-transcribe-{uuid.uuid4().hex}{suffix}"
        tmp_path = Path(tempfile.gettempdir()) / tmp_name
        try:
            # Copied a chunk at a time and counted: the file never sits whole
            # in memory, and one over the ceiling stops being read at once.
            written = 0
            with tmp_path.open("wb") as handle:
                while chunk := await file.read(CHUNK):
                    written += len(chunk)
                    if written > config.transcribe_max_bytes:
                        raise HTTPException(
                            status_code=413,
                            detail=f"audio over {config.transcribe_max_bytes} bytes",
                        )
                    handle.write(chunk)
            if written == 0:
                raise HTTPException(status_code=422, detail="Uploaded file is empty")
            # faster-whisper inference is CPU-bound -- keep it off the event loop.
            text = await asyncio.to_thread(transcribe_speech, tmp_path)
        finally:
            tmp_path.unlink(missing_ok=True)

        return {"text": text}

    @app.websocket("/ws/voice")
    async def voice(websocket: WebSocket) -> None:
        """Streams PCM16 mono 16kHz audio in, sends a transcript and reply per turn.

        Each connection gets its own Pipecat pipeline (VAD endpointing ->
        STT), run through Pipecat's own `PipelineWorker` + `WorkerRunner`.
        Incoming binary frames are queued into the pipeline as they arrive
        (no buffering into a single upload -- this is a live stream); once
        the VAD stage decides a turn has ended, the resulting
        `TranscriptionFrame` is sent back as
        `{"type": "user_transcript", "text": "..."}`, then handed to
        `demo-master-agent`'s AG-UI endpoint as a normal user message. One
        AG-UI thread per connection, so the agent sees the session as one
        conversation across turns, the same as the text chat it also serves.

        The reply streams back as it is generated, one sentence at a time
        (`AGUIBridgeClient.stream_turn`'s own chunking -- see its
        docstring): each sentence goes out as
        `{"type": "assistant_text_chunk", "text": "..."}` for a live
        caption, immediately followed by its synthesized speech -- a
        `{"type": "assistant_audio_chunk", "encoding": "pcm16",
        "sample_rate": 24000}` control message, then one binary WebSocket
        frame carrying that sentence's audio as 16-bit PCM mono bytes (see
        `voice_service.tts` for why 24kHz and not this pipeline's own
        16kHz). A frontend plays each audio frame as it arrives rather than
        waiting for the whole reply, the same way the caption text arrives
        sentence by sentence instead of all at once.

        Barge-in: each `TranscriptionFrame`
        starts a new turn and bumps `current_turn`, a counter closed over by
        this handler. `on_frame_reached_downstream` runs each call as its
        own `asyncio.create_task` (see `pipecat.utils.base_object`'s
        `_call_event_handler` -- it registers this event without `sync=True`),
        so a second turn's handler can start running -- and finish its own
        transcription -- while an earlier turn's handler is still awaiting
        the AG-UI round trip or Kokoro synthesis (both off the event loop:
        the AG-UI stream is network I/O, and Kokoro's inference runs via
        `asyncio.to_thread`). That overlap is exactly what makes barge-in
        possible without any change to how frames flow through the pipeline
        itself. Before sending each `assistant_text_chunk`/
        `assistant_audio_chunk`, the handler checks whether its own turn is
        still `current_turn`; once a newer turn has started, it stops
        sending anything further for the superseded turn and emits
        `{"type": "assistant_turn_cancelled"}` once, so a real client knows
        to drop whatever of that reply it already queued for playback.

        Known limit: the master agent's AG-UI endpoint has no cancel route,
        and the run input carries no cancellation field. A superseded turn
        stops *this* service from relaying and synthesizing further, and the
        stream is closed; the agent's run may still finish on its own.
        """
        origin = (websocket.headers.get("origin") or "").rstrip("/")
        if origin and origin not in allowed_origins:
            logger.warning("Voice connection refused from origin %s.", origin[:80])
            await websocket.close(code=1008)
            return
        await websocket.accept()

        pipeline = build_voice_pipeline(
            analysis_window_s=config.analysis_window_s,
            silence_threshold_s=config.silence_threshold_s,
        )
        worker_params = PipelineParams(audio_in_sample_rate=SAMPLE_RATE)
        worker = PipelineWorker(pipeline, params=worker_params)
        worker.add_reached_downstream_filter((TranscriptionFrame,))
        bridge = AGUIBridgeClient(
            config.master_agent_url, approval_notice=config.approval_notice
        )

        # Barge-in bookkeeping: `current_turn` names the newest turn: each
        # TranscriptionFrame bumps it before doing anything else. `send_lock`
        # only serializes writes onto `websocket` -- concurrent turns can
        # otherwise interleave their `send_json`/`send_bytes` calls, since
        # each turn is handled by its own concurrently-running task (see the
        # docstring above).
        current_turn = 0
        send_lock = asyncio.Lock()

        # pipecat's `event_handler` is itself untyped, so mypy cannot see that
        # the handler below stays typed once decorated.
        @worker.event_handler("on_frame_reached_downstream")  # type: ignore[untyped-decorator]
        async def _send_transcript(_worker: PipelineWorker, frame: Frame) -> None:
            nonlocal current_turn
            if not isinstance(frame, TranscriptionFrame):
                return

            current_turn += 1
            my_turn = current_turn

            async with send_lock:
                await websocket.send_json(
                    {"type": "user_transcript", "text": frame.text}
                )

            reply = bridge.stream_turn(frame.text)
            superseded = False
            try:
                async for sentence in reply:
                    async with send_lock:
                        if my_turn != current_turn:
                            superseded = True
                        else:
                            await websocket.send_json(
                                {"type": "assistant_text_chunk", "text": sentence}
                            )
                    if superseded:
                        break

                    # Kokoro inference is CPU-bound and synchronous -- keep it off
                    # the event loop.
                    audio_bytes = await asyncio.to_thread(synthesize_speech, sentence)
                    if not audio_bytes:
                        continue

                    async with send_lock:
                        if my_turn != current_turn:
                            superseded = True
                        else:
                            await websocket.send_json(
                                {
                                    "type": "assistant_audio_chunk",
                                    "encoding": "pcm16",
                                    "sample_rate": TTS_SAMPLE_RATE,
                                }
                            )
                            await websocket.send_bytes(audio_bytes)
                    if superseded:
                        break
            except Exception:
                logger.error(
                    "Turn not answered by %s: the transcript was sent, the reply was "
                    "not.",
                    config.master_agent_url,
                    exc_info=True,
                )
                # Said to the browser without the cause, which stays in the
                # logs: silence would read as "still thinking".
                with contextlib.suppress(Exception):
                    async with send_lock:
                        await websocket.send_json({"type": "assistant_turn_failed"})
                return
            finally:
                if superseded:
                    # Stop consuming (and close our end of) the AG-UI SSE
                    # stream -- see the docstring's "Known limit" for why
                    # this does not, by itself, stop demo-master-agent's run.
                    await reply.aclose()

            if superseded:
                async with send_lock:
                    await websocket.send_json({"type": "assistant_turn_cancelled"})

        runner = WorkerRunner(handle_sigint=False)
        await runner.add_workers(worker)
        runner_task = asyncio.create_task(runner.run())

        try:
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    break
                audio_bytes = message.get("bytes")
                if audio_bytes:
                    await worker.queue_frame(
                        InputAudioRawFrame(
                            audio=audio_bytes, sample_rate=SAMPLE_RATE, num_channels=1
                        )
                    )
        except WebSocketDisconnect:
            pass
        finally:
            await worker.queue_frame(EndFrame())
            await runner_task

    logger.info("Voice service ready on port %d.", config.port)
    return app
