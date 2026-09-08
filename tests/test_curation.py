"""La potatura del contesto: funzione pura, nessun database."""
from __future__ import annotations

from memory_service.curation import CLEARED, ContextPolicy, curate


def msg(role: str, content: str = "x", **extra) -> dict:
    return {"role": role, "content": content, **extra}


def turn(user: str = "domanda") -> list[dict]:
    """Un turno completo come lo produce l'agente."""
    return [
        msg("user", user),
        msg("reasoning", "", encrypted_value="[molto lungo]"),
        msg("assistant", "", toolCalls=[{"id": "c1"}]),
        msg("tool", "risultato del tool"),
        msg("assistant", "risposta"),
    ]


def test_nothing_to_curate_is_left_alone():
    messages = [msg("user", "ciao"), msg("assistant", "ok")]

    curated, report = curate(messages, ContextPolicy())

    assert curated == messages
    assert report.as_dict() == {
        "conservati": 2,
        "ragionamenti_tolti": 0,
        "risultati_svuotati": 0,
        "messaggi_scartati": 0,
    }


def test_reasoning_of_past_turns_does_not_come_back():
    curated, report = curate(turn(), ContextPolicy())

    # Il modello rifa' il proprio ragionamento: rimandarglielo costa token e
    # non aggiunge nulla.
    assert [m["role"] for m in curated] == ["user", "assistant", "tool", "assistant"]
    assert report.ragionamenti_tolti == 1


def test_reasoning_can_be_kept_when_asked():
    curated, report = curate(turn(), ContextPolicy(drop_reasoning=False))

    assert any(m["role"] == "reasoning" for m in curated)
    assert report.ragionamenti_tolti == 0


def test_old_tool_results_are_emptied_but_the_call_stays():
    messages = [msg("tool", f"risultato {i}") for i in range(6)]

    curated, report = curate(messages, ContextPolicy(keep_tool_results=2))

    # Quattro svuotati, due interi: la traccia della chiamata resta in tutti.
    assert [m["content"] for m in curated] == [CLEARED] * 4 + ["risultato 4", "risultato 5"]
    assert len(curated) == 6
    assert report.risultati_svuotati == 4


def test_the_most_recent_results_survive_whole():
    messages = [msg("tool", "vecchio"), msg("tool", "recente")]

    curated, _ = curate(messages, ContextPolicy(keep_tool_results=1))

    assert curated[-1]["content"] == "recente"


def test_the_window_cuts_on_a_turn_boundary():
    messages = turn("primo") + turn("secondo") + turn("terzo")

    curated, report = curate(
        messages, ContextPolicy(drop_reasoning=False, keep_tool_results=99, max_messages=7)
    )

    # Non a meta' turno: il primo messaggio conservato e' quello dell'utente,
    # altrimenti resterebbe un risultato di tool senza la sua chiamata.
    assert curated[0]["role"] == "user"
    assert curated[0]["content"] == "secondo"
    assert report.messaggi_scartati == 5


def test_a_conversation_without_turn_boundaries_is_kept_whole():
    # Meglio un contesto lungo di uno incoerente: senza un confine su cui
    # tagliare non si taglia.
    messages = [msg("assistant", str(i)) for i in range(10)]

    curated, report = curate(messages, ContextPolicy(max_messages=3))

    assert len(curated) == 10
    assert report.messaggi_scartati == 0


def test_the_last_user_message_is_never_dropped():
    messages = turn("vecchio") + [msg("user", "ultimo")]

    curated, _ = curate(messages, ContextPolicy(max_messages=1))

    assert curated[-1]["content"] == "ultimo"


def test_the_original_messages_are_not_modified():
    messages = [msg("tool", "risultato"), msg("tool", "altro"), msg("tool", "terzo")]

    curate(messages, ContextPolicy(keep_tool_results=1))

    # La potatura e' in lettura: il transcript in memoria resta quello che era.
    assert [m["content"] for m in messages] == ["risultato", "altro", "terzo"]


def test_curation_counts_what_survived():
    curated, report = curate(turn() + turn(), ContextPolicy(keep_tool_results=1))

    assert report.conservati == len(curated)
    assert report.ragionamenti_tolti == 2
    assert report.risultati_svuotati == 1
