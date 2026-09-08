"""Il calcolo del delta: funzione pura, nessun database."""
from __future__ import annotations

from datetime import UTC, datetime

from memory_service.models import StoredMessage
from memory_service.snapshots import new_messages


def stored(*pairs: tuple[str, str | None]) -> list[StoredMessage]:
    return [
        StoredMessage(
            role=role,
            content=role,
            external_id=external_id,
            seq=index + 1,
            ts=datetime.now(UTC),
        )
        for index, (role, external_id) in enumerate(pairs)
    ]


def test_an_empty_thread_takes_everything():
    incoming = [{"id": "m1", "role": "user", "content": "ciao"}]

    assert [m.external_id for m in new_messages([], incoming)] == ["m1"]


def test_already_written_messages_are_not_rewritten():
    incoming = [
        {"id": "m1", "role": "user", "content": "ciao"},
        {"id": "m2", "role": "assistant", "content": "ok"},
        {"id": "m3", "role": "user", "content": "ancora"},
    ]

    fresh = new_messages(stored(("user", "m1"), ("assistant", "m2")), incoming)

    assert [m.external_id for m in fresh] == ["m3"]


def test_a_resent_snapshot_adds_nothing():
    incoming = [{"id": "m1", "role": "user", "content": "ciao"}]

    assert new_messages(stored(("user", "m1")), incoming) == []


def test_the_original_shape_is_kept_verbatim():
    incoming = [
        {
            "id": "m9",
            "role": "assistant",
            "content": "",
            "toolCalls": [{"id": "c1", "function": {"name": "ui_table"}}],
        }
    ]

    fresh = new_messages([], incoming)

    # Ricostruire la conversazione senza le chiamate ai tool significherebbe
    # restituire al modello qualcosa che non e' mai successo.
    assert fresh[0].payload == incoming[0]
    assert fresh[0].role == "assistant"


def test_messages_without_an_id_fall_back_to_position():
    incoming = [
        {"role": "user", "content": "primo"},
        {"role": "assistant", "content": "secondo"},
        {"role": "user", "content": "terzo"},
    ]

    fresh = new_messages(stored(("user", None), ("assistant", None)), incoming)

    assert [m.content for m in fresh] == ["terzo"]


def test_an_unknown_role_is_kept_not_refused():
    # Un servizio di memoria che rifiuta un ruolo nuovo perde il messaggio.
    fresh = new_messages([], [{"id": "m1", "role": "developer", "content": "x"}])

    assert fresh[0].role == "developer"


def test_a_message_without_a_role_gets_a_declared_placeholder():
    fresh = new_messages([], [{"id": "m1", "content": "x"}])

    assert fresh[0].role == "unknown"


def test_non_textual_content_does_not_become_a_wrong_string():
    fresh = new_messages([], [{"id": "m1", "role": "user", "content": [{"type": "image"}]}])

    assert fresh[0].content == ""
    assert fresh[0].payload == {"id": "m1", "role": "user", "content": [{"type": "image"}]}
