"""Rewrites the environment tables in the README from the code that reads them.

Run it after changing a configuration field:

    uv run --project ../demo-master-agent python tools/env_table.py

A table written by hand is a table that lies after the second change; this one
is generated from the fields themselves, and the CI checks it is up to date.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
README = ROOT / "README.md"

SOURCES = (
    ("master agent", "../demo-master-agent", "demo.config"),
    ("memory service", "../demo-memory-service", "memory_service.config"),
)

START = "<!-- env-table:start -->"
END = "<!-- env-table:end -->"

FRONTEND = """### frontend

Read at request time, so the same image serves any environment. The
`NEXT_PUBLIC_*` names still work as the build-time fallback.

| Variable | Default | What it decides |
|---|---|---|
| `AGUI_URL` | `http://127.0.0.1:8000/agui` | Where the agent answers. The logs endpoint is derived from it. |
| `PRODUCT_NAME` | `AG-UI Lab` | Name shown in the header and in the tab. |
| `PRODUCT_TAGLINE` | `an agent at work` | Line under the name. |
| `PRODUCT_DESCRIPTION` | *(see lib/runtime-config.ts)* | Page description. |
| `PRODUCT_DISCLAIMER` | *(see lib/runtime-config.ts)* | Footer line. |
| `PRODUCT_LOCALE` | `en` | `lang` of the document. |
| `PRODUCT_MONOGRAM` | `a/` | The two characters in the badge. |
| `PRODUCT_BADGES` | `AG-UI,MAF 1.17,Next.js` | Comma-separated badges in the header. |

### knowledge agent

| Variable | Default | What it decides |
|---|---|---|
| `KNOWLEDGE_BASE_URL` | `http://localhost:8200/` | The url this agent declares in its own card. |
| `KNOWLEDGE_SERVICE_TOKEN` | *(empty)* | Token that unlocks the extended card. Empty means nobody gets it. |
| `KNOWLEDGE_JSON_LOGS` | `false` | Structured logs for a collector. |
| `OPENAI_BASE_URL`, `OPENAI_API_KEY`, `OPENAI_CHAT_COMPLETION_MODEL` | *(as the master agent)* | The model this agent reads its corpus with. |
"""


def table_of(project: str, module: str) -> str:
    """Each repository has its own virtualenv: `uv run` picks the right one."""
    result = subprocess.run(
        ["uv", "run", "--project", str((ROOT / project).resolve()), "python", "-m", module],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SystemExit(f"{module} did not print its table:\n{result.stderr}")
    return result.stdout.strip()


def generated() -> str:
    parts = [START, ""]
    for name, project, module in SOURCES:
        parts += [f"### {name}", "", table_of(project, module), ""]
    parts += [FRONTEND, END]
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
        print("environment tables out of date: run tools/env_table.py")
        return 1
    README.write_text(updated, encoding="utf-8")
    print("environment tables rewritten")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
