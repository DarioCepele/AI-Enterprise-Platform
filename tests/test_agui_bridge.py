"""The transcript-to-AG-UI bridge, against a real `demo-master-agent` run.

`demo-master-agent` is started as a subprocess, from its own repo and its
own virtualenv (a sibling of this one), with `DEMO_FAKE_CLIENT=true` -- the
same offline/deterministic client its own tests use
(`demo-master-agent/tests/test_fake_client.py`,
`demo-master-agent/src/demo/chat_clients/fake.py`): no network call, no API
key, no flakiness. With no chat client passed in, `build_master_agent()`
picks `FakeStreamingChatClient()` with its default chunks
(`["I am ", "working ", "on the ", "answer."]`), so any turn answers
"I am working on the answer." -- fixed and known ahead of time.

`DEMO_POSTGRES_DSN` and `DEMO_MEMORY_SERVICE_URL` are forced empty for the
subprocess regardless of `demo-master-agent/.env`: this test needs neither a
shared-state Postgres nor a memory service, and `python-dotenv`'s
`load_dotenv()` (called at import in `demo/config.py`) does not override a
variable already present in the environment -- so setting it here, even to
"", wins over the `.env` file.

This is a real subprocess (uvicorn serving `demo.server.app:create_app`),
not the in-process ASGI-transport pattern `demo-master-agent`'s own tests
use for its `app` fixture: that pattern needs `demo-master-agent`'s package
and its dependencies (`agent-framework`, `a2a-sdk`, `psycopg`,
`opentelemetry`, ...) importable from *this* project's interpreter, which
would mean adding `demo-master-agent` as a dependency of
`demo-voice-service` -- out of scope for this contract's `pyproject.toml`
allowance (only a missing HTTP client). A subprocess, run with
`demo-master-agent`'s own already-synced virtualenv, needs none of that.
"""
from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import time
from pathlib import Path

import httpx
import pytest

from voice_service.agui_client import AGUIBridgeClient

FIXED_TRANSCRIPT = "what is the status of my plan today"
# FakeStreamingChatClient()'s own default chunks, joined -- see
# demo-master-agent/src/demo/chat_clients/fake.py: DEFAULT_CHUNKS.
EXPECTED_REPLY = "I am working on the answer."

STARTUP_TIMEOUT_S = 30.0


def _master_agent_repo() -> Path:
    """The sibling repo, as the contract names it: `<...>/demo/demo-master-agent`."""
    return Path(__file__).resolve().parents[2] / "demo-master-agent"


def _master_agent_python(repo: Path) -> Path:
    """The interpreter `demo-master-agent` is already synced into.

    Its dependencies (agent-framework, a2a-sdk, psycopg, opentelemetry, ...)
    live only in that virtualenv, not in this project's -- this is not the
    interpreter running this test.
    """
    windows_python = repo / ".venv" / "Scripts" / "python.exe"
    posix_python = repo / ".venv" / "bin" / "python"
    return windows_python if windows_python.exists() else posix_python


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture
async def master_agent_url():
    """Starts `demo-master-agent` for real, with the fake client, and tears it down."""
    repo = _master_agent_repo()
    python = _master_agent_python(repo)
    if not python.exists():
        pytest.skip(
            f"demo-master-agent has no synced .venv at {repo} "
            "(expected a sibling repo with `uv sync` already run)."
        )

    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"

    env = dict(os.environ)
    env["DEMO_FAKE_CLIENT"] = "true"
    # Forced empty regardless of demo-master-agent/.env: see module docstring.
    env["DEMO_POSTGRES_DSN"] = ""
    env["DEMO_MEMORY_SERVICE_URL"] = ""
    env["DEMO_KNOWLEDGE_AGENT_URL"] = ""
    env["DEMO_SUBAGENTS"] = ""

    process = subprocess.Popen(
        [
            str(python),
            "-m",
            "uvicorn",
            "demo.server.app:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=str(repo),
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


async def _wait_until_ready(base_url: str, process: subprocess.Popen) -> None:
    deadline = time.monotonic() + STARTUP_TIMEOUT_S
    async with httpx.AsyncClient(timeout=2.0) as client:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                output = process.stdout.read() if process.stdout else ""
                raise RuntimeError(
                    f"demo-master-agent exited early (code {process.returncode}):\n{output}"
                )
            try:
                response = await client.get(f"{base_url}/health/live")
                if response.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.3)
    process.kill()
    raise RuntimeError(f"demo-master-agent did not become ready within {STARTUP_TIMEOUT_S}s")


async def test_a_fixed_transcript_gets_a_nonempty_predictable_reply(master_agent_url):
    """The bridge, against a real run: a fixed turn, an assistant_text-ready reply.

    Also checks thread continuity: a second turn on the same bridge (one AG-UI
    thread per voice session, see `AGUIBridgeClient`) still gets answered --
    one subprocess covers both checks, keeping the suite's runtime down.
    """
    bridge = AGUIBridgeClient(master_agent_url)

    reply = await bridge.send_turn(FIXED_TRANSCRIPT)
    assert reply == EXPECTED_REPLY

    second_reply = await bridge.send_turn("a second, unrelated turn")
    assert second_reply == EXPECTED_REPLY


async def test_stream_turn_yields_chunks_that_join_back_to_the_same_reply(master_agent_url):
    """`stream_turn` (added for Step 4 of the plan's Tappa 1 -- feeding TTS as
    text arrives) against the same real run: it must yield at least one
    chunk, and joining every chunk it yields must reproduce exactly what
    `send_turn` returns for the same input -- the property `send_turn`'s own
    docstring relies on, now checked directly against a real AG-UI stream
    instead of only reasoned about.
    """
    bridge = AGUIBridgeClient(master_agent_url)

    chunks = [chunk async for chunk in bridge.stream_turn(FIXED_TRANSCRIPT)]
    assert len(chunks) >= 1
    assert "".join(chunks) == EXPECTED_REPLY
