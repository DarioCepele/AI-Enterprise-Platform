"""La lettura dei fatti prodotti dal modello: funzione pura."""
from __future__ import annotations

from memory_service.facts import Fact, facts_message, parse_facts


def test_a_clean_list_is_read_as_is():
    raw = '[{"chiave": "referente", "valore": "Marta"}]'

    assert parse_facts(raw) == [Fact("referente", "Marta")]


def test_a_json_wrapped_in_markdown_is_still_read():
    raw = '```json\n[{"chiave": "budget", "valore": "18k"}]\n```'

    assert parse_facts(raw) == [Fact("budget", "18k")]


def test_an_object_with_a_facts_field_is_accepted():
    raw = '{"fatti": [{"chiave": "citta", "valore": "Torino"}]}'

    assert parse_facts(raw) == [Fact("citta", "Torino")]


def test_english_keys_are_accepted_too():
    raw = '[{"key": "role", "value": "backend"}]'

    assert parse_facts(raw) == [Fact("role", "backend")]


def test_half_facts_are_dropped():
    raw = '[{"chiave": "referente"}, {"valore": "solo valore"}, {"chiave": "ok", "valore": "si"}]'

    assert parse_facts(raw) == [Fact("ok", "si")]


def test_a_key_that_is_a_sentence_is_refused():
    raw = '[{"chiave": "Il referente del progetto e Marta", "valore": "Marta"}]'

    assert parse_facts(raw) == []


def test_the_same_key_twice_keeps_the_first():
    raw = '[{"chiave": "citta", "valore": "Torino"}, {"chiave": "citta", "valore": "Milano"}]'

    assert parse_facts(raw) == [Fact("citta", "Torino")]


def test_unreadable_output_is_ignored_not_raised():
    assert parse_facts("mi dispiace, non ho trovato fatti") == []
    assert parse_facts("") == []


def test_the_injected_message_says_what_wins_in_a_conflict():
    message = facts_message([{"chiave": "citta", "valore": "Torino"}])

    assert message["role"] == "system"
    assert message["id"].startswith("memoria:")
    assert "citta: Torino" in message["content"]
    assert "vale quello che dice adesso" in message["content"]
