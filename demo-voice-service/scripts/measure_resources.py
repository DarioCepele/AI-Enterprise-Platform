"""Step 7 of the plan's Tappa 1: measure this process's *real* RAM once VAD,
STT and TTS are all loaded together.

Run it with:

    uv run python scripts/measure_resources.py

What it does, in order:

1. Forces the lazy-loaded models in `voice_service.vad`, `voice_service.stt`
   and `voice_service.tts` to actually load, by calling each module's public
   function once on real input (the same `speech_en.wav` fixture the test
   suite uses for VAD/STT, plus one short sentence for TTS) -- the same
   "run at least one full turn" approach the contract asks for, without
   needing a running `demo-master-agent` (this script only cares about the
   voice pipeline's own three models, not the AG-UI round trip).
2. Once all three are loaded, reads back *this same, already-running*
   process's own memory from Windows, via `Get-Process -Id <this pid>` --
   the same technique (`Get-Process | Select WorkingSet, PrivateMemorySize`)
   named in this contract and used in an earlier one. This is a real
   external OS measurement of a live process, not an estimate: the numbers
   only exist because a PowerShell `Get-Process` call was actually run
   against this interpreter's own PID while the models were still resident.

No GPU is configured anywhere in this repo or its prior contracts -- there is
no CUDA device for PyTorch/`faster-whisper`/Kokoro to have been placed on, so
there is no VRAM number to report here. This script does not invent one.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import wave
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

import numpy as np  # noqa: E402

from voice_service.stt import transcribe  # noqa: E402
from voice_service.tts import synthesize  # noqa: E402
from voice_service.vad import contains_speech  # noqa: E402

FIXTURE = REPO_ROOT / "tests" / "fixtures" / "speech_en.wav"
WARMUP_SENTENCE = "This is a short warm-up sentence used only to force Kokoro to load."


def _load_pcm16_bytes(path: Path) -> bytes:
    with wave.open(str(path), "rb") as wav_file:
        return wav_file.readframes(wav_file.getnframes())


def _measure_this_process_windows(pid: int) -> dict[str, float]:
    """Asks Windows itself for this PID's memory, via `Get-Process`.

    Returns working-set and private-bytes in MB, rounded to one decimal --
    the same fields (`WorkingSetMB`/`PrivateMB`) named in the contract.
    """
    ps_command = (
        f"Get-Process -Id {pid} | Select-Object "
        "@{N='WorkingSetMB';E={[math]::Round($_.WorkingSet64/1MB,1)}}, "
        "@{N='PrivateMB';E={[math]::Round($_.PrivateMemorySize64/1MB,1)}} "
        "| ConvertTo-Json"
    )
    # Safe: executable and arguments are hardcoded here, the only interpolated
    # value is this process's own PID (an int from os.getpid()) -- no external input.
    result = subprocess.run(  # noqa: S603
        ["powershell", "-NoProfile", "-Command", ps_command],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def main() -> None:
    audio_bytes = _load_pcm16_bytes(FIXTURE)
    samples = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0

    print("Loading VAD (Silero) ...")
    contains_speech(samples[: 16_000 * 1], 16_000)

    print("Loading STT (faster-whisper 'small', int8, CPU) ...")
    transcribe(FIXTURE)

    print("Loading TTS (Kokoro-82M) ...")
    synthesize(WARMUP_SENTENCE)

    pid = os.getpid()
    print(
        f"All three models loaded in process pid={pid}. Measuring via Get-Process ..."
    )
    stats = _measure_this_process_windows(pid)
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
