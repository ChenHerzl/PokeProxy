from __future__ import annotations

import json
import re
from pathlib import Path

from pokeproxy.config import MatchCondition, Operator, PokemonJSON, Rule

FIELD_TYPES: dict[str, type[int] | type[str] | type[bool]] = {
    "number": int,
    "name": str,
    "type_one": str,
    "type_two": str,
    "total": int,
    "hit_points": int,
    "attack": int,
    "defense": int,
    "special_attack": int,
    "special_defense": int,
    "speed": int,
    "generation": int,
    "legendary": bool,
}

if set(FIELD_TYPES) != set(PokemonJSON.model_fields):
    raise TypeError("FIELD_TYPES out of sync with PokemonJSON")

_CONDITION_RE = re.compile(r"^\s*(\w+)\s*(==|!=|>|<)\s*(.*?)\s*$")


def parse_condition(expr: str) -> MatchCondition:
    """Parse a condition string like 'attack>80' into a MatchCondition."""
    if re.search(r"(>=|<=)", expr):
        raise ValueError(
            f"Unsupported operator in: {expr!r}. Only ==, !=, >, < are allowed."
        )

    match = _CONDITION_RE.match(expr)
    if not match:
        raise ValueError(f"Invalid condition syntax: {expr!r}")

    field_name, op_str, raw_value = match.group(1), match.group(2), match.group(3)

    if field_name not in FIELD_TYPES:
        raise ValueError(f"Unknown field: {field_name!r}")

    field_type = FIELD_TYPES[field_name]
    op = _validate_operator(op_str, field_name, field_type)
    value = _coerce_value(raw_value, field_type, field_name)

    return MatchCondition(field=field_name, operator=op, value=value)  # type: ignore[arg-type]


def _validate_operator(
    op: str, field_name: str, field_type: type[int] | type[str] | type[bool]
) -> Operator:
    if field_type is bool and op in (">", "<"):
        raise ValueError(f"Operator {op!r} not supported for bool field {field_name!r}")
    return op  # type: ignore[return-value]


def _coerce_value(
    raw: str, field_type: type[int] | type[str] | type[bool], field_name: str
) -> int | str | bool:
    if field_type is bool:
        lower = raw.lower()
        if lower == "true":
            return True
        if lower == "false":
            return False
        raise ValueError(
            f"Bool field {field_name!r} requires 'true' or 'false', got {raw!r}"
        )
    if field_type is int:
        try:
            return int(raw)
        except ValueError:
            raise ValueError(
                f"Numeric field {field_name!r} requires an integer value, got {raw!r}"
            ) from None
    return raw


def evaluate_condition(cond: MatchCondition, pokemon: PokemonJSON) -> bool:
    """Evaluate a single condition against a Pokemon."""
    actual = getattr(pokemon, cond.field)
    expected = cond.value

    match cond.operator:
        case "==":
            return actual == expected  # type: ignore[no-any-return]
        case "!=":
            return actual != expected  # type: ignore[no-any-return]
        case ">":
            return actual > expected  # type: ignore[no-any-return]
        case "<":
            return actual < expected  # type: ignore[no-any-return]
        case _:
            raise ValueError(f"Unknown operator: {cond.operator!r}")


def match_pokemon(pokemon: PokemonJSON, rules: list[Rule]) -> Rule | None:
    """Return the first matching rule, or None if no rule matches."""
    for rule in rules:
        if all(evaluate_condition(c, pokemon) for c in rule.conditions):
            return rule
    return None


def load_rules(config_path: str) -> list[Rule]:
    """Load and validate rules from a JSON config file."""
    path = Path(config_path)
    data = json.loads(path.read_text())

    raw_rules: list[dict[str, object]] = data.get("rules", [])
    rules: list[Rule] = []

    for raw_rule in raw_rules:
        url = str(raw_rule.get("url", ""))
        if not url.startswith(("http://", "https://")):
            raise ValueError(f"Invalid rule URL: {url!r} — must start with http(s)://")

        reason = str(raw_rule.get("reason", ""))
        match_exprs = raw_rule.get("match", [])
        if not isinstance(match_exprs, list):
            raise ValueError(
                f"'match' must be a list, got {type(match_exprs).__name__}"
            )
        if not match_exprs:
            raise ValueError("Rule must have at least one match condition")

        conditions = [parse_condition(str(expr)) for expr in match_exprs]
        rules.append(Rule(url=url, reason=reason, conditions=conditions))

    return rules
