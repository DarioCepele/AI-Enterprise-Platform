"""Il piano di lavoro come stato condiviso.

I tool non emettono testo per l'utente: mutano `state.plan`, e il pannello
"Piano di lavoro" e' una funzione pura di quell'oggetto.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any

from agent_framework import Content, FunctionTool, tool
from agent_framework.ag_ui import state_update

from demo.tools.ui_tools import STATE_KEY

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

    def __init__(self) -> None:
        self._plan: dict[str, Any] = {"status": "idle", "steps": []}

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


def build_plan_tools(store: PlanStore) -> list[FunctionTool]:
    """I tool del piano, legati a `store`.

    Sono chiusure e non funzioni di modulo perche' lo stato del piano non deve
    essere globale: i test ne costruiscono uno per caso.
    """

    @tool
    def todo_write(
        steps: Annotated[
            list[dict],
            "I passi del piano. Ogni passo: id (intero, da 1), title, detail, source.",
        ],
    ) -> Content:
        """Scrive il piano di lavoro, sostituendo quello precedente.

        Usalo una volta sola all'inizio, quando la richiesta dell'utente
        richiede piu' passi. Non usarlo per richieste da un passo solo.
        """
        plan = store.write(steps)
        return state_update(
            text=f"Piano scritto: {len(plan['steps'])} passi.",
            tool_result={"component": "plan", "steps": len(plan["steps"])},
            state={"plan": plan},
        )

    @tool
    def todo_set_status(
        step_id: Annotated[int, "L'id del passo da aggiornare"],
        status: Annotated[str, "Uno fra: pending, in_progress, completed, failed"],
        note: Annotated[
            str | None, "Motivo, obbligatorio quando status e' failed"
        ] = None,
    ) -> Content:
        """Aggiorna lo stato di un passo del piano.

        Marca un passo `in_progress` prima di lavorarci e `completed` appena
        finito, cosi' l'utente vede il piano avanzare mentre lavori.
        """
        plan = store.set_status(step_id, status, note)
        return state_update(
            text=f"Passo {step_id}: {status}.",
            tool_result={"component": "plan", "step_id": step_id, "status": status},
            state={"plan": plan},
        )

    return [todo_write, todo_set_status]
