"""The conditions of a branch, evaluated without running arbitrary code.

A rule in a process definition is a business rule, and a definition is data that
somebody may upload. `eval` here would mean handing the process over to whoever
writes the YAML.

The grammar is deliberately small: `true`, `false`, and `field op value` with
op in ==, !=, >, >=, <, <=. Anything else is refused by name, which is a better
failure than a rule that quietly reads as false.
"""
from __future__ import annotations

import re
from typing import Any

CONDITION = re.compile(
    r"^\s*(?P<field>[A-Za-z_][A-Za-z0-9_.]*)\s*(?P<operator>==|!=|>=|<=|>|<)\s*(?P<value>.+?)\s*$"
)

OPERATORS = {
    "==": lambda left, right: left == right,
    "!=": lambda left, right: left != right,
    ">": lambda left, right: left > right,
    ">=": lambda left, right: left >= right,
    "<": lambda left, right: left < right,
    "<=": lambda left, right: left <= right,
}


class ConditionError(ValueError):
    """A condition that cannot be evaluated, and why."""


def evaluate(condition: str, context: dict[str, Any]) -> bool:
    text = condition.strip()
    if text in ("true", "True"):
        return True
    if text in ("false", "False"):
        return False

    match = CONDITION.match(text)
    if match is None:
        raise ConditionError(
            f"condition '{condition}' is not understood: expected 'true', 'false' "
            "or 'field <op> value'"
        )

    left = _lookup(match.group("field"), context)
    right = _literal(match.group("value"))
    try:
        return bool(OPERATORS[match.group("operator")](left, right))
    except TypeError as error:
        raise ConditionError(
            f"condition '{condition}': cannot compare {left!r} with {right!r} ({error})"
        ) from error


def _lookup(path: str, context: dict[str, Any]) -> Any:
    """Dotted lookup, missing keys become None instead of an exception."""
    value: Any = context
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _literal(text: str) -> Any:
    stripped = text.strip()
    if stripped.startswith(("'", '"')) and stripped[-1:] == stripped[0]:
        return stripped[1:-1]
    if stripped in ("true", "True"):
        return True
    if stripped in ("false", "False"):
        return False
    if stripped in ("null", "None"):
        return None
    try:
        return int(stripped)
    except ValueError:
        pass
    try:
        return float(stripped)
    except ValueError:
        return stripped
