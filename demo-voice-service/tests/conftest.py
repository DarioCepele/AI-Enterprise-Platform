"""Shared fixtures, discovered by pytest for every module in this directory
without an explicit import -- `master_agent_url` used to be defined in
`test_agui_bridge.py` and re-exported into `test_barge_in.py`/
`test_end_to_end.py` by importing it there, which worked (pytest resolves
fixtures by name in the requesting module's namespace either way) but made
ruff flag every use as F811 ("redefinition") since it cannot tell a test
function's fixture parameter from an actual name collision. `conftest.py`
is pytest's own built-in answer to sharing a fixture across modules; moving
it here removes the import, and the false positive along with it.
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
    env["MASTER_FAKE_CLIENT"] = "true"
    # Forced empty regardless of demo-master-agent/.env: see
    # test_agui_bridge.py's module docstring.
    env["MASTER_POSTGRES_DSN"] = ""
    env["MASTER_MEMORY_SERVICE_URL"] = ""
    env["MASTER_KNOWLEDGE_AGENT_URL"] = ""
    env["MASTER_SUBAGENTS"] = ""

    # Safe: the interpreter path is derived from this file's own location and the
    # rest of the argv is hardcoded (the port is an int from _free_port()).
    process = subprocess.Popen(  # noqa: S603
        [
            str(python),
            "-m",
            "uvicorn",
            "master_agent.server.app:create_app",
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
                    f"demo-master-agent exited early "
                    f"(code {process.returncode}):\n{output}"
                )
            try:
                response = await client.get(f"{base_url}/health/live")
                if response.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.3)
    process.kill()
    raise RuntimeError(
        f"demo-master-agent did not become ready within {STARTUP_TIMEOUT_S}s"
    )
