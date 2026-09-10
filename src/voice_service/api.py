"""The HTTP surface of the voice service: health probes, and the pipeline.

This service is a Pipecat pipeline (WebSocket audio in, VAD, STT, a turn
handed to `demo-master-agent`'s AG-UI endpoint, TTS, audio out) built up in
stages. The turn now reaches `demo-master-agent`: once a
`TranscriptionFrame` closes a turn, its text goes to that agent's AG-UI
endpoint as a normal user message, and the assistant's reply streams back
over this same WebSocket -- as text captions and as synthesized speech,
chunk by chunk, per Step 4/5 of the plan
(`piani/2026-09-10-audio-video.md`, Tappa 1): each sentence
`voice_service.agui_client.AGUIBridgeClient.stream_turn` yields is handed to
`voice_service.tts.synthesize` and sent on as soon as it is ready, not
buffered until the whole reply is in. Once a real dependency (a session
store) lands, `/health/ready` should start checking it -- the way
`process-service` and `memory-service` check Postgres before answering ok.

`POST /transcribe` is a separate, independent surface: a batch "one audio
file in, one transcript out" endpoint for tools (e.g. a future video-analysis
tool in `demo-master-agent`) that already have a whole file and just want it
transcribed, reusing `voice_service.stt.transcribe` directly -- no VAD, no
streaming, no Pipecat pipeline involved.
"""
from __future__ import annotations

import asyncio
import logging
import tempfile
import uuid
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from pipecat.frames.frames import EndFrame, InputAudioRawFrame, TranscriptionFrame
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.workers.runner import WorkerRunner

from .agui_client import AGUIBridgeClient
from .config import Settings, get_settings
from .pipeline import SAMPLE_RATE, build_voice_pipeline
from .stt import transcribe as transcribe_speech
from .tts import SAMPLE_RATE as TTS_SAMPLE_RATE
from .tts import synthesize as synthesize_speech

logger = logging.getLogger(__name__)

SERVICE_NAME = "voice-service"


def create_app(settings: Settings | None = None) -> FastAPI:
    """Builds the app. `settings` is passed in tests."""
    config = settings or get_settings()

    app = FastAPI(title="Voice service")

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        """Whether this process is stuck. Nothing behind this call can fail yet."""
        return {"status": "alive"}

    @app.get("/health")
    @app.get("/health/ready")
    async def ready() -> dict[str, str]:
        """Whether it can serve.

        No backend to check yet -- this scaffold has none. This is the seam
        a future dependency plugs into, not a promise that there is nothing
        to check.
        """
        return {"status": "ok"}

    @app.post("/transcribe")
    async def transcribe(file: UploadFile = File(...)) -> dict[str, str]:
        """Transcribes one whole audio file, uploaded as `multipart/form-data`.

        Distinct from `/ws/voice`: this is a batch, "one file in, one
        transcript out" path -- no streaming, no turn-taking, no VAD, no
        Pipecat pipeline. It is meant for a tool that already has a whole
        audio file (e.g. `demo-master-agent`'s future video-analysis tool,
        handing it a video's extracted audio track) and just wants text back,
        reusing this service's already-built STT engine instead of standing
        up its own.

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
        audio_bytes = await file.read()
        if not audio_bytes:
            raise HTTPException(status_code=422, detail="Uploaded file is empty")

        suffix = Path(file.filename).suffix if file.filename else ".wav"
        tmp_path = Path(tempfile.gettempdir()) / f"voice-service-transcribe-{uuid.uuid4().hex}{suffix}"
        try:
            tmp_path.write_bytes(audio_bytes)
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

        Barge-in (Tappa 2 Step 1 of the plan): each `TranscriptionFrame`
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

        Known limit (verified against `demo-master-agent/src/demo/server/app.py`
        and the `agent_framework_ag_ui` package it uses): `demo-master-agent`'s
        AG-UI endpoint exposes exactly one route, `POST /agui` -- there is no
        cancel/abort endpoint, and `AGUIRequest` carries no run-cancellation
        field. So a superseded turn only stops *this* service from receiving,
        relaying, and synthesizing further -- it does not stop
        `demo-master-agent`'s own run, which was already started and may
        keep computing (and consuming LLM tokens) until it finishes on its
        own. Not a bug to fix here (`demo-master-agent` is out of scope for
        this contract, read-only) -- a limit to know about.
        """
        await websocket.accept()

        pipeline = build_voice_pipeline(
            analysis_window_s=config.analysis_window_s,
            silence_threshold_s=config.silence_threshold_s,
        )
        worker = PipelineWorker(pipeline, params=PipelineParams(audio_in_sample_rate=SAMPLE_RATE))
        worker.add_reached_downstream_filter((TranscriptionFrame,))
        bridge = AGUIBridgeClient(config.master_agent_url)

        # Barge-in bookkeeping: `current_turn` names the newest turn: each
        # TranscriptionFrame bumps it before doing anything else. `send_lock`
        # only serializes writes onto `websocket` -- concurrent turns can
        # otherwise interleave their `send_json`/`send_bytes` calls, since
        # each turn is handled by its own concurrently-running task (see the
        # docstring above).
        current_turn = 0
        send_lock = asyncio.Lock()

        @worker.event_handler("on_frame_reached_downstream")
        async def _send_transcript(_worker: PipelineWorker, frame) -> None:
            nonlocal current_turn
            if not isinstance(frame, TranscriptionFrame):
                return

            current_turn += 1
            my_turn = current_turn

            async with send_lock:
                await websocket.send_json({"type": "user_transcript", "text": frame.text})

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

                    # Kokoro inference is CPU-bound and synchronous -- keep it off the event loop.
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
                    "Turn not answered by %s: the transcript was sent, the reply was not.",
                    config.master_agent_url,
                    exc_info=True,
                )
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
                        InputAudioRawFrame(audio=audio_bytes, sample_rate=SAMPLE_RATE, num_channels=1)
                    )
        except WebSocketDisconnect:
            pass
        finally:
            await worker.queue_frame(EndFrame())
            await runner_task

    logger.info("Voice service ready on port %d.", config.port)
    return app
