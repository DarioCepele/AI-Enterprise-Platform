"""Client A2A scritto contro l'SDK stabile."""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import httpx
from a2a.client import Client, ClientConfig, ClientFactory
from a2a.types import (
    AgentCard,
    Message,
    Part,
    Role,
    SendMessageConfiguration,
    GetTaskRequest,
    SendMessageRequest,
    TaskPushNotificationConfig,
    TaskState,
)
from google.protobuf.json_format import ParseDict

logger = logging.getLogger(__name__)

CARD_PATH = ".well-known/agent-card.json"

STATI = {
    TaskState.TASK_STATE_SUBMITTED: "accettato",
    TaskState.TASK_STATE_WORKING: "al lavoro",
    TaskState.TASK_STATE_COMPLETED: "concluso",
    TaskState.TASK_STATE_FAILED: "fallito",
    TaskState.TASK_STATE_CANCELED: "annullato",
    TaskState.TASK_STATE_INPUT_REQUIRED: "attende una risposta",
    TaskState.TASK_STATE_REJECTED: "rifiutato",
    TaskState.TASK_STATE_AUTH_REQUIRED: "richiede autenticazione",
}

APERTI = {TaskState.TASK_STATE_SUBMITTED, TaskState.TASK_STATE_WORKING}
CHIUSI = {
    TaskState.TASK_STATE_COMPLETED,
    TaskState.TASK_STATE_FAILED,
    TaskState.TASK_STATE_CANCELED,
    TaskState.TASK_STATE_REJECTED,
}


@dataclass
class Artefatto:
    """Un output strutturato del sottoagente."""

    artifact_id: str
    name: str
    description: str
    testo: str
    dati: dict[str, Any] | None = None


@dataclass
class Avanzamento:
    """Un passo del task, come lo vede chi lo ha chiesto."""

    task_id: str
    stato: str
    stato_grezzo: int
    testo: str = ""
    artefatto: Artefatto | None = None
    domanda: str = ""

    @property
    def chiuso(self) -> bool:
        return self.stato_grezzo in CHIUSI

    @property
    def attende_risposta(self) -> bool:
        return self.stato_grezzo == TaskState.TASK_STATE_INPUT_REQUIRED


@dataclass
class Esito:
    """Come e' finito un task."""

    task_id: str
    stato: str
    testo: str
    artefatti: list[Artefatto] = field(default_factory=list)
    domanda: str = ""

    @property
    def riuscito(self) -> bool:
        return self.stato == STATI[TaskState.TASK_STATE_COMPLETED]


async def fetch_agent_card(url: str, timeout: float = 10.0) -> AgentCard:
    async with httpx.AsyncClient(timeout=timeout) as http:
        response = await http.get(f"{url.rstrip('/')}/{CARD_PATH}")
        response.raise_for_status()
        return ParseDict(response.json(), AgentCard(), ignore_unknown_fields=True)


def _testo_di(parts) -> str:
    return "".join(part.text for part in parts if part.text)


def _artefatto_di(artifact) -> Artefatto:
    dati = None
    for part in artifact.parts:
        if part.HasField("data"):
            from google.protobuf.json_format import MessageToDict

            dati = MessageToDict(part.data)
            break
    return Artefatto(
        artifact_id=artifact.artifact_id,
        name=artifact.name,
        description=artifact.description,
        testo=_testo_di(artifact.parts),
        dati=dati,
    )


class A2AClient:
    """Parla con un agente remoto vedendo il ciclo di vita del task."""

    def __init__(self, card: AgentCard, http_client: httpx.AsyncClient | None = None) -> None:
        self._http = http_client or httpx.AsyncClient(timeout=120.0)
        self._client: Client = ClientFactory(
            ClientConfig(httpx_client=self._http, streaming=True)
        ).create(card)

    async def chiedi(
        self,
        testo: str,
        task_id: str | None = None,
        context_id: str | None = None,
        webhook: tuple[str, str] | None = None,
    ) -> AsyncIterator[Avanzamento]:
        """Manda un messaggio e restituisce gli avanzamenti del task.

        Con `task_id` il messaggio riprende un task che aspettava una risposta,
        invece di aprirne uno nuovo.
        """
        message = Message(
            message_id=uuid4().hex,
            role=Role.ROLE_USER,
            parts=[Part(text=testo)],
        )
        if task_id:
            message.task_id = task_id
        if context_id:
            message.context_id = context_id

        richiesta = SendMessageRequest(message=message)
        if webhook:
            url, token = webhook
            richiesta.configuration.CopyFrom(
                SendMessageConfiguration(
                    task_push_notification_config=TaskPushNotificationConfig(url=url, token=token)
                )
            )

        corrente = task_id or ""
        async for response in self._client.send_message(richiesta):
            if response.HasField("task"):
                task = response.task
                corrente = task.id
                yield Avanzamento(
                    task_id=task.id,
                    stato=STATI.get(task.status.state, "sconosciuto"),
                    stato_grezzo=task.status.state,
                    testo=_testo_di(task.status.message.parts) if task.status.message.parts else "",
                )
            elif response.HasField("status_update"):
                aggiornamento = response.status_update
                corrente = aggiornamento.task_id or corrente
                messaggio = aggiornamento.status.message
                testo = _testo_di(messaggio.parts) if messaggio.parts else ""
                yield Avanzamento(
                    task_id=corrente,
                    stato=STATI.get(aggiornamento.status.state, "sconosciuto"),
                    stato_grezzo=aggiornamento.status.state,
                    testo=testo,
                    domanda=testo
                    if aggiornamento.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
                    else "",
                )
            elif response.HasField("artifact_update"):
                aggiornamento = response.artifact_update
                corrente = aggiornamento.task_id or corrente
                yield Avanzamento(
                    task_id=corrente,
                    stato=STATI[TaskState.TASK_STATE_WORKING],
                    stato_grezzo=TaskState.TASK_STATE_WORKING,
                    artefatto=_artefatto_di(aggiornamento.artifact),
                )
            elif response.HasField("message"):
                yield Avanzamento(
                    task_id=corrente,
                    stato=STATI[TaskState.TASK_STATE_COMPLETED],
                    stato_grezzo=TaskState.TASK_STATE_COMPLETED,
                    testo=_testo_di(response.message.parts),
                )

    async def esito(self, task_id: str) -> Esito:
        """Rilegge un task concluso.

        La notifica push dice che il task e' finito, non cosa ha prodotto: il
        risultato si va a prendere, invece di ricostruirlo accumulando le
        notifiche -- che sarebbe stato di processo.
        """
        task = await self._client.get_task(GetTaskRequest(id=task_id))
        artefatti = [_artefatto_di(a) for a in task.artifacts]
        testo = "\n".join(a.testo for a in artefatti if a.testo).strip()
        return Esito(
            task_id=task.id,
            stato=STATI.get(task.status.state, "sconosciuto"),
            testo=testo,
            artefatti=artefatti,
        )

    async def aclose(self) -> None:
        await self._http.aclose()
