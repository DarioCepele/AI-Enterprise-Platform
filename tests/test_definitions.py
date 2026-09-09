"""A process definition is data, and a wrong one says which step is wrong."""
from __future__ import annotations

import pytest

from process_service.definitions import DefinitionError, ProcessDefinition, parse_definition

VALID = {
    "id": "example-approval",
    "version": 1,
    "name": "Example approval",
    "steps": [
        {"id": "collect", "type": "tool", "tool": "collect_request"},
        {"id": "check", "type": "agent", "owner": "knowledge", "depends_on": ["collect"]},
        {
            "id": "decide",
            "type": "decision",
            "depends_on": ["check"],
            "branches": [
                {"when": "amount > 10000", "goto": "approval"},
                {"when": "true", "goto": "execute"},
            ],
        },
        {"id": "approval", "type": "approval", "approvers": ["finance"]},
        {"id": "execute", "type": "tool", "tool": "apply", "idempotency_key": "request_id"},
    ],
}


def test_a_valid_definition_is_parsed():
    definition = parse_definition(VALID)

    assert isinstance(definition, ProcessDefinition)
    assert definition.id == "example-approval"
    assert definition.version == 1
    assert [step.id for step in definition.steps] == [
        "collect",
        "check",
        "decide",
        "approval",
        "execute",
    ]


def test_the_first_steps_are_the_ones_nobody_leads_to():
    definition = parse_definition(VALID)

    # `approval` and `execute` have no dependencies either, but a branch leads
    # to them: starting them too would run both sides of the decision.
    assert [step.id for step in definition.entry_steps()] == ["collect"]


def test_two_steps_with_the_same_id_are_refused_by_name():
    broken = {**VALID, "steps": [*VALID["steps"], {"id": "collect", "type": "tool", "tool": "x"}]}

    with pytest.raises(DefinitionError, match="collect"):
        parse_definition(broken)


def test_a_dependency_that_does_not_exist_is_named():
    broken = {
        **VALID,
        "steps": [{"id": "only", "type": "tool", "tool": "x", "depends_on": ["ghost"]}],
    }

    with pytest.raises(DefinitionError, match="ghost"):
        parse_definition(broken)


def test_a_cycle_is_refused_with_the_steps_that_form_it():
    broken = {
        **VALID,
        "steps": [
            {"id": "a", "type": "tool", "tool": "x", "depends_on": ["b"]},
            {"id": "b", "type": "tool", "tool": "x", "depends_on": ["a"]},
        ],
    }

    with pytest.raises(DefinitionError, match="a, b|b, a"):
        parse_definition(broken)


def test_a_branch_pointing_nowhere_is_named():
    broken = {
        **VALID,
        "steps": [
            {"id": "collect", "type": "tool", "tool": "x"},
            {
                "id": "decide",
                "type": "decision",
                "depends_on": ["collect"],
                "branches": [{"when": "true", "goto": "elsewhere"}],
            },
        ],
    }

    with pytest.raises(DefinitionError, match="elsewhere"):
        parse_definition(broken)


def test_an_agent_step_without_an_owner_is_refused():
    broken = {**VALID, "steps": [{"id": "ask", "type": "agent"}]}

    # Who runs a step is not a detail: without an owner the engine would have to
    # guess, and guessing is what a process definition exists to avoid.
    with pytest.raises(DefinitionError, match="ask"):
        parse_definition(broken)


def test_a_tool_step_without_a_tool_is_refused():
    broken = {**VALID, "steps": [{"id": "do", "type": "tool"}]}

    with pytest.raises(DefinitionError, match="do"):
        parse_definition(broken)


def test_a_decision_without_branches_is_refused():
    broken = {**VALID, "steps": [{"id": "pick", "type": "decision"}]}

    with pytest.raises(DefinitionError, match="pick"):
        parse_definition(broken)


def test_an_unknown_step_type_is_refused_saying_which_ones_exist():
    broken = {**VALID, "steps": [{"id": "weird", "type": "teleport"}]}

    with pytest.raises(DefinitionError, match="teleport"):
        parse_definition(broken)


def test_the_version_is_part_of_the_identity():
    first = parse_definition(VALID)
    second = parse_definition({**VALID, "version": 2})

    # An instance finishes with the version it started with, so the pair
    # (id, version) is what a running instance refers to.
    assert first.key() == ("example-approval", 1)
    assert second.key() == ("example-approval", 2)
