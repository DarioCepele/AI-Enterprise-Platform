"""Il piano di lavoro come stato condiviso.

I tool non emettono testo per l'utente: mutano `state.plan`, e il pannello
"Piano di lavoro" e' una funzione pura di quell'oggetto.
"""
from __future__ import annotations

import logging
from typing import Annotated, Any

from agent_framework import Content, FunctionTool, tool
from agent_framework.ag_ui import state_update

from demo.tools.ui_tools import STATE_KEY

from ..plan import STEP_STATUSES, PlanStore
from ..server.run_context import plan_of_run

logger = logging.getLogger(__name__)




def build_plan_tools(store: PlanStore | None = None) -> list[FunctionTool]:
    """I tool del piano.

    Senza `store` i tool usano il piano della run in corso, idratato dallo
    stato condiviso: il processo non ne conserva copia e due repliche non si
    contraddicono. Con `store` esplicito lavorano su quello -- lo usano i test.
    """

    def piano() -> PlanStore:
        if store is not None:
            return store
        corrente = plan_of_run()
        if corrente is None:
            raise RuntimeError("nessun piano: i tool del piano vanno usati dentro una run")
        return corrente

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
        plan = piano().write(steps)
        logger.info("Piano scritto: %d passi.", len(plan["steps"]))
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
        plan = piano().set_status(step_id, status, note)
        if status == "failed":
            logger.error("Passo %d: failed. Motivo: %s", step_id, note)
        else:
            logger.info("Passo %d: %s.", step_id, status)
        return state_update(
            text=f"Passo {step_id}: {status}.",
            tool_result={"component": "plan", "step_id": step_id, "status": status},
            state={"plan": plan},
        )

    return [todo_write, todo_set_status]
