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

CLEARED = "[risultato rimosso per fare spazio nel contesto]"

MEMORY_ID_PREFIX = "memoria:"
SUMMARY_ID_PREFIX = f"{MEMORY_ID_PREFIX}riassunto"


@dataclass(frozen=True)
class ContextPolicy:
    """Le regole con cui si ricompone il contesto."""

    drop_reasoning: bool = True
    keep_tool_results: int = 4
    max_messages: int | None = 60


@dataclass(frozen=True)
class Curation:
    """Cosa e' stato tolto. Serve a rendere visibile una potatura silenziosa."""

    conservati: int
    ragionamenti_tolti: int
    risultati_svuotati: int
    messaggi_scartati: int
    riassunti: bool = False

    def as_dict(self) -> dict[str, int]:
        return {
            "conservati": self.conservati,
            "ragionamenti_tolti": self.ragionamenti_tolti,
            "risultati_svuotati": self.risultati_svuotati,
            "messaggi_scartati": self.messaggi_scartati,
            "riassunti": int(self.riassunti),
        }


def _role(message: dict[str, Any]) -> str:
    role = message.get("role")
    return role if isinstance(role, str) else ""


def window_start(messages: list[dict[str, Any]], max_messages: int) -> int:
    """Da dove far partire la finestra, tagliando su un confine di turno.

    Pubblica perche' la usa anche la compattazione: il riassunto deve coprire
    esattamente i messaggi che la lettura lascera' fuori, e due regole diverse
    lascerebbero un buco fra cio' che e' riassunto e cio' che si vede.

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


def summary_message(text: str, covers_to_seq: int) -> dict[str, Any]:
    """Il riassunto come messaggio di sistema, riconoscibile al ritorno."""
    return {
        "id": f"{SUMMARY_ID_PREFIX}:{covers_to_seq}",
        "role": "system",
        "content": f"Riassunto della conversazione precedente:\n{text}",
    }


def curate(
    messages: list[dict[str, Any]],
    policy: ContextPolicy,
    summary: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], Curation]:
    """Il contesto da restituire, e il conto di cio' che non c'e' piu'.

    `summary` e' il riassunto dei turni che escono dalla finestra, gia' pronto:
    qui non si chiama nessun modello. La compattazione costa un'inferenza e sta
    fuori dal percorso di lettura, che deve restare veloce e prevedibile.
    """
    start = window_start(messages, policy.max_messages) if policy.max_messages else 0
    window = messages[start:]
    scartati = start

    if policy.drop_reasoning:
        kept = [m for m in window if _role(m) != "reasoning"]
        ragionamenti = len(window) - len(kept)
        window = kept
    else:
        ragionamenti = 0

    tool_indexes = [index for index, m in enumerate(window) if _role(m) == "tool"]
    to_clear = set(tool_indexes[: max(len(tool_indexes) - policy.keep_tool_results, 0)])

    curated: list[dict[str, Any]] = []
    for index, message in enumerate(window):
        if index in to_clear:
            curated.append({**message, "content": CLEARED})
        else:
            curated.append(message)

    riassunti = bool(summary and scartati)
    if riassunti:
        curated = [summary, *curated]

    return curated, Curation(
        conservati=len(curated),
        ragionamenti_tolti=ragionamenti,
        risultati_svuotati=len(to_clear),
        messaggi_scartati=scartati,
        riassunti=riassunti,
    )
