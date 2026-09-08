"""Il piano di lavoro: stato di dominio, non dettaglio dei tool."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

STEP_STATUSES = ("pending", "in_progress", "completed", "failed")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class PlanStore:
    """Tiene il piano corrente.

    Serve perche' `state_update` sostituisce le chiavi di primo livello dello
    stato invece di fonderle: per cambiare un passo bisogna riemettere il piano
    intero, quindi bisogna poterlo rileggere. I tool MAF non ricevono lo stato
    condiviso, quindi lo teniamo qui.

    Un solo piano per istanza: la demo costruisce un agente solo, quindi due
    schede del browser condividono lo stesso piano. Limite accettato, scritto
    nel README.
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
        """Copia del piano. Copia e non riferimento: chi la riceve la serializza dopo."""
        return {
            "status": self._plan["status"],
            "steps": [dict(step) for step in self._plan["steps"]],
        }

    def write(self, steps: list[dict[str, Any]]) -> dict[str, Any]:
        """Sostituisce il piano. Ogni passo parte da `pending`."""
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

    def set_status(
        self, step_id: int, status: str, note: str | None
    ) -> dict[str, Any]:
        """Cambia lo stato di un passo e ricalcola quello del piano."""
        if status not in STEP_STATUSES:
            raise ValueError(
                f"stato '{status}' sconosciuto: attesi {', '.join(STEP_STATUSES)}"
            )

        step = next((s for s in self._plan["steps"] if s["id"] == step_id), None)
        if step is None:
            known = ", ".join(str(s["id"]) for s in self._plan["steps"]) or "nessuno"
            raise ValueError(f"passo {step_id} non esiste: passi noti {known}")

        if status == "failed" and (not note or not note.strip()):
            raise ValueError(
                f"stato 'failed' richiede un motivo (note): passare un messaggio non vuoto"
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
