"""Skills in the Agent Skills format: one folder, one SKILL.md, YAML frontmatter.

The format is the open one the ecosystem adopted, not a registry of our own: a
skill written here travels elsewhere without being rewritten. The frontmatter
is read with a real YAML parser, so the fields the format allows beyond `name`
and `description` -- `license`, `allowed-tools`, a nested `metadata` map,
folded multi-line descriptions -- are understood instead of mangled.

Skills come from the platform's own folder and from any folder a fork adds
(`MASTER_SKILLS_DIRS`); a name defined twice is an error, not a silent winner.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated, Any

import yaml
from agent_framework import Content, FunctionTool, tool

logger = logging.getLogger(__name__)

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"

_FRONTMATTER = re.compile(r"\A---\s*\n(?P<meta>.*?)\n---\s*\n(?P<body>.*)\Z", re.S)


def parse_skill(text: str) -> dict[str, Any]:
    """Splits a SKILL.md into metadata and body. Raises if the shape is wrong."""
    match = _FRONTMATTER.match(text)
    if match is None:
        raise ValueError(
            "SKILL.md without frontmatter: a --- block is required at the top "
            "of the file"
        )
    try:
        meta = yaml.safe_load(match.group("meta")) or {}
    except yaml.YAMLError as error:
        raise ValueError(f"unreadable frontmatter: {error}") from error
    if not isinstance(meta, dict):
        raise ValueError("the frontmatter must be a YAML mapping")

    name = str(meta.get("name") or "").strip()
    if not name:
        raise ValueError("frontmatter without a name field")
    description = " ".join(str(meta.get("description") or "").split())
    if not description:
        raise ValueError(f"skill '{name}' without a description field")

    return {
        **meta,
        "name": name,
        "description": description,
        "body": match.group("body").strip(),
    }


def list_skills(roots: Path | Sequence[Path] = SKILLS_DIR) -> list[dict[str, Any]]:
    """The available skills, sorted by name. A broken or duplicate skill raises."""
    folders = [roots] if isinstance(roots, Path) else list(roots)
    found: dict[str, dict[str, Any]] = {}
    for folder in folders:
        for skill_file in sorted(folder.glob("*/SKILL.md")):
            parsed = parse_skill(skill_file.read_text(encoding="utf-8"))
            if parsed["name"] in found:
                raise ValueError(
                    f"skill '{parsed['name']}' is defined twice (again in {skill_file})"
                )
            found[parsed["name"]] = parsed
    return [found[name] for name in sorted(found)]


def build_skill_tools(roots: Path | Sequence[Path] = SKILLS_DIR) -> list[FunctionTool]:
    """The load_skill tool, bound to one or more folders of skills."""
    catalogue = list_skills(roots)
    listing = "\n".join(f"- {s['name']}: {s['description']}" for s in catalogue)

    @tool
    def load_skill(
        name: Annotated[str, "The skill name, as it appears in the catalogue"],
    ) -> Content:
        """Loads the operating instructions of a skill.

        Call it when the request falls into a domain the catalogue covers,
        before starting to work. The available skills are:
        """
        wanted = next((s for s in catalogue if s["name"] == name), None)
        if wanted is None:
            logger.warning("Skill '%s' not found.", name)
            known = ", ".join(s["name"] for s in catalogue) or "none"
            return Content.from_text(
                f"The skill '{name}' does not exist. Available skills: {known}."
            )
        logger.info("Skill '%s' loaded.", name)
        return Content.from_text(str(wanted["body"]))

    load_skill.description = f"{load_skill.description}\n{listing}"
    return [load_skill]
