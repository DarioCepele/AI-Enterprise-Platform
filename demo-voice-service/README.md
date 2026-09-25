# demo-voice-service

Real-time voice service for the laboratory.

This repo runs a Pipecat voice pipeline on `/ws/voice`: WebSocket audio in,
VAD (Silero), streaming STT (faster-whisper), a turn handed to
`demo-master-agent`'s existing AG-UI endpoint, streaming TTS (Kokoro-82M),
and audio out -- with barge-in and configurable endpointing. It is built on
top of: `uv`-managed dependencies, a FastAPI app with health probes, and a
non-root Docker image, following the same conventions as
`demo-process-service` and `demo-memory-service`.

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

This service has no database, so nothing forces the selector loop for that
reason -- but the Pipecat pipeline (WebSocket audio, VAD, STT/TTS) needs the
same selector loop for its own async sockets on Windows. The same pattern
was applied here from the start, so that the pipeline landed on ground that
already worked, instead of rediscovering this failure mode the way
`demo-process-service` did.

## RAM measured with the full pipeline loaded (Tappa 1, Step 7)

One observed data point from this development machine, not a hard
requirement -- the same way `voice_service.tts`'s own docstring reports
Kokoro's non-determinism as something measured directly, not assumed.

With all three lazy-loaded models resident in one process at once (Silero
VAD, faster-whisper `small` int8/CPU, and Kokoro-82M -- each forced to load
by actually calling it once, the same "run at least one full turn" idea as
the plan's Step 7 asks for), `Get-Process -Id <this process's pid> |
Select-Object WorkingSet64, PrivateMemorySize64` was run against that same,
still-running process from PowerShell (see `scripts/measure_resources.py`,
run with `uv run python scripts/measure_resources.py`):

| | Measured |
|---|---|
| Working set | ~1.46 GB (1455.6 MB) |
| Private bytes | ~4.1 GB (4211 MB) |

VRAM: not measured, and not applicable here. This machine does have an
NVIDIA GPU (`nvidia-smi` reports a GeForce RTX 2050, 4096 MiB), but
`torch.cuda.is_available()` is `False` in this project's environment -- the
PyTorch build these dependencies pull in is CPU-only, matching the plan's
"CPU-only, nessuna GPU obbligatoria" decision
(`../piani/2026-09-10-audio-video.md`, Tappa 1). Nothing this service does
ever touches the GPU, so there is no real VRAM figure to report for it;
this section does not invent one.

This is a single run on one Windows development machine, with whatever else
happened to already be resident at the time -- not a number this service is
held to, and not guaranteed to reproduce exactly on another machine. Re-run
`scripts/measure_resources.py` on a given machine before relying on it
there.

## What comes next

See `../piani/2026-09-10-audio-video.md`, Tappa 1, for the pipeline that gets
built on top of this scaffold in later work.
