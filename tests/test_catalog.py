"""The catalogue: definitions are files, and a broken one stops the boot."""
from __future__ import annotations

from pathlib import Path

import pytest

from process_service.catalog import Catalog, load_catalog
from process_service.definitions import DefinitionError

GOOD = """
id: simple
version: 1
name: Simple
steps:
  - id: only
    type: tool
    tool: do_something
"""

NEWER = """
id: simple
version: 2
name: Simple, revised
steps:
  - id: only
    type: tool
    tool: do_something_else
"""


def write(folder: Path, name: str, text: str) -> None:
    (folder / name).write_text(text, encoding="utf-8")


def test_the_definitions_of_the_repository_load(tmp_path: Path):
    catalog = load_catalog(Path("processes"))

    assert "example-approval" in {definition.id for definition in catalog.all()}


def test_a_broken_definition_stops_the_boot_naming_the_file(tmp_path: Path):
    write(tmp_path, "good.yaml", GOOD)
    write(tmp_path, "broken.yaml", "id: broken\nversion: 1\nsteps:\n  - id: x\n    type: tool\n")

    # Refusing at startup instead of at the first instance: the second way fails
    # in front of whoever is using the process.
    with pytest.raises(DefinitionError, match="broken.yaml"):
        load_catalog(tmp_path)


def test_a_file_that_is_not_yaml_is_named_too(tmp_path: Path):
    write(tmp_path, "bad.yaml", "{{{ not yaml")

    with pytest.raises(DefinitionError, match="bad.yaml"):
        load_catalog(tmp_path)


def test_the_latest_version_is_the_one_that_starts(tmp_path: Path):
    write(tmp_path, "v1.yaml", GOOD)
    write(tmp_path, "v2.yaml", NEWER)

    catalog = load_catalog(tmp_path)

    assert catalog.latest("simple").version == 2


def test_an_old_version_stays_readable(tmp_path: Path):
    write(tmp_path, "v1.yaml", GOOD)
    write(tmp_path, "v2.yaml", NEWER)

    catalog = load_catalog(tmp_path)

    # An instance that started on version 1 has to keep reading version 1, or it
    # would finish following a process nobody started.
    assert catalog.get("simple", 1).steps[0].tool == "do_something"
    assert catalog.get("simple", 2).steps[0].tool == "do_something_else"


def test_the_same_id_and_version_twice_is_refused(tmp_path: Path):
    write(tmp_path, "one.yaml", GOOD)
    write(tmp_path, "copy.yaml", GOOD)

    with pytest.raises(DefinitionError, match="simple"):
        load_catalog(tmp_path)


def test_an_unknown_process_says_what_exists(tmp_path: Path):
    write(tmp_path, "good.yaml", GOOD)
    catalog = load_catalog(tmp_path)

    with pytest.raises(KeyError, match="simple"):
        catalog.latest("nothing-like-this")


def test_an_empty_folder_is_a_catalogue_with_nothing_in_it(tmp_path: Path):
    catalog = load_catalog(tmp_path)

    assert isinstance(catalog, Catalog)
    assert catalog.all() == []
