from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlsplit

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
    try:
        data = json.loads(Path(config_path).read_text())
    except (OSError, ValueError):
        raise ValueError(
            "Routing configuration must be a readable UTF-8 JSON file"
        ) from None
    if (
        not isinstance(data, dict)
        or set(data) != {"rules"}
        or not isinstance(data["rules"], list)
    ):
        raise ValueError(
            "Routing configuration requires a 'rules' list and no unknown fields"
        )
    rules = []
    for index, raw in enumerate(data["rules"]):
        field = "schema (url, reason, match required)"
        try:
            if not isinstance(raw, dict) or set(raw) != {"url", "reason", "match"}:
                raise ValueError("required fields are url, reason, match")
            url, reason, expressions = raw["url"], raw["reason"], raw["match"]
            field = "url (HTTP(S) host without credentials/fragment)"
            if not isinstance(url, str) or any(c.isspace() or ord(c) < 32 for c in url):
                raise ValueError("URL must be an HTTP(S) string without whitespace")
            parsed = urlsplit(url)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.fragment
                or (parsed.port is not None and parsed.port < 1)
            ):
                raise ValueError(
                    "URL requires an HTTP(S) host and no credentials or fragment"
                )
            field = "reason (printable ASCII, at most 1024 characters)"
            if (
                not isinstance(reason, str)
                or len(reason) > 1024
                or any(ord(c) < 32 or ord(c) > 126 for c in reason)
            ):
                raise ValueError("reason must be printable ASCII")
            field = "match (nonempty list of valid condition strings)"
            if (
                not isinstance(expressions, list)
                or not expressions
                or not all(isinstance(e, str) for e in expressions)
            ):
                raise ValueError("match must be a nonempty list of strings")
            conditions = [parse_condition(e) for e in expressions]
            rules.append(Rule(url=url, reason=reason, conditions=conditions))
        except (ValueError, TypeError):
            # Never include URLs, credentials, or raw configuration in startup errors.
            raise ValueError(
                f"Invalid routing rule at index {index}: {field}"
            ) from None
    return rules
