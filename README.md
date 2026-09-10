# demo-voice-service

Real-time voice service for the laboratory -- scaffold only.

This repo currently holds the skeleton the future Pipecat voice pipeline
(WebSocket audio in, VAD, streaming STT, a turn handed to
`demo-master-agent`'s existing AG-UI endpoint, streaming TTS, audio out) will
be built on top of: `uv`-managed dependencies, a FastAPI app with health
probes, and a non-root Docker image, following the same conventions as
`demo-process-service` and `demo-memory-service`. No Pipecat, STT, TTS, or
state of its own lands in this repo yet.

## Run locally

```
uv sync
uv run python -m voice_service
```

Then:

```
curl http://127.0.0.1:8500/health/live
curl http://127.0.0.1:8500/health/ready
```

Stop with Ctrl+C; the process shuts down cleanly.

## Test

```
uv run pytest
```

## Environment

| Variable | Default | What it decides |
|---|---|---|
| `VOICE_PORT` | `8500` | Where the service listens when started locally. |
| `VOICE_JSON_LOGS` | `false` | Structured logs for a collector instead of the readable line. |

## Docker

```
docker build -t demo-voice-service .
docker run --rm -p 8500:8500 demo-voice-service
```

The image runs as a non-root user (`service`, uid 10001), the same pattern
used by the other services in the laboratory.

## Why `__main__.py` builds the loop by hand

`demo-process-service` would not start on Windows: `uvicorn.run(...)` builds
its own event loop, which on Windows defaults to the `ProactorEventLoop`, and
that service's Postgres driver needed the selector loop instead. The fix was
to set `asyncio.WindowsSelectorEventLoopPolicy()` before touching uvicorn, and
to drive uvicorn through `uvicorn.Config` + `asyncio.run(uvicorn.Server(...).serve())`
instead of `uvicorn.run(...)`, which would silently build a fresh
(Proactor) loop itself.

This scaffold has no database, so nothing forces the selector loop yet -- but
the pipeline this repo exists for (Pipecat: WebSocket audio, VAD, STT/TTS)
will need the same selector loop for its own async sockets on Windows. The
same pattern is applied here from the start, so that the pipeline lands on
ground that already works, instead of rediscovering this failure mode the
way `demo-process-service` did.

## What comes next

See `../piani/2026-09-10-audio-video.md`, Tappa 1, for the pipeline that gets
built on top of this scaffold in later work.
