"""Misura del contesto: quanto entra nella finestra, a ogni chiamata al modello.

Il contesto e' una risorsa finita, e le soglie con cui potarlo -- quando
riassumere, quando svuotare i risultati dei tool -- vanno scelte su numeri
misurati, non a occhio. Questo middleware produce quei numeri.

Registra solo **conteggi**, mai il contenuto dei messaggi: i log finiscono nel
tab LOG del frontend, e la conversazione non e' materiale da diagnostica.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from agent_framework import ChatContext, ChatResponse, Message, chat_middleware

logger = logging.getLogger(__name__)

# Content che portano il risultato di un tool. Contarli a parte serve a decidere
# se convenga svuotarli (tool clearing) prima di riassumere il resto.
_RESULT_ATTRS = ("result", "output")


def _content_size(content: Any) -> int:
    """Caratteri di un content, qualunque forma abbia."""
    text = getattr(content, "text", None)
    if isinstance(text, str):
        return len(text)
    arguments = getattr(content, "arguments", None)
    if arguments is not None:
        return len(str(arguments))
    for attr in _RESULT_ATTRS:
        value = getattr(content, attr, None)
        if value is not None:
            return len(str(value))
    return 0


def _is_tool_result(content: Any) -> bool:
    return any(getattr(content, attr, None) is not None for attr in _RESULT_ATTRS)


def measure(messages: Sequence[Message]) -> dict[str, int]:
    """Dimensione del contesto in partenza: messaggi, caratteri, quota dei tool.

    I caratteri sono un proxy dei token, disponibile anche quando il provider
    non riporta l'uso. Il rapporto e' grossolano ma stabile, e basta per
    vedere una curva che cresce.
    """
    total = 0
    tool_chars = 0
    for message in messages:
        for content in getattr(message, "contents", None) or []:
            size = _content_size(content)
            total += size
            if _is_tool_result(content):
                tool_chars += size
    return {"messages": len(messages), "chars": total, "tool_chars": tool_chars}


def _usage(response: object) -> dict[str, int]:
    """Token realmente consumati, quando il provider li dichiara."""
    details = getattr(response, "usage_details", None) or {}
    return {
        "input_tokens": int(details.get("input_token_count") or 0),
        "output_tokens": int(details.get("output_token_count") or 0),
    }


def _log(size: dict[str, int], usage: dict[str, int]) -> None:
    tokens = (
        f"{usage['input_tokens']} token in, {usage['output_tokens']} out"
        if usage["input_tokens"] or usage["output_tokens"]
        else "token non riportati dal provider"
    )
    logger.info(
        "Contesto: %d messaggi, %d caratteri (%d dai tool); %s.",
        size["messages"],
        size["chars"],
        size["tool_chars"],
        tokens,
    )


@chat_middleware
async def log_context_size(
    context: ChatContext,
    call_next: Callable[[], Awaitable[None]],
) -> None:
    """Registra la dimensione del contesto a ogni chiamata al modello.

    Una run con tool fa piu' di una chiamata: ognuna ha la sua riga, ed e'
    esattamente li' che si vede il contesto gonfiarsi dentro la stessa run.
    """
    size = measure(context.messages)

    # Nello streaming il conteggio dei token esiste solo alla fine, quando lo
    # stream e' stato consumato e la risposta finalizzata.
    def _on_final(response: ChatResponse) -> ChatResponse:
        _log(size, _usage(response))
        return response

    context.stream_result_hooks.append(_on_final)
    await call_next()

    if not context.stream:
        _log(size, _usage(context.result))
