"""Tool che producono artefatti renderizzati dal frontend."""
from __future__ import annotations

import itertools
from typing import Annotated

from agent_framework import Content, FunctionTool, tool
from agent_framework.ag_ui import state_update

# Chiavi riservate sotto cui state_update deposita i suoi payload in
# Content.additional_properties. Il display payload e' una stringa JSON,
# lo state resta un dict. L'emitter AG-UI le estrae e le rimuove.
STATE_KEY = "__ag_ui_tool_result_state__"
DISPLAY_KEY = "__ag_ui_tool_result_display__"

# Gli artefatti sono numerati per processo. Serve solo a dare al frontend una
# chiave con cui accoppiare l'elenco nello stato al payload nel tool result:
# non e' un identificativo stabile fra riavvii, e non deve diventarlo.
_artifact_ids = itertools.count(1)


@tool
def ui_table(
    title: Annotated[str, "Titolo della tabella"],
    columns: Annotated[list[str], "Intestazioni di colonna"],
    rows: Annotated[list[list[str]], "Righe, ognuna lunga quanto columns"],
) -> Content:
    """Mostra una tabella all'utente.

    Usa questo tool quando devi confrontare piu' elementi lungo dimensioni comuni.
    """
    artifact_id = f"art_{next(_artifact_ids)}"
    return state_update(
        text=f"Ho mostrato la tabella '{title}' con {len(rows)} righe.",
        tool_result={
            "component": "ui-table",
            "id": artifact_id,
            "title": title,
            "columns": columns,
            "rows": rows,
        },
        # Solo la chiave `artifacts`: state_update sostituisce le chiavi di
        # primo livello, quindi toccare anche `plan` qui lo cancellerebbe.
        state={
            "artifacts": [
                {"id": artifact_id, "component": "ui-table", "title": title}
            ]
        },
    )


def get_tools() -> list[FunctionTool]:
    """I tool nativi disponibili al master agent."""
    return [ui_table]
