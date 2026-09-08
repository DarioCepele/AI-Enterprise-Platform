"""L'executor A2A del knowledge agent, scritto contro l'SDK stabile."""
from __future__ import annotations

import json
import logging
from typing import Any

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.helpers import new_task_from_user_message
from a2a.server.tasks import TaskUpdater
from a2a.types import Part, TaskState
from agent_framework import Agent
from google.protobuf.json_format import ParseDict
from google.protobuf.struct_pb2 import Value

logger = logging.getLogger(__name__)

ARTEFATTO = "scheda"

# Marcatore che il modello mette quando la domanda non basta a se stessa.
CHIEDE = "[SERVE-CHIARIMENTO]"


class LettureDocumenti:
    """Quali documenti ha letto l'agente, dagli argomenti che arrivano a delta.

    Il modello streamma una tool call in piu' pezzi, e i pezzi non si assomigliano:
    il primo porta il **nome** del tool e argomenti vuoti, quelli dopo portano gli
    **argomenti** a delta e nessun nome. Chi filtra per nome a ogni pezzo scarta
    proprio quelli che contengono la risposta -- misurato, non immaginato.
    """

    def __init__(self) -> None:
        self._parziali: dict[str, str] = {}
        self._nostre: set[str] = set()
        self.documenti: list[str] = []

    def osserva(self, update: Any) -> None:
        for content in getattr(update, "contents", None) or []:
            if getattr(content, "type", "") != "function_call":
                continue
            call_id = getattr(content, "call_id", "") or ""
            nome_tool = getattr(content, "name", "") or ""
            if nome_tool == "leggi_documento":
                self._nostre.add(call_id)
            elif nome_tool:
                continue
            if call_id not in self._nostre:
                continue
            argomenti = getattr(content, "arguments", None)
            if isinstance(argomenti, dict):
                self._registra(argomenti.get("nome"))
                continue
            if isinstance(argomenti, str):
                self._parziali[call_id] = self._parziali.get(call_id, "") + argomenti
                self._prova(self._parziali[call_id])

    def _prova(self, grezzo: str) -> None:
        try:
            argomenti = json.loads(grezzo)
        except json.JSONDecodeError:
            return
        if isinstance(argomenti, dict):
            self._registra(argomenti.get("nome"))

    def _registra(self, nome: Any) -> None:
        if isinstance(nome, str) and nome and nome not in self.documenti:
            self.documenti.append(nome)


def scheda(domanda: str, risposta: str, documenti: list[str]) -> list[Part]:
    """L'output del sottoagente: testo per il modello, dati per l'interfaccia."""
    dati = ParseDict(
        {
            "component": "scheda",
            "domanda": domanda,
            "documenti": documenti,
            "estratto": risposta,
        },
        Value(),
    )
    return [Part(text=risposta), Part(data=dati)]


class KnowledgeExecutor(AgentExecutor):
    """Traduce una richiesta A2A in una run dell'agente, e viceversa.

    Scritto a mano invece di usare la colla in beta per due ragioni: toglie un
    pacchetto beta dal percorso portante, e permette di emettere un artefatto
    **con un nome e dei dati** invece di una sequenza di chunk anonimi.
    """

    def __init__(self, agent: Agent) -> None:
        self._agent = agent

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)

        updater = TaskUpdater(event_queue, task.id, context.context_id)
        domanda = context.get_user_input()

        await updater.submit()
        await updater.start_work()

        pezzi: list[str] = []
        letture = LettureDocumenti()
        try:
            async for update in self._agent.run(domanda, stream=True):
                letture.osserva(update)
                testo = getattr(update, "text", None)
                if testo:
                    pezzi.append(testo)
                    await updater.update_status(
                        TaskState.TASK_STATE_WORKING,
                        message=updater.new_agent_message([Part(text=testo)]),
                    )
        except Exception as errore:
            logger.error("Run fallita per '%s'.", domanda, exc_info=True)
            await updater.failed(
                message=updater.new_agent_message([Part(text=f"knowledge agent: {errore}")])
            )
            return

        risposta = "".join(pezzi).strip()

        if risposta.startswith(CHIEDE):
            domanda_di_ritorno = risposta[len(CHIEDE) :].strip() or "Puoi precisare la richiesta?"
            await updater.requires_input(
                message=updater.new_agent_message([Part(text=domanda_di_ritorno)])
            )
            logger.info("Task %s in attesa di un chiarimento.", task.id)
            return

        if not risposta:
            await updater.failed(
                message=updater.new_agent_message(
                    [Part(text="knowledge agent: nessuna risposta prodotta")]
                )
            )
            return

        await updater.add_artifact(
            scheda(domanda, risposta, letture.documenti),
            name=ARTEFATTO,
            last_chunk=True,
        )
        await updater.complete()
        logger.info(
            "Task %s concluso: %d caratteri, documenti %s.",
            task.id,
            len(risposta),
            ", ".join(letture.documenti) or "nessuno",
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        await updater.cancel()
        logger.info("Task %s annullato su richiesta.", context.task_id)
