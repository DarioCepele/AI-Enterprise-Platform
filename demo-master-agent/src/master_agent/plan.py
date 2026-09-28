"""The work plan: domain state, not a tool detail."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

STEP_STATUSES = ("pending", "in_progress", "completed", "failed")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class PlanStore:
    """Holds the current plan.

    It exists because `state_update` replaces top-level state keys instead of
    merging them: changing one step means re-emitting the whole plan, so the
    plan has to be readable back. MAF tools do not receive the shared state,
    so it is kept here.

    One per run: the plan tools build it from the thread's shared state at the
    start of each run (see `run_context.plan_of_run`), so two threads, or two
    replicas, never share a plan. An explicit instance is for tests.
    """

    def __init__(self, plan: dict[str, Any] | None = None) -> None:
        self._plan = self._normalize(plan)

    @staticmethod
    def _normalize(plan: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(plan, dict):
            return {"status": "idle", "steps": []}
        steps = plan.get("steps")
        return {
            "status": str(plan.get("status", "idle")),
            "steps": [dict(step) for step in steps if isinstance(step, dict)]
            if isinstance(steps, list)
            else [],
        }

    def snapshot(self) -> dict[str, Any]:
        """A copy of the plan. A copy and not a reference: the caller serializes it
        later.
        """
        return {
            "status": self._plan["status"],
            "steps": [dict(step) for step in self._plan["steps"]],
        }

    def write(self, steps: list[dict[str, Any]]) -> dict[str, Any]:
        """Replaces the plan. Every step starts as `pending`."""
        self._plan = {
            "status": "in_progress",
            "steps": [
                {
                    "id": int(step["id"]),
                    "title": str(step["title"]),
                    "detail": str(step.get("detail", "")),
                    "source": str(step.get("source", "")),
                    "status": "pending",
                    "started_at": None,
                    "ended_at": None,
                    "note": None,
                }
                for step in steps
            ],
        }
        return self.snapshot()

    def set_status(self, step_id: int, status: str, note: str | None) -> dict[str, Any]:
        """Changes a step's status and recomputes the plan's own."""
        if status not in STEP_STATUSES:
            raise ValueError(
                f"unknown status '{status}': expected {', '.join(STEP_STATUSES)}"
            )

        step = next((s for s in self._plan["steps"] if s["id"] == step_id), None)
        if step is None:
            known = ", ".join(str(s["id"]) for s in self._plan["steps"]) or "none"
            raise ValueError(f"step {step_id} does not exist: known steps {known}")

        if status == "failed" and (not note or not note.strip()):
            raise ValueError(
                "status 'failed' requires a reason (note): pass a non-empty message"
            )

        step["status"] = status
        step["note"] = note
        if status == "in_progress" and step["started_at"] is None:
            step["started_at"] = _now()
        if status in ("completed", "failed"):
            step["ended_at"] = _now()

        statuses = [s["status"] for s in self._plan["steps"]]
        if "failed" in statuses:
            self._plan["status"] = "failed"
        elif all(s == "completed" for s in statuses):
            self._plan["status"] = "completed"
        else:
            self._plan["status"] = "in_progress"

        return self.snapshot()
