"""Rewrites the environment tables in the README from the code that reads them.

Run it after changing a configuration field:

    python tools/env_table.py            # rewrite
    python tools/env_table.py --check    # fail if the README is out of date

A table written by hand is a table that lies after the second change. Every
service prints its own table from its Settings (`python -m <module>`, in its
own environment, through `uv run`); the frontend's comes from
`lib/runtime-env.json`, which its tests hold to what the page actually reads.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
REPO = ROOT.parent
README = ROOT / "README.md"

SOURCES = (
    ("master agent", "demo-master-agent", "master_agent.config"),
    ("knowledge agent", "demo-knowledge-agent", "knowledge.config"),
    ("analysis agent", "demo-analysis-agent", "analysis.config"),
    ("memory service", "demo-memory-service", "memory_service.config"),
    ("process service", "demo-process-service", "process_service.config"),
    ("voice service", "demo-voice-service", "voice_service.config"),
    ("scraping MCP server", "demo-scraping-mcp", "scraping_mcp.config"),
)
FRONTEND = REPO / "demo-frontend" / "lib" / "runtime-env.json"

START = "<!-- env-table:start -->"
END = "<!-- env-table:end -->"


def table_of(project: str, module: str) -> str:
    """Each service has its own environment: `uv run` picks the right one."""
    result = subprocess.run(
        ["uv", "run", "--project", str(REPO / project), "python", "-m", module],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise SystemExit(f"{module} did not print its table:\n{result.stderr}")
    return result.stdout.strip()


def cell(text: str) -> str:
    return text.replace("|", "\\|")


def frontend_table() -> str:
    rows = [
        "Read at request time, so the same image serves any environment. The",
        "`NEXT_PUBLIC_*` names still work, as the fallback baked in at build time.",
        "",
        "| Variable | Default | What it decides |",
        "|---|---|---|",
    ]
    for entry in json.loads(FRONTEND.read_text(encoding="utf-8")):
        default = f"`{entry['default']}`" if entry["default"] else "*(empty)*"
        rows.append(f"| `{entry['variable']}` | {cell(default)} | {cell(entry['note'])} |")
    return "\n".join(rows)


def generated() -> str:
    parts = [START, ""]
    for name, project, module in SOURCES:
        parts += [f"### {name}", "", table_of(project, module), ""]
    parts += ["### frontend", "", frontend_table(), "", END]
    return "\n".join(parts)


def main() -> int:
    text = README.read_text(encoding="utf-8")
    if START not in text or END not in text:
        raise SystemExit(f"markers {START} / {END} not found in the README")
    before = text[: text.index(START)]
    after = text[text.index(END) + len(END) :]
    updated = before + generated() + after
    if updated == text:
        print("environment tables already up to date")
        return 0
    if "--check" in sys.argv:
        print("environment tables out of date: run python tools/env_table.py")
        return 1
    README.write_text(updated, encoding="utf-8", newline="\n")
    print("environment tables rewritten")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
