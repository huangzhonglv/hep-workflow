"""Strict JSON loading for repository trust boundaries.

The standard-library decoder intentionally accepts JavaScript-style non-finite
constants and silently keeps the last occurrence of duplicate object keys.
Both behaviours are unsafe for schemas and scientific configuration, so all
repository loaders use the helpers in this module instead.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Iterable


class StrictJSONError(ValueError):
    """Raised when input is not unambiguous RFC-compliant JSON."""


def _reject_constant(token: str) -> Any:
    raise StrictJSONError(f"non-finite numeric constant is not valid JSON: {token}")


def _parse_finite_float(token: str) -> float:
    value = float(token)
    if not math.isfinite(value):
        raise StrictJSONError(f"non-finite decoded number: {token}")
    mantissa = token.lower().split("e", 1)[0]
    if value == 0.0 and any(character in "123456789" for character in mantissa):
        raise StrictJSONError(f"numeric underflow is not supported: {token}")
    return value


def _parse_finite_int(token: str) -> int:
    try:
        value = int(token)
        converted = float(value)
    except (OverflowError, ValueError) as exc:
        raise StrictJSONError(f"integer exceeds the supported numeric range: {token[:32]}") from exc
    if not math.isfinite(converted):
        raise StrictJSONError(f"integer exceeds the supported numeric range: {token[:32]}")
    return value


def _reject_duplicate_keys(pairs: Iterable[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise StrictJSONError(f"duplicate object key: {key!r}")
        result[key] = value
    return result


def _reject_non_finite_values(value: Any, path: str = "<root>") -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise StrictJSONError(f"non-finite decoded number at {path}")
    if isinstance(value, list):
        for index, item in enumerate(value):
            _reject_non_finite_values(item, f"{path}[{index}]")
    elif isinstance(value, dict):
        for key, item in value.items():
            _reject_non_finite_values(item, f"{path}.{key}")


def loads_json(text: str, *, source: str = "<string>") -> Any:
    """Decode strict JSON and include its source in actionable failures."""

    try:
        payload = json.loads(
            text,
            parse_constant=_reject_constant,
            parse_float=_parse_finite_float,
            parse_int=_parse_finite_int,
            object_pairs_hook=_reject_duplicate_keys,
        )
        _reject_non_finite_values(payload)
        return payload
    except StrictJSONError as exc:
        raise StrictJSONError(f"invalid JSON in {source}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise StrictJSONError(f"invalid JSON in {source}: {exc}") from exc
    except RecursionError as exc:
        raise StrictJSONError(
            f"invalid JSON in {source}: nesting exceeds the supported parser depth"
        ) from exc


def load_json(path: Path) -> Any:
    """Read and decode one strict JSON file."""

    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise StrictJSONError(f"cannot read JSON file {path}: {exc}") from exc
    return loads_json(text, source=str(path))
