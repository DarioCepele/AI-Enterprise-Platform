"""The work plan as shared state.

The tools emit no text for the user: they mutate `state.plan`, and the
work-plan panel is a pure function of that object.
"""

from __future__ import annotations

import logging
from typing import Annotated

from agent_framework import Content, FunctionTool, tool
from agent_framework.ag_ui import state_update

from ..plan import PlanStore
from ..server.run_context import plan_of_run

logger = logging.getLogger(__name__)


def build_plan_tools(store: PlanStore | None = None) -> list[FunctionTool]:
    """The plan tools.

    Without `store` the tools use the plan of the current run, hydrated from
    the shared state: the process keeps no copy and two replicas cannot
    contradict each other. With an explicit `store` they work on that one --
    the tests do.
    """

    def plan_store() -> PlanStore:
        if store is not None:
            return store
        current = plan_of_run()
        if current is None:
            raise RuntimeError("no plan: the plan tools must be used inside a run")
        return current

    @tool
    def todo_write(
        steps: Annotated[
            list[dict],
            "The steps of the plan. Each step: id (integer, from 1), title, "
            "detail, source.",
        ],
    ) -> Content:
        """Writes the work plan, replacing the previous one.

        Use it once at the beginning, when the user's request needs more than
        one step. Do not use it for single-step requests.
        """
        plan = plan_store().write(steps)
        logger.info("Plan written: %d steps.", len(plan["steps"]))
        return state_update(
            text=f"Plan written: {len(plan['steps'])} steps.",
            tool_result={"component": "plan", "steps": len(plan["steps"])},
            state={"plan": plan},
        )

    @tool
    def todo_set_status(
        step_id: Annotated[int, "The id of the step to update"],
        status: Annotated[str, "One of: pending, in_progress, completed, failed"],
        note: Annotated[str | None, "Reason, required when status is failed"] = None,
    ) -> Content:
        """Updates the status of one step of the plan.

        Mark a step `in_progress` before working on it and `completed` as soon
        as it is done, so the user sees the plan advance while you work.
        """
        plan = plan_store().set_status(step_id, status, note)
        if status == "failed":
            logger.error(
                "Step %d: failed (%d-character reason).", step_id, len(note or "")
            )
        else:
            logger.info("Step %d: %s.", step_id, status)
        return state_update(
            text=f"Step {step_id}: {status}.",
            tool_result={"component": "plan", "step_id": step_id, "status": status},
            state={"plan": plan},
        )

    return [todo_write, todo_set_status]
