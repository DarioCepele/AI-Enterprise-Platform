"""Skills in the Agent Skills format: one folder, one SKILL.md, YAML frontmatter.

The format is the open one the ecosystem adopted, not a registry of our own:
a skill written here travels elsewhere without being rewritten.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Annotated, Any

from agent_framework import Content, FunctionTool, tool

logger = logging.getLogger(__name__)

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"

_FRONTMATTER = re.compile(r"\A---\s*\n(?P<meta>.*?)\n---\s*\n(?P<body>.*)\Z", re.S)

_FIELD = re.compile(r"^(?P<key>[A-Za-z_][A-Za-z0-9_-]*):\s*(?P<value>.*)$")

def parse_skill(text: str) -> dict[str, str]:
    """Splits a SKILL.md into metadata and body. Raises if the shape is wrong."""
    match = _FRONTMATTER.match(text)
    if match is None:
        raise ValueError(
            "SKILL.md without frontmatter: a --- block is required at the top of the file"
        )

    meta: dict[str, str] = {}
    for line in match.group("meta").splitlines():
        if not line.strip():
            continue
        field = _FIELD.match(line.strip())
        if field is None:
            raise ValueError(f"unreadable frontmatter line: {line!r}")
        meta[field.group("key")] = field.group("value").strip()

    if "name" not in meta:
        raise ValueError("frontmatter without a name field")
    if "description" not in meta:
        raise ValueError(f"skill '{meta['name']}' without a description field")

    return {
        "name": meta["name"],
        "description": meta["description"],
        "body": match.group("body").strip(),
    }

def list_skills(root: Path = SKILLS_DIR) -> list[dict[str, Any]]:
    """The available skills, sorted by name. A broken skill raises right away."""
    found = []
    for skill_file in sorted(root.glob("*/SKILL.md")):
        parsed = parse_skill(skill_file.read_text(encoding="utf-8"))
        found.append(parsed)
    return found

def build_skill_tools(root: Path = SKILLS_DIR) -> list[FunctionTool]:
    """The load_skill tool, bound to a folder of skills.

    `root` is a parameter because the tests load from a tmp_path instead of
    the repository's real skills.
    """
    catalogue = list_skills(root)
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
        return Content.from_text(wanted["body"])

    load_skill.description = f"{load_skill.description}\n{listing}"
    return [load_skill]
