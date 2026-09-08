"""Skill in formato Agent Skills: una cartella, un SKILL.md, frontmatter YAML.

Il formato e' quello aperto adottato dall'ecosistema, non un registry nostro:
una skill scritta qui si porta altrove senza riscriverla.
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
    """Divide un SKILL.md in metadati e corpo. Solleva se la forma non torna."""
    match = _FRONTMATTER.match(text)
    if match is None:
        raise ValueError(
            "SKILL.md senza frontmatter: serve un blocco --- in cima al file"
        )

    meta: dict[str, str] = {}
    for line in match.group("meta").splitlines():
        if not line.strip():
            continue
        field = _FIELD.match(line.strip())
        if field is None:
            raise ValueError(f"riga di frontmatter non interpretabile: {line!r}")
        meta[field.group("key")] = field.group("value").strip()

    if "name" not in meta:
        raise ValueError("frontmatter senza campo name")
    if "description" not in meta:
        raise ValueError(f"skill '{meta['name']}' senza campo description")

    return {
        "name": meta["name"],
        "description": meta["description"],
        "body": match.group("body").strip(),
    }

def list_skills(root: Path = SKILLS_DIR) -> list[dict[str, Any]]:
    """Le skill disponibili, ordinate per nome. Una skill rotta solleva subito."""
    found = []
    for skill_file in sorted(root.glob("*/SKILL.md")):
        parsed = parse_skill(skill_file.read_text(encoding="utf-8"))
        found.append(parsed)
    return found

def build_skill_tools(root: Path = SKILLS_DIR) -> list[FunctionTool]:
    """Il tool load_skill, legato a una cartella di skill.

    `root` e' un parametro perche' i test caricano da una tmp_path invece che
    dalle skill vere del repo.
    """
    catalogue = list_skills(root)
    listing = "\n".join(f"- {s['name']}: {s['description']}" for s in catalogue)

    @tool
    def load_skill(
        name: Annotated[str, "Il nome della skill, come compare nel catalogo"],
    ) -> Content:
        """Carica le istruzioni operative di una skill.

        Chiamalo quando la richiesta ricade in un dominio coperto dal catalogo,
        prima di iniziare a lavorare. Le skill disponibili sono:
        """
        wanted = next((s for s in catalogue if s["name"] == name), None)
        if wanted is None:
            logger.warning("Skill '%s' non trovata.", name)
            known = ", ".join(s["name"] for s in catalogue) or "nessuna"

            return Content.from_text(
                f"La skill '{name}' non esiste. Skill disponibili: {known}."
            )
        logger.info("Skill '%s' caricata.", name)
        return Content.from_text(wanted["body"])

    load_skill.description = f"{load_skill.description}\n{listing}"
    return [load_skill]
