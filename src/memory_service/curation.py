"""Cosa di questa conversazione torna nel contesto del modello.

Il servizio **conserva tutto** e **restituisce il necessario**: sono due
decisioni diverse, e tenerle separate e' il punto. Una memoria che pota in
scrittura ha buttato per sempre; una che pota in lettura puo' cambiare idea, e
il transcript integrale resta disponibile per i riassunti e per capire cosa e'
successo davvero.

Misurato su due turni di questo laboratorio (40 messaggi, 12 KB):

| ruolo     | quota dei byte |
|-----------|----------------|
| user      | 1.8%           |
| reasoning | 18.9%          |
| assistant | 58.0%          |
| tool      | 21.4%          |

Quello che l'utente ha detto e' il 2% del transcript. Il resto e' macchinario,
e il macchinario vecchio non aiuta il turno successivo: piu' token ci sono,
meno il modello ne recupera con precisione.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Il testo che prende il posto di un risultato svuotato. Dice che c'era
# qualcosa, invece di far credere al modello che il tool non abbia risposto.
CLEARED = "[risultato rimosso per fare spazio nel contesto]"


@dataclass(frozen=True)
class ContextPolicy:
    """Le regole con cui si ricompone il contesto."""

    # Il ragionamento di un turno passato non serve a continuare quello nuovo:
    # il modello lo rifa'. Dentro una run resta intatto, perche' li' il ciclo
    # non passa da qui.
    drop_reasoning: bool = True
    # Quanti risultati di tool restano per intero, dal fondo. Gli altri si
    # svuotano tenendo la traccia della chiamata: e' la potatura a rischio piu'
    # basso, perche' quei risultati sono ri-ottenibili chiamando di nuovo.
    keep_tool_results: int = 4
    # Tetto di messaggi restituiti. None = nessun tetto.
    max_messages: int | None = 60


@dataclass(frozen=True)
class Curation:
    """Cosa e' stato tolto. Serve a rendere visibile una potatura silenziosa."""

    conservati: int
    ragionamenti_tolti: int
    risultati_svuotati: int
    messaggi_scartati: int

    def as_dict(self) -> dict[str, int]:
        return {
            "conservati": self.conservati,
            "ragionamenti_tolti": self.ragionamenti_tolti,
            "risultati_svuotati": self.risultati_svuotati,
            "messaggi_scartati": self.messaggi_scartati,
        }


def _role(message: dict[str, Any]) -> str:
    role = message.get("role")
    return role if isinstance(role, str) else ""


def _window_start(messages: list[dict[str, Any]], max_messages: int) -> int:
    """Da dove far partire la finestra, tagliando su un confine di turno.

    Tagliare a un indice qualsiasi lascerebbe orfano il risultato di un tool la
    cui chiamata e' finita fuori: una conversazione che al modello non torna.
    Si taglia quindi su un messaggio dell'utente, cioe' dove un turno comincia.

    Fra i due confini possibili si sceglie quello **precedente** al taglio
    ideale, non quello successivo: si tiene qualche messaggio in piu' del tetto
    invece di buttare un turno intero. Il tetto e' un obiettivo, la domanda
    dell'utente e' il contenuto. Senza nessun confine si tiene tutto: meglio un
    contesto lungo di uno incoerente.
    """
    if len(messages) <= max_messages:
        return 0
    for index in range(len(messages) - max_messages, -1, -1):
        if _role(messages[index]) == "user":
            return index
    return 0


def curate(
    messages: list[dict[str, Any]],
    policy: ContextPolicy,
) -> tuple[list[dict[str, Any]], Curation]:
    """Il contesto da restituire, e il conto di cio' che non c'e' piu'."""
    start = _window_start(messages, policy.max_messages) if policy.max_messages else 0
    window = messages[start:]
    scartati = start

    if policy.drop_reasoning:
        kept = [m for m in window if _role(m) != "reasoning"]
        ragionamenti = len(window) - len(kept)
        window = kept
    else:
        ragionamenti = 0

    # I risultati piu' recenti restano interi: sono quelli su cui il modello
    # sta ancora ragionando.
    tool_indexes = [index for index, m in enumerate(window) if _role(m) == "tool"]
    to_clear = set(tool_indexes[: max(len(tool_indexes) - policy.keep_tool_results, 0)])

    curated: list[dict[str, Any]] = []
    for index, message in enumerate(window):
        if index in to_clear:
            # Si sostituisce il contenuto, non il messaggio: la traccia della
            # chiamata resta, e il modello vede che quel passo e' avvenuto.
            curated.append({**message, "content": CLEARED})
        else:
            curated.append(message)

    return curated, Curation(
        conservati=len(curated),
        ragionamenti_tolti=ragionamenti,
        risultati_svuotati=len(to_clear),
        messaggi_scartati=scartati,
    )
