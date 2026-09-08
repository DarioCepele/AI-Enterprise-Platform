"""Tool che producono artefatti renderizzati dal frontend."""
from __future__ import annotations

import logging
from uuid import uuid4
from typing import Annotated

from agent_framework import Content, FunctionTool, tool
from agent_framework.ag_ui import state_update

logger = logging.getLogger(__name__)

STATE_KEY = "__ag_ui_tool_result_state__"
DISPLAY_KEY = "__ag_ui_tool_result_display__"


@tool
def ui_table(
    title: Annotated[str, "Titolo della tabella"],
    columns: Annotated[list[str], "Intestazioni di colonna"],
    rows: Annotated[list[list[str]], "Righe, ognuna lunga quanto columns"],
) -> Content:
    """Mostra una tabella all'utente.

    Usa questo tool quando devi confrontare piu' elementi lungo dimensioni comuni.
    """
    artifact_id = f"art_{uuid4().hex[:8]}"
    logger.info("Tabella '%s' prodotta: %d colonne, %d righe.", title, len(columns), len(rows))
    return state_update(
        text=f"Ho mostrato la tabella '{title}' con {len(rows)} righe.",
        tool_result={
            "component": "ui-table",
            "id": artifact_id,
            "title": title,
            "columns": columns,
            "rows": rows,
        },

        state={
            "artifacts": [
                {"id": artifact_id, "component": "ui-table", "title": title}
            ]
        },
    )

def get_tools() -> list[FunctionTool]:
    """I tool nativi disponibili al master agent."""
    return [ui_table]
