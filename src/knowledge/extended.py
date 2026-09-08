"""La card estesa e chi ha diritto di vederla."""
from __future__ import annotations

import logging
import os
from collections.abc import Sequence
from hmac import compare_digest

from a2a.server.context import ServerCallContext
from a2a.types import AgentCard, AgentSkill
from a2a.utils.errors import ExtendedAgentCardNotConfiguredError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

SCHEMA = "servizio"
PREFISSO = "bearer "
PERCORSI_PROTETTI = ("/extendedAgentCard", "/v1/extendedAgentCard")


def token_atteso() -> str:
    return os.getenv("KNOWLEDGE_SERVICE_TOKEN", "")


def token_della_richiesta(intestazioni: dict[str, str]) -> str:
    valore = ""
    for nome, contenuto in intestazioni.items():
        if nome.lower() == "authorization":
            valore = contenuto
            break
    if not valore.lower().startswith(PREFISSO):
        return ""
    return valore[len(PREFISSO) :].strip()


def intestazioni_valide(intestazioni: dict[str, str]) -> bool:
    atteso = token_atteso()
    ricevuto = token_della_richiesta(intestazioni)
    return bool(atteso) and bool(ricevuto) and compare_digest(ricevuto, atteso)


def autenticato(contesto: ServerCallContext | None) -> bool:
    return intestazioni_valide((contesto.state.get("headers") if contesto else None) or {})


class SoloConToken(BaseHTTPMiddleware):
    """401 sul percorso REST della card estesa, con l'indicazione di come autenticarsi.

    Serve a un client legittimo, che dal 401 impara quale schema usare; il
    resto dell'API non cambia, perche' l'agente pubblico resta pubblico.
    """

    async def dispatch(self, request: Request, call_next):
        if request.url.path in PERCORSI_PROTETTI and not intestazioni_valide(
            dict(request.headers)
        ):
            logger.warning("Card estesa negata su %s: token assente o non valido.", request.url.path)
            return JSONResponse(
                {"error": "serve un token di servizio"},
                status_code=401,
                headers={"WWW-Authenticate": f'Bearer realm="{SCHEMA}"'},
            )
        return await call_next(request)


def catalogo_skill(documenti: Sequence[str]) -> AgentSkill:
    return AgentSkill(
        id="catalogo",
        name="Catalogo dei documenti",
        description=(
            "Elenca i documenti indicizzati e permette di citarli per nome: "
            + ", ".join(documenti)
        ),
        tags=["catalogo", "interno"],
    )


def build_extended_card(pubblica: AgentCard, documenti: Sequence[str]) -> AgentCard:
    """La card pubblica piu' cio' che non si mette in vetrina.

    Quali documenti abbiamo indicizzato dice a chi guarda di cosa si occupa
    l'organizzazione: e' esattamente il tipo di dettaglio che serve a chi deve
    usare l'agente e non a chi passa di li'.
    """
    estesa = AgentCard()
    estesa.CopyFrom(pubblica)
    estesa.description = (
        f"{pubblica.description} Vista estesa: include il catalogo indicizzato."
    )
    estesa.skills.append(catalogo_skill(documenti))
    return estesa


async def card_per_chi_chiede(card: AgentCard, contesto: ServerCallContext) -> AgentCard:
    """Serve la card estesa solo a chi si e' autenticato.

    A chi non lo e' la card estesa non esiste, invece di esistere e negare: la
    risposta e' la stessa che darebbe un agente che non ne ha una, e non
    conferma a un estraneo che qui c'e' qualcosa di piu' da chiedere. Il 401
    con `WWW-Authenticate` lo riceve chi arriva sul percorso REST, che e'
    dove un client legittimo va a cercare le credenziali da usare.
    """
    if not autenticato(contesto):
        logger.warning("Card estesa negata: token di servizio assente o non valido.")
        raise ExtendedAgentCardNotConfiguredError(
            "Authenticated Extended Card is not configured"
        )
    logger.info("Card estesa servita a un chiamante autenticato.")
    return card
