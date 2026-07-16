"""Shared validation primitives for workflow and canonical identifiers."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Final


# Keep this set explicit so the repository contract does not change merely because
# a newer Python interpreter adds a soft keyword.  These are the hard keywords
# shared by the supported Python 3.11-3.13 runtimes.
PYTHON_KEYWORDS: Final[frozenset[str]] = frozenset(
    {
        "False",
        "None",
        "True",
        "and",
        "as",
        "assert",
        "async",
        "await",
        "break",
        "class",
        "continue",
        "def",
        "del",
        "elif",
        "else",
        "except",
        "finally",
        "for",
        "from",
        "global",
        "if",
        "import",
        "in",
        "is",
        "lambda",
        "nonlocal",
        "not",
        "or",
        "pass",
        "raise",
        "return",
        "try",
        "while",
        "with",
        "yield",
    }
)

_CANONICAL_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*", re.ASCII)
_ANALYSIS_ID = re.compile(r"analysis-[0-9]{3}", re.ASCII)
_REPRO_ID = re.compile(r"run-[0-9]{3}", re.ASCII)
_TASK_ID = re.compile(r"task-[0-9]{3}", re.ASCII)
_CONSTRAINT_ID = re.compile(r"c-[0-9]{3}", re.ASCII)
_NUMERICS_HISTORY_ANALYSIS_TOKEN = re.compile(
    r"(?<![A-Za-z0-9_-])analysis_id=(analysis-[0-9]{3})(?![A-Za-z0-9_-])",
    re.ASCII,
)


def _validate_pattern(value: object, pattern: re.Pattern[str], label: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ValueError(f"{label} has an invalid format: {value!r}")
    return value


def validate_canonical_identifier(
    value: object,
    label: str = "identifier",
) -> str:
    """Return a Python-compatible canonical identifier or raise ``ValueError``."""

    identifier = _validate_pattern(value, _CANONICAL_IDENTIFIER, label)
    if identifier in PYTHON_KEYWORDS:
        raise ValueError(f"{label} must not be a Python keyword: {identifier!r}")
    return identifier


def validate_analysis_id(value: object, label: str = "analysis_id") -> str:
    """Return an ASCII ``analysis-NNN`` identifier or raise ``ValueError``."""

    return _validate_pattern(value, _ANALYSIS_ID, label)


def numerics_history_analysis_id(entry: object) -> str | None:
    """Return one unambiguous analysis identity from a history entry.

    New producers write the explicit ``analysis_id`` field. Exact note-token
    parsing remains a read-only compatibility path for legacy manifests; loose
    substring matching is deliberately forbidden.
    """

    if not isinstance(entry, dict):
        raise ValueError("manifest history entry must be an object")
    explicit = entry.get("analysis_id")
    if explicit is not None:
        explicit = validate_analysis_id(explicit, "history analysis_id")

    note = entry.get("note")
    note_ids = (
        _NUMERICS_HISTORY_ANALYSIS_TOKEN.findall(note)
        if isinstance(note, str)
        else []
    )
    unique_note_ids = sorted(set(note_ids))
    if len(unique_note_ids) > 1:
        raise ValueError(
            "numerics history note contains multiple analysis_id tokens: "
            f"{unique_note_ids}"
        )
    note_id = unique_note_ids[0] if unique_note_ids else None
    if explicit is not None and note_id is not None and explicit != note_id:
        raise ValueError(
            "numerics history analysis_id disagrees with its exact note token: "
            f"{explicit!r} != {note_id!r}"
        )
    return explicit if explicit is not None else note_id


def validate_repro_id(value: object, label: str = "repro_id") -> str:
    """Return an ASCII ``run-NNN`` identifier or raise ``ValueError``."""

    return _validate_pattern(value, _REPRO_ID, label)


def validate_task_id(value: object, label: str = "task_id") -> str:
    """Return an ASCII ``task-NNN`` identifier or raise ``ValueError``."""

    return _validate_pattern(value, _TASK_ID, label)


def validate_constraint_id(value: object, label: str = "constraint_id") -> str:
    """Return an ASCII ``c-NNN`` identifier or raise ``ValueError``."""

    return _validate_pattern(value, _CONSTRAINT_ID, label)


def figure_output_key(figure_spec: dict[str, Any]) -> str:
    """Return the deterministic basename owned by one validated figure spec."""

    kind = figure_spec.get("kind")
    if kind == "exclusion_2d":
        x_name = validate_canonical_identifier(
            figure_spec.get("x"), "figure x identifier"
        )
        y_name = validate_canonical_identifier(
            figure_spec.get("y"), "figure y identifier"
        )
        return f"exclusion-{x_name}-{y_name}"
    if kind == "scan_1d":
        x_name = validate_canonical_identifier(
            figure_spec.get("x"), "figure x identifier"
        )
        observables = figure_spec.get("observables")
        if not isinstance(observables, list) or not observables:
            raise ValueError("scan_1d figure observables must be a non-empty list")
        observable_fragment = "--".join(
            validate_canonical_identifier(item, "figure observable identifier")
            for item in observables
        )
        return f"scan1d-{x_name}-{observable_fragment}"
    raise ValueError(f"unsupported figure kind {kind!r}")


def validate_figure_output_keys(scan_config: dict[str, Any]) -> list[str]:
    """Return unique output basenames or reject cross-spec output collisions."""

    figures = scan_config.get("figures", [])
    if not isinstance(figures, list):
        raise ValueError("figures must be a list")
    owners: dict[str, int] = {}
    keys: list[str] = []
    for index, figure_spec in enumerate(figures):
        if not isinstance(figure_spec, dict):
            raise ValueError(f"figures[{index}] must be an object")
        key = figure_output_key(figure_spec)
        prior = owners.get(key)
        if prior is not None:
            raise ValueError(
                f"figures[{index}] and figures[{prior}] map to the same output "
                f"basename {key!r}"
            )
        owners[key] = index
        keys.append(key)
    return keys


def _relative_parts(candidate: os.PathLike[str] | str, label: str) -> tuple[str, ...]:
    try:
        raw = os.fspath(candidate)
    except TypeError as exc:
        raise ValueError(f"{label} must be a relative filesystem path") from exc
    if not isinstance(raw, str) or not raw:
        raise ValueError(f"{label} must be a non-empty relative filesystem path")
    if "\\" in raw:
        raise ValueError(f"{label} must not contain backslashes")
    path = Path(raw)
    if path.is_absolute():
        raise ValueError(f"{label} must be relative, not absolute")
    parts = tuple(raw.split("/"))
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"{label} must not contain empty or dot path segments")
    return parts


def resolve_contained(
    root: os.PathLike[str] | str,
    candidate: os.PathLike[str] | str,
    label: str,
    reject_symlinks: bool = True,
) -> Path:
    """Resolve one relative path beneath ``root`` without normalization escapes.

    Absolute candidates, backslashes, empty/dot segments, symlink components, and
    resolved paths outside the designated root are rejected before callers write.
    ``root`` itself is the trusted anchor; symlink checks apply to candidate
    components below that anchor.
    """

    parts = _relative_parts(candidate, label)
    root_path = Path(root).resolve(strict=False)
    lexical = root_path.joinpath(*parts)

    if reject_symlinks:
        current = root_path
        for part in parts:
            current = current / part
            if current.is_symlink():
                raise ValueError(f"{label} contains a symlink component: {part!r}")

    resolved = lexical.resolve(strict=False)
    if not resolved.is_relative_to(root_path):
        raise ValueError(f"{label} escapes its designated root")
    return resolved


def validate_named_json_path(
    path: os.PathLike[str] | str,
    root: os.PathLike[str] | str,
    identifier: object,
    label: str,
) -> Path:
    """Bind one analysis JSON path to an exact canonical ``analysis-NNN`` name."""

    identifier = validate_analysis_id(identifier, f"{label} identifier")

    try:
        raw_path = os.fspath(path)
    except TypeError as exc:
        raise ValueError(f"{label} must be a filesystem path") from exc
    if not isinstance(raw_path, str) or not raw_path:
        raise ValueError(f"{label} must be a non-empty filesystem path")
    if "\\" in raw_path:
        raise ValueError(f"{label} must not contain backslashes")

    candidate = Path(raw_path)
    root_path = Path(root).resolve(strict=False)
    if candidate.is_absolute():
        raw_parts = raw_path.split("/")[1:]
        if any(part in {"", ".", ".."} for part in raw_parts):
            raise ValueError(f"{label} must not contain empty or dot path segments")
        resolved = candidate.resolve(strict=False)
        if not resolved.is_relative_to(root_path):
            raise ValueError(f"{label} escapes its designated root")
        try:
            relative = candidate.relative_to(root_path)
        except ValueError:
            relative = resolved.relative_to(root_path)
        current = root_path
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise ValueError(
                    f"{label} contains a symlink component: {part!r}"
                )
        actual = resolved
    else:
        actual = resolve_contained(root_path, raw_path, label)
    expected = resolve_contained(root, f"{identifier}.json", label)
    if actual != expected:
        raise ValueError(
            f"{label} must be named {identifier}.json under its designated root"
        )
    return actual
