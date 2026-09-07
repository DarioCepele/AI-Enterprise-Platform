"""Raccolta dei log applicativi per il tab LOG del frontend.

Non tocca AG-UI: gli eventi CUSTOM del protocollo sono riservati al framework
(usage, oauth_consent_request, function_approval_request, PredictState) e non
esiste una factory di Content che ne produca uno arbitrario. I log viaggiano
quindi su un endpoint HTTP proprio, che legge questo collettore a cursore.
"""
from __future__ import annotations

import logging
import threading
from collections import deque
from datetime import datetime, timezone
from typing import Any

# Solo i logger dell'applicazione. I logger di libreria (httpx, openai)
# scrivono URL e header di richiesta: inoltrarli al browser significa
# pubblicare la chiave API. Il filtro e' una misura di sicurezza, non estetica.
APP_LOGGER = "demo"

# Il server e' longevo. Il buffer tiene le ultime righe e dichiara quante ne
# ha perse, invece di crescere finche' la memoria finisce.
MAX_LOG_EVENTS = 500


def _timestamp(record: logging.LogRecord) -> str:
    return datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(
        timespec="milliseconds"
    )


class _CollectingHandler(logging.Handler):
    def __init__(self, collector: LogCollector) -> None:
        super().__init__(level=logging.INFO)
        self._collector = collector

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        if record.exc_info:
            # formatException produce il traceback senza il messaggio davanti.
            message = f"{message}\n{self.formatter.formatException(record.exc_info)}"

        self._collector.append(
            {
                "ts": _timestamp(record),
                "level": record.levelname,
                "source": record.name.removeprefix(f"{APP_LOGGER}."),
                "message": message,
            }
        )


class LogCollector:
    """Buffer circolare dei log di `demo.*`, letto a cursore.

    Il cursore e' il numero di sequenza dell'ultima riga vista. Rileggere dallo
    stesso cursore restituisce le stesse righe: un client che ritenta dopo un
    errore di rete non perde nulla.
    """

    def __init__(self) -> None:
        self._entries: deque[dict[str, Any]] = deque(maxlen=MAX_LOG_EVENTS)
        self._handler: _CollectingHandler | None = None
        self._previous_level: int = logging.NOTSET
        self._next_seq = 1
        # uvicorn serve le richieste su piu' thread: append e since si incrociano.
        self._lock = threading.Lock()

    def append(self, entry: dict[str, Any]) -> None:
        with self._lock:
            entry["seq"] = self._next_seq
            self._next_seq += 1
            self._entries.append(entry)

    def attach(self) -> None:
        """Aggancia il collettore al logger `demo`. Chiamarlo due volte non duplica."""
        if self._handler is not None:
            return
        self._handler = _CollectingHandler(self)
        self._handler.setFormatter(logging.Formatter())
        logger = logging.getLogger(APP_LOGGER)
        # logging.getLogger("demo") e' un oggetto globale di processo: se non
        # salviamo il livello di partenza qui, detach() non ha modo di sapere
        # cosa ripristinare e il livello INFO resterebbe per sempre.
        self._previous_level = logger.level
        logger.addHandler(self._handler)
        # Senza questo, il livello ereditato dal root (WARNING) scarta gli INFO.
        logger.setLevel(logging.INFO)

    def detach(self) -> None:
        if self._handler is None:
            return
        logger = logging.getLogger(APP_LOGGER)
        logger.removeHandler(self._handler)
        logger.setLevel(self._previous_level)
        self._handler = None

    def since(self, cursor: int) -> dict[str, Any]:
        """Le righe con seq > cursor, il nuovo cursore, e quante se ne sono perse."""
        with self._lock:
            entries = [dict(e) for e in self._entries if e["seq"] > cursor]
            oldest_kept = self._entries[0]["seq"] if self._entries else self._next_seq
            # Quante righe sono uscite dal buffer prima che il client le leggesse.
            dropped = max(0, oldest_kept - cursor - 1)
            newest = entries[-1]["seq"] if entries else cursor
            return {"entries": entries, "cursor": newest, "dropped": dropped}

    def __enter__(self) -> LogCollector:
        self.attach()
        return self

    def __exit__(self, *exc: object) -> None:
        self.detach()
