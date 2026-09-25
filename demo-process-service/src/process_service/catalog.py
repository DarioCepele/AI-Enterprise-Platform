"""Definitions are files in a folder, like the skills of the master agent.

Loaded once at startup, all of them: a broken definition stops the boot instead
of surfacing at the first instance, which is to say in front of whoever was
using the process.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from .definitions import DefinitionError, ProcessDefinition, parse_definition

logger = logging.getLogger(__name__)

DEFAULT_FOLDER = Path(__file__).resolve().parent.parent.parent / "processes"


class Catalog:
    """Every definition, by id and version."""

    def __init__(self, definitions: list[ProcessDefinition]) -> None:
        self._by_key = {definition.key(): definition for definition in definitions}

    def all(self) -> list[ProcessDefinition]:
        return sorted(self._by_key.values(), key=lambda definition: definition.key())

    def get(self, process_id: str, version: int) -> ProcessDefinition:
        """One exact version: a running instance keeps the one it started with."""
        try:
            return self._by_key[(process_id, version)]
        except KeyError:
            raise KeyError(
                f"process '{process_id}' version {version} is not in the catalogue. "
                f"Known: {self._known()}"
            ) from None

    def latest(self, process_id: str) -> ProcessDefinition:
        """The version a new instance starts on."""
        versions = [
            definition
            for key, definition in self._by_key.items()
            if key[0] == process_id
        ]
        if not versions:
            raise KeyError(
                f"process '{process_id}' is not in the catalogue. "
                f"Known: {self._known()}"
            )
        return max(versions, key=lambda definition: definition.version)

    def _known(self) -> str:
        names = (f"{name}@{version}" for name, version in sorted(self._by_key))
        return ", ".join(names) or "none"


def load_catalog(folder: Path | str = DEFAULT_FOLDER) -> Catalog:
    directory = Path(folder)
    definitions: list[ProcessDefinition] = []
    seen: dict[tuple[str, int], Path] = {}

    for path in sorted(directory.glob("*.y*ml")):
        definition = _read(path)
        if definition.key() in seen:
            raise DefinitionError(
                f"{path.name}: process '{definition.id}' version {definition.version} "
                f"is already defined in {seen[definition.key()].name}"
            )
        seen[definition.key()] = path
        definitions.append(definition)

    logger.info(
        "Process catalogue: %d definitions from %s.", len(definitions), directory
    )
    return Catalog(definitions)


def _read(path: Path) -> ProcessDefinition:
    try:
        payload: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        raise DefinitionError(f"{path.name}: not readable as YAML ({error})") from error

    if not isinstance(payload, dict):
        raise DefinitionError(f"{path.name}: a definition has to be a mapping")

    try:
        return parse_definition(payload)
    except DefinitionError as error:
        raise DefinitionError(f"{path.name}: {error}") from error
