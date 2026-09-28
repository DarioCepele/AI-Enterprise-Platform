"""Load the shared contracts, and name who breaks when they stop matching.

The samples live in one place in the repository (`CONTRACTS_DIR`, by default
the `contracts` folder of the infrastructure directory next to the services):
they are the only copy, so the two sides of a contract cannot drift apart while
both keep passing their own tests.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

LOCATION_VARIABLES = ("CONTRACTS_DIR", "AGUI_LAB_CONTRACTS")
INFRA_CANDIDATES = ("infra", "demo-infra")


def _default_location() -> Path | None:
    """The contracts folder, found by walking up from the current directory."""
    for folder in (Path.cwd(), *Path.cwd().parents):
        for infra in INFRA_CANDIDATES:
            candidate = folder / infra / "contracts"
            if candidate.is_dir():
                return candidate
    return None


def contracts_dir() -> Path:
    configured = next((os.environ[v] for v in LOCATION_VARIABLES if os.getenv(v)), "")
    directory = Path(configured) if configured else _default_location()
    if directory is None or not directory.is_dir():
        raise FileNotFoundError(
            f"shared contracts not found in {directory or 'infra/contracts'}. "
            f"Run the tests from inside the repository, or set {LOCATION_VARIABLES[0]} "
            "to the contracts directory. These tests do not skip themselves: a "
            "contract test that goes quiet when the other side is missing is the "
            "silence the contracts exist to remove."
        )
    return directory


def load(name: str) -> dict[str, Any]:
    directory = contracts_dir()
    path = directory / f"{name}.json"
    if not path.is_file():
        known = sorted(
            str(found.relative_to(directory)).replace("\\", "/").removesuffix(".json")
            for found in directory.rglob("*.json")
        )
        raise FileNotFoundError(f"unknown contract '{name}'. Known: {', '.join(known)}")
    loaded: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return loaded


def sample(name: str) -> Any:
    return load(name)["sample"]


def assert_shape(name: str, payload: Any, *, at: str = "") -> None:
    """Check that `payload` has the keys the contract declares, and no others.

    Keys are the contract; values are only there to make the sample readable.
    """
    contract = load(name)
    _compare(contract, contract["sample"], payload, at)


def _compare(contract: Mapping[str, Any], expected: Any, actual: Any, at: str) -> None:
    where = at or "the payload"
    if isinstance(expected, Mapping):
        assert isinstance(actual, Mapping), _message(
            contract, f"{where} is not an object"
        )
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        assert not missing and not extra, _message(
            contract,
            f"{where}: missing {missing or 'nothing'}, unexpected {extra or 'nothing'}",
        )
        for key, value in expected.items():
            _compare(contract, value, actual[key], f"{where}.{key}" if at else key)
        return

    if isinstance(expected, Sequence) and not isinstance(expected, str):
        assert isinstance(actual, Sequence) and not isinstance(actual, str), _message(
            contract, f"{where} is not a list"
        )
        if expected and actual:
            _compare(contract, expected[0], actual[0], f"{where}[0]")


def _message(contract: Mapping[str, Any], detail: str) -> str:
    consumers = ", ".join(contract.get("consumed_by", [])) or "nobody declared"
    return (
        f"contract '{contract['contract']}' v{contract['version']} broken: {detail}.\n"
        f"Consumed by: {consumers}. Update the sample in the contracts folder, "
        "raise its version, and fix every side in the same run."
    )
