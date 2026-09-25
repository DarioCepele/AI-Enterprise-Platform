"""Pure tests for parsing model-generated facts."""
from __future__ import annotations

from memory_service.facts import Fact, facts_message, parse_facts


def test_a_clean_list_is_read_as_is():
    raw = '[{"key": "contact", "value": "Marta"}]'

    assert parse_facts(raw) == [Fact("contact", "Marta")]


def test_a_json_wrapped_in_markdown_is_still_read():
    raw = '```json\n[{"key": "budget", "value": "18k"}]\n```'

    assert parse_facts(raw) == [Fact("budget", "18k")]


def test_an_object_with_a_facts_field_is_accepted():
    raw = '{"facts": [{"key": "city", "value": "Torino"}]}'

    assert parse_facts(raw) == [Fact("city", "Torino")]


def test_english_keys_are_accepted_too():
    raw = '[{"key": "role", "value": "backend"}]'

    assert parse_facts(raw) == [Fact("role", "backend")]


def test_half_facts_are_dropped():
    raw = '[{"key": "contact"}, {"value": "value only"}, {"key": "ok", "value": "yes"}]'

    assert parse_facts(raw) == [Fact("ok", "yes")]


def test_a_key_that_is_a_sentence_is_refused():
    raw = '[{"key": "The project contact is Marta", "value": "Marta"}]'

    assert parse_facts(raw) == []


def test_the_same_key_twice_keeps_the_first():
    raw = '[{"key": "city", "value": "Torino"}, {"key": "city", "value": "Milano"}]'

    assert parse_facts(raw) == [Fact("city", "Torino")]


def test_unreadable_output_is_ignored_not_raised():
    assert parse_facts("sorry, I found no facts") == []
    assert parse_facts("") == []


def test_the_injected_message_says_what_wins_in_a_conflict():
    message = facts_message([{"key": "city", "value": "Torino"}])

    assert message["role"] == "system"
    assert message["id"].startswith("memory:")
    assert "city: Torino" in message["content"]
    assert "what they say now wins" in message["content"]
