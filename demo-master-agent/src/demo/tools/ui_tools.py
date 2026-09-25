"""Tools that produce artifacts rendered by the frontend."""

from __future__ import annotations

import logging
from typing import Annotated
from uuid import uuid4

from agent_framework import Content, FunctionTool, tool
from agent_framework.ag_ui import state_update

logger = logging.getLogger(__name__)

STATE_KEY = "__ag_ui_tool_result_state__"
DISPLAY_KEY = "__ag_ui_tool_result_display__"


@tool
def ui_table(
    title: Annotated[str, "Table title"],
    columns: Annotated[list[str], "Column headers"],
    rows: Annotated[list[list[str]], "Rows, each as long as columns"],
) -> Content:
    """Shows a table to the user.

    Use this tool when you have to compare several items along common dimensions.
    """
    artifact_id = f"art_{uuid4().hex[:8]}"
    logger.info(
        "Table '%s' produced: %d columns, %d rows.", title, len(columns), len(rows)
    )
    return state_update(
        text=f"I showed the table '{title}' with {len(rows)} entries.",
        tool_result={
            "component": "ui-table",
            "id": artifact_id,
            "title": title,
            "columns": columns,
            "rows": rows,
        },
        state={
            "artifacts": [{"id": artifact_id, "component": "ui-table", "title": title}]
        },
    )


def get_tools() -> list[FunctionTool]:
    """The native tools available to the master agent."""
    return [ui_table]
