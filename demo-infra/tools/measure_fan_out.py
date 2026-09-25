"""Measures what running two agent steps together actually buys.

The claim the engine makes is that independent steps overlap. This runs the
same work twice against the **real** agents -- once with the two steps side by
side, once with one waiting for the other -- and reads the cost back out of the
instance's own history. No numbers are typed into a README by hand.

    docker compose up -d knowledge-agent analysis-agent postgres
    uv run --project ../demo-process-service python tools/measure_fan_out.py

It starts a process service of its own -- inside the compose network, with a
throwaway pair of definitions mounted into it -- so the template's `processes/`
folder stays as it ships and the agents keep the addresses they advertise on
their own cards.
"""
from __future__ import annotations

import asyncio
import json
import os
import statistics
import subprocess
import tempfile
import time
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
SERVICE = HERE.parent.parent / "demo-process-service"

PORT = int(os.getenv("MEASURE_PORT", "8301"))

# One run of each says almost nothing: a model that decides to call a tool one
# more time costs more than the whole difference being measured. The runs
# alternate, so a slow minute on the provider hits both sides.
RUNS = int(os.getenv("MEASURE_RUNS", "5"))
BASE = f"http://127.0.0.1:{PORT}"

# The measuring service runs in the compose network under this name, because an
# agent card advertises the address the agent has **there**: reached from the
# host, the same card sends the client to a name the host cannot resolve.
CONTAINER = "measure-fan-out"
PUBLIC = f"http://{CONTAINER}:8300"

AGENTS = json.dumps(
    {
        "knowledge": "http://knowledge-agent:8200/",
        "analysis": "http://analysis-agent:8400/",
    }
)

DOCUMENTS = "Come gestisce gli errori Rust? Rispondi in tre righe."
NUMBERS = (
    "Misura questa serie di tempi di risposta in ms: 120, 135, 128, 141, 980. "
    "Rispondi in tre righe."
)

TOGETHER = {
    "id": "together",
    "version": 1,
    "name": "Two agents at once",
    "steps": [
        {"id": "docs", "type": "agent", "owner": "knowledge", "input": {"question": DOCUMENTS}},
        {"id": "numbers", "type": "agent", "owner": "analysis", "input": {"question": NUMBERS}},
    ],
}

ONE_AFTER_THE_OTHER = {
    "id": "in-turn",
    "version": 1,
    "name": "The same two, one waiting for the other",
    "steps": [
        {"id": "docs", "type": "agent", "owner": "knowledge", "input": {"question": DOCUMENTS}},
        {
            "id": "numbers",
            "type": "agent",
            "owner": "analysis",
            "depends_on": ["docs"],
            "input": {"question": NUMBERS},
        },
    ],
}


def definitions_folder() -> Path:
    folder = Path(tempfile.mkdtemp(prefix="measure-"))
    for definition in (TOGETHER, ONE_AFTER_THE_OTHER):
        (folder / f"{definition['id']}.yaml").write_text(
            json.dumps(definition, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return folder


async def wait_until_ready(client: httpx.AsyncClient, seconds: float = 60.0) -> None:
    deadline = time.perf_counter() + seconds
    while time.perf_counter() < deadline:
        try:
            answer = await client.get(f"{BASE}/health/ready", timeout=5.0)
            if answer.status_code == 200:
                return
        except httpx.HTTPError:
            pass
        await asyncio.sleep(0.5)
    raise RuntimeError(f"the service did not come up on {BASE}")


async def run_once(client: httpx.AsyncClient, process_id: str) -> dict[str, object]:
    began = time.perf_counter()
    started = await client.post(f"{BASE}/processes/{process_id}/instances", json={"input": {}})
    started.raise_for_status()
    instance_id = started.json()["id"]

    status = "running"
    while status in ("running", "pending", "waiting"):
        await asyncio.sleep(0.5)
        read = await client.get(f"{BASE}/instances/{instance_id}", timeout=30.0)
        status = read.json()["status"]
        if time.perf_counter() - began > 300:
            raise RuntimeError(f"instance {instance_id} is still {status} after five minutes")
    took = time.perf_counter() - began

    events = (await client.get(f"{BASE}/instances/{instance_id}/events")).json()["events"]
    spent = [event for event in events if event["kind"] == "step_usage"]
    return {
        "process": process_id,
        "status": status,
        "seconds": round(took, 2),
        "rounds": sum(int(event["data"].get("rounds", 0)) for event in spent),
        "input_tokens": sum(int(event["data"].get("input_tokens", 0)) for event in spent),
        "output_tokens": sum(int(event["data"].get("output_tokens", 0)) for event in spent),
        "agents": sorted({str(event["data"].get("agent")) for event in spent}),
    }


async def measure() -> dict[str, list[dict[str, object]]]:
    async with httpx.AsyncClient(timeout=60.0) as client:
        await wait_until_ready(client)
        runs: dict[str, list[dict[str, object]]] = {"together": [], "in-turn": []}
        for attempt in range(RUNS):
            for process_id in ("together", "in-turn"):
                result = await run_once(client, process_id)
                runs[process_id].append(result)
                print(f"  {attempt + 1}/{RUNS} {result}", flush=True)
        return runs


def main() -> None:
    folder = definitions_folder()
    command = [
        "docker",
        "compose",
        "run",
        "--rm",
        "--detach",
        "--name",
        CONTAINER,
        "--publish",
        f"127.0.0.1:{PORT}:8300",
        "--volume",
        f"{folder}:/measure",
        "--env",
        "PROCESS_DEFINITIONS_PATH=/measure",
        "--env",
        f"PROCESS_PUBLIC_URL={PUBLIC}",
        "--env",
        f"PROCESS_AGENTS={AGENTS}",
        "process-service",
    ]
    print(f"Definitions in {folder}, service on {BASE} as '{CONTAINER}'", flush=True)
    subprocess.run(command, cwd=str(HERE.parent), check=True, stdout=subprocess.DEVNULL)
    try:
        runs = asyncio.run(measure())
    finally:
        subprocess.run(
            ["docker", "rm", "-f", CONTAINER],
            cwd=str(HERE.parent),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    together = runs["together"]
    in_turn = runs["in-turn"]

    def line(name: str, key: str, unit: str = "") -> None:
        here = [float(run[key]) for run in together]
        there = [float(run[key]) for run in in_turn]
        print(
            f"| {name} "
            f"| {statistics.median(here):.0f}{unit} ({min(here):.0f}-{max(here):.0f}) "
            f"| {statistics.median(there):.0f}{unit} ({min(there):.0f}-{max(there):.0f}) |"
        )

    print()
    print(f"Mediana su {RUNS} giri, fra parentesi il minimo e il massimo.")
    print()
    print("| | insieme | uno dopo l'altro |")
    print("| --- | --- | --- |")
    line("tempo totale", "seconds", " s")
    line("round del modello", "rounds")
    line("token in", "input_tokens")
    line("token out", "output_tokens")


if __name__ == "__main__":
    main()
