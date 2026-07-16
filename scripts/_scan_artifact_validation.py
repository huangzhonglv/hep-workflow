"""Strict semantic validation for a completed hep-numerics scan artifact pair.

This module is deliberately side-effect free.  It validates the immutable
relationship between a scan config, ``scan.csv``, ``scan.meta.json``, and the
generated analysis summary; callers decide how validation failures affect
their workflow.
"""

from __future__ import annotations

import ast
import csv
import hashlib
import json
import keyword
import math
import re
from pathlib import Path
from typing import Any

import numpy as np

try:
    from _strict_json import StrictJSONError, load_json, loads_json
except ModuleNotFoundError:  # Imported as scripts._scan_artifact_validation.
    from scripts._strict_json import StrictJSONError, load_json, loads_json

try:
    from _identity import resolve_contained, validate_figure_output_keys
except ModuleNotFoundError:  # Imported as scripts._scan_artifact_validation.
    from scripts._identity import resolve_contained, validate_figure_output_keys


ANALYSIS_ID_PATTERN = re.compile(r"^analysis-[0-9]{3}$", re.ASCII)
CANONICAL_IDENTIFIER_PATTERN = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*$",
    re.ASCII,
)
RUN_SCAN_META_REQUIRED_FIELDS = frozenset(
    {
        "analysis_id",
        "history_action",
        "scan_config_snapshot",
        "scan_config_source",
        "scan_config_sha256",
        "model_version",
        "model_checksum",
        "seed",
        "rng",
        "started_at",
        "finished_at",
        "timing_seconds",
        "timing",
        "n_points",
        "n_allowed",
        "n_excluded",
        "n_skipped",
        "environment",
        "formula_fallbacks",
        "warnings",
    }
)

_SCAN_EXECUTION_KEYS = (
    "analysis_id",
    "model_name",
    "depends_on",
    "scan_parameters",
    "fixed_parameters",
    "observables",
    "constraints_used",
    "figures",
    "allow_formula_fallback",
    "seed",
)


def _namespace_columns(config: Any) -> tuple[list[str], list[str]]:
    columns: list[str] = []
    issues: list[str] = []
    owners: dict[str, str] = {}

    if not isinstance(config, dict):
        return [], ["scan config must be an object"]

    def add_column(value: Any, owner: str) -> None:
        if not isinstance(value, str) or not value:
            issues.append(f"{owner} must define a non-empty string column name")
            return
        prior_owner = owners.get(value)
        if prior_owner is not None:
            issues.append(
                f"scan output column {value!r} collides between {prior_owner} and {owner}"
            )
        else:
            owners[value] = owner
        columns.append(value)

    for collection_name in ("scan_parameters", "fixed_parameters"):
        collection = config.get(collection_name)
        if not isinstance(collection, list):
            issues.append(f"{collection_name} must be an array")
            continue
        for index, item in enumerate(collection):
            if not isinstance(item, dict):
                issues.append(f"{collection_name}[{index}] must be an object")
                continue
            add_column(
                item.get("canonical_name"),
                f"{collection_name}[{index}].canonical_name",
            )

    observables = config.get("observables")
    if not isinstance(observables, list):
        issues.append("observables must be an array")
    else:
        for index, item in enumerate(observables):
            if not isinstance(item, dict):
                issues.append(f"observables[{index}] must be an object")
                continue
            add_column(item.get("observable"), f"observables[{index}].observable")

    constraints = config.get("constraints_used")
    if not isinstance(constraints, list):
        issues.append("constraints_used must be an array")
    else:
        for index, constraint_id in enumerate(constraints):
            if not isinstance(constraint_id, str) or not constraint_id:
                issues.append(
                    f"constraints_used[{index}] must be a non-empty string identifier"
                )
                continue
            for suffix in ("verdict", "margin", "chi2", "skip_reason"):
                add_column(
                    f"{constraint_id}_{suffix}",
                    f"constraints_used[{index}] {suffix} column",
                )

    return columns, issues


def validate_scan_config_namespace(config: Any) -> list[str]:
    """Return issues in the global namespace emitted by a scan config."""

    _, issues = _namespace_columns(config)
    return issues


def expected_scan_columns(config: Any) -> list[str]:
    """Return the exact scan CSV columns, rejecting every global collision."""

    columns, issues = _namespace_columns(config)
    if issues:
        raise ValueError("; ".join(issues))
    return columns


def _load_json_object(path: Path, label: str, issues: list[str]) -> dict[str, Any] | None:
    if not path.exists():
        issues.append(f"missing {label}: {path}")
        return None
    if not path.is_file():
        issues.append(f"{label} is not a regular file: {path}")
        return None
    try:
        payload = load_json(path)
    except (OSError, StrictJSONError) as exc:
        issues.append(f"invalid {label}: {exc}")
        return None
    if not isinstance(payload, dict):
        issues.append(f"{label} must contain a JSON object")
        return None
    return payload


def _require_contained(path: Path, project_dir: Path, label: str, issues: list[str]) -> None:
    root = project_dir.resolve(strict=True)
    if any(part in {".", ".."} for part in path.parts):
        issues.append(f"{label} contains an unsafe dot path segment: {path}")
        return
    candidate = path if path.is_absolute() else root / path
    candidate = candidate.absolute()
    try:
        relative = candidate.relative_to(root)
    except ValueError:
        relative = None
        # The trusted project root may be spelled through an OS alias such as
        # macOS /tmp -> /private/tmp. Match only an ancestor that is the root's
        # inode; components below it are still checked by resolve_contained.
        for prefix in (candidate, *candidate.parents):
            try:
                same_root = prefix.exists() and prefix.samefile(root)
            except OSError:
                same_root = False
            if same_root:
                relative = candidate.relative_to(prefix)
                break
        if relative is None:
            issues.append(f"{label} escapes the project directory: {path}")
            return
    try:
        resolve_contained(root, relative.as_posix(), label)
    except (OSError, ValueError) as exc:
        issues.append(str(exc))


def _finite_number(value: Any, label: str, issues: list[str]) -> float | None:
    if isinstance(value, bool):
        issues.append(f"{label} must be a finite real number, not boolean")
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        issues.append(f"{label} must be a finite real number, got {value!r}")
        return None
    if not math.isfinite(number):
        issues.append(f"{label} must be finite, got {value!r}")
        return None
    return number


def _finite_csv_number(cell: str, label: str, issues: list[str]) -> float | None:
    if cell == "":
        issues.append(f"{label} must not be blank")
        return None
    return _finite_number(cell, label, issues)


def _metadata_count(meta: dict[str, Any], key: str, issues: list[str]) -> int | None:
    value = meta.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        issues.append(f"scan.meta.json {key} must be a non-negative integer")
        return None
    return value


def _build_expected_axes(
    config: dict[str, Any], issues: list[str]
) -> tuple[list[str], list[tuple[float, ...]], int] | None:
    specs = config.get("scan_parameters")
    if not isinstance(specs, list) or not specs:
        issues.append("completed scan config must contain at least one scan parameter")
        return None

    names: list[str] = []
    axes: list[tuple[float, ...]] = []
    total_points = 1

    for index, spec in enumerate(specs):
        label = f"scan_parameters[{index}]"
        if not isinstance(spec, dict):
            issues.append(f"{label} must be an object")
            return None
        name = spec.get("canonical_name")
        if not isinstance(name, str) or not name:
            issues.append(f"{label}.canonical_name must be a non-empty string")
            return None
        range_value = spec.get("range")
        if not isinstance(range_value, list) or len(range_value) != 2:
            issues.append(f"{label}.range must contain exactly two values")
            return None
        start = _finite_number(range_value[0], f"{label}.range[0]", issues)
        stop = _finite_number(range_value[1], f"{label}.range[1]", issues)
        grid = spec.get("grid")
        if isinstance(grid, bool) or not isinstance(grid, int) or grid < 2:
            issues.append(f"{label}.grid must be an integer >= 2")
            return None
        scale = spec.get("scale")
        if scale not in {"linear", "log"}:
            issues.append(f"{label}.scale must be 'linear' or 'log'")
            return None
        if start is None or stop is None:
            return None
        if not start < stop:
            issues.append(
                f"{label}.range must be strictly increasing; got [{start!r}, {stop!r}]"
            )
            return None
        if scale == "log" and start <= 0:
            issues.append(f"{label}.range must be strictly positive for log scale")
            return None

        try:
            with np.errstate(all="ignore"):
                if scale == "log":
                    generated = np.logspace(np.log10(start), np.log10(stop), num=grid)
                else:
                    generated = np.linspace(start, stop, num=grid)
            axis = tuple(float(value) for value in generated)
        except (ArithmeticError, OverflowError, TypeError, ValueError) as exc:
            issues.append(f"{label} grid generation failed: {exc}")
            return None
        if len(axis) != grid or any(not math.isfinite(value) for value in axis):
            issues.append(f"{label} generated grid must contain exactly {grid} finite values")
            return None
        if len(set(axis)) != grid:
            issues.append(
                f"{label} generated grid contains duplicate coordinates at binary64 precision"
            )
            return None

        names.append(name)
        axes.append(axis)
        total_points *= grid

    return names, axes, total_points


def _fixed_values(config: dict[str, Any], issues: list[str]) -> dict[str, float]:
    result: dict[str, float] = {}
    specs = config.get("fixed_parameters")
    if not isinstance(specs, list):
        issues.append("fixed_parameters must be an array")
        return result
    for index, spec in enumerate(specs):
        label = f"fixed_parameters[{index}]"
        if not isinstance(spec, dict):
            issues.append(f"{label} must be an object")
            continue
        name = spec.get("canonical_name")
        if not isinstance(name, str) or not name:
            issues.append(f"{label}.canonical_name must be a non-empty string")
            continue
        value = _finite_number(spec.get("value"), f"{label}.value", issues)
        if value is not None:
            result[name] = value
    return result


def _json_exact_equal(left: Any, right: Any) -> bool:
    """Compare decoded JSON values without bool/int or int/float coercion."""

    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(
            _json_exact_equal(left[key], right[key]) for key in left
        )
    if isinstance(left, list):
        return len(left) == len(right) and all(
            _json_exact_equal(left_item, right_item)
            for left_item, right_item in zip(left, right, strict=True)
        )
    return bool(left == right)


def scan_execution_snapshot(config: Any) -> dict[str, Any]:
    """Return the normalized config fields that can change scan.csv semantics."""

    if not isinstance(config, dict):
        raise ValueError("scan config must be an object")
    missing = [
        key
        for key in _SCAN_EXECUTION_KEYS
        if key not in config and key != "allow_formula_fallback"
    ]
    if missing:
        raise ValueError(f"scan config lacks execution fields: {missing}")
    snapshot: dict[str, Any] = {}
    for key in _SCAN_EXECUTION_KEYS:
        if key == "allow_formula_fallback":
            snapshot[key] = config.get(key, False)
        elif key == "figures":
            figures = config[key]
            if not isinstance(figures, list) or not all(
                isinstance(item, dict) for item in figures
            ):
                raise ValueError("scan config figures must contain objects")
            snapshot[key] = [
                {field: value for field, value in item.items() if field != "title"}
                for item in figures
            ]
        else:
            snapshot[key] = config[key]
    return snapshot


def figure_render_snapshot(config: Any) -> dict[str, Any]:
    """Return the live presentation request attested by figure provenance."""

    if not isinstance(config, dict):
        raise ValueError("scan config must be an object")
    figures = config.get("figures")
    if not isinstance(figures, list):
        raise ValueError("scan config figures must be an array")
    return {
        "analysis_id": config.get("analysis_id"),
        "model_name": config.get("model_name"),
        "figures": figures,
    }


def canonical_json_sha256(payload: Any) -> str:
    """Hash one decoded JSON value using the repository canonical serializer."""

    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _current_rng_consumers(
    project_dir: Path,
    config: dict[str, Any],
) -> tuple[list[str], list[str]]:
    """Derive active custom RNG consumers without importing executable code."""

    custom_bindings = [
        binding
        for binding in config.get("observables", [])
        if isinstance(binding, dict)
        and isinstance(binding.get("source"), dict)
        and binding["source"].get("type") == "custom"
    ]
    if not custom_bindings:
        return [], []
    path = project_dir / "numerics" / "custom_observables.py"
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except (OSError, UnicodeError, SyntaxError) as exc:
        return [], [f"cannot derive RNG consumers from {path}: {exc}"]
    signatures = {
        node.name: {
            argument.arg
            for argument in (
                *node.args.posonlyargs,
                *node.args.args,
                *node.args.kwonlyargs,
            )
        }
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    consumers: list[str] = []
    issues: list[str] = []
    for binding in custom_bindings:
        observable = binding.get("observable")
        function_name = binding["source"].get("function")
        if not isinstance(observable, str) or not isinstance(function_name, str):
            continue
        parameters = signatures.get(function_name)
        if parameters is None:
            issues.append(
                f"cannot derive RNG consumer: custom function {function_name!r} is missing"
            )
        elif "rng" in parameters:
            consumers.append(observable)
    return sorted(set(consumers)), issues


def _summary_count_pattern(label: str) -> re.Pattern[str]:
    return re.compile(
        rf"^\s*-\s*{re.escape(label)}:\s*([0-9]+)(?=\s*(?:\(|$))",
        re.MULTILINE,
    )


def _validate_summary(
    summary_path: Path,
    analysis_id: str,
    row_count: int,
    n_allowed: int,
    n_excluded: int,
    issues: list[str],
) -> None:
    if not summary_path.exists():
        issues.append(f"missing analysis summary: {summary_path}")
        return
    if not summary_path.is_file():
        issues.append(f"analysis summary is not a regular file: {summary_path}")
        return
    try:
        text = summary_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        issues.append(f"analysis summary is not valid UTF-8 text: {exc}")
        return
    if not text.strip():
        issues.append("analysis summary must be non-empty")
        return
    identity_pattern = re.compile(
        rf"(?<![A-Za-z0-9_-]){re.escape(analysis_id)}(?![A-Za-z0-9_-])"
    )
    if identity_pattern.search(text) is None:
        issues.append(f"analysis summary does not identify {analysis_id}")
    for label, count in (
        ("Total points", row_count),
        ("Allowed", n_allowed),
        ("Excluded", n_excluded),
        ("Skipped", 0),
    ):
        marker_counts = _summary_count_pattern(label).findall(text)
        if marker_counts != [str(count)]:
            issues.append(f"analysis summary lacks exact marker '- {label}: {count}'")


def validate_figure_artifact_set(
    project_dir: str | Path,
    analysis_id: str,
    live_scan_config: dict[str, Any],
    scan_meta: dict[str, Any],
    figure_meta: dict[str, Any],
    *,
    figure_dir: str | Path | None = None,
    require_live_render_match: bool = True,
) -> list[str]:
    """Return semantic issues for one rendered figure generation."""

    issues: list[str] = []
    project = Path(project_dir).resolve()
    directory = (
        Path(figure_dir)
        if figure_dir is not None
        else project / "numerics" / "figures" / analysis_id
    )
    if not directory.is_absolute():
        directory = project / directory
    _require_contained(directory, project, "figure directory", issues)
    if not directory.is_dir():
        return [*issues, f"figure directory is missing: {directory}"]
    if figure_meta.get("analysis_id") != analysis_id:
        issues.append("figures.meta.json analysis_id does not match its directory")

    frozen_config = scan_meta.get("scan_config_snapshot")
    render_config = live_scan_config
    if require_live_render_match:
        try:
            expected_render_snapshot = figure_render_snapshot(live_scan_config)
        except ValueError as exc:
            issues.append(str(exc))
            expected_render_snapshot = None
        if expected_render_snapshot is not None and not _json_exact_equal(
            figure_meta.get("render_config_snapshot"), expected_render_snapshot
        ):
            issues.append(
                "figures.meta.json render_config_snapshot does not exactly match the live figure request"
            )
    else:
        recorded_render_snapshot = figure_meta.get("render_config_snapshot")
        if not isinstance(recorded_render_snapshot, dict):
            issues.append("figures.meta.json render_config_snapshot must be an object")
        else:
            render_config = recorded_render_snapshot
            if not isinstance(frozen_config, dict):
                issues.append(
                    "scan.meta.json scan_config_snapshot must be an object for figure validation"
                )
            else:
                attested_config = dict(frozen_config)
                for key in ("analysis_id", "model_name", "figures"):
                    attested_config[key] = recorded_render_snapshot.get(key)
                try:
                    frozen_execution = scan_execution_snapshot(frozen_config)
                    attested_execution = scan_execution_snapshot(attested_config)
                except ValueError as exc:
                    issues.append(str(exc))
                else:
                    if not _json_exact_equal(attested_execution, frozen_execution):
                        issues.append(
                            "figures.meta.json frozen render request changes immutable "
                            "scan execution semantics"
                        )

    try:
        execution_hash = canonical_json_sha256(
            scan_execution_snapshot(frozen_config)
        )
    except ValueError as exc:
        issues.append(str(exc))
    else:
        if figure_meta.get("scan_execution_sha256") != execution_hash:
            issues.append(
                "figures.meta.json scan_execution_sha256 does not match the immutable scan snapshot"
            )

    scan_csv_path = project / "numerics" / "scan-results" / analysis_id / "scan.csv"
    scan_meta_path = project / "numerics" / "scan-results" / analysis_id / "scan.meta.json"
    for path, field, label in (
        (scan_csv_path, "scan_csv_sha256", "scan.csv"),
        (scan_meta_path, "scan_meta_sha256", "scan.meta.json"),
    ):
        try:
            actual = f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"
        except OSError as exc:
            issues.append(f"cannot hash {label} for figure provenance: {exc}")
            continue
        if figure_meta.get(field) != actual:
            issues.append(f"figures.meta.json {field} does not match current {label}")
    if figure_meta.get("scan_csv_sha256") != scan_meta.get("scan_csv_sha256"):
        issues.append(
            "figures.meta.json scan_csv_sha256 does not match scan.meta.json"
        )

    try:
        output_keys = validate_figure_output_keys(render_config)
    except ValueError as exc:
        issues.append(str(exc))
        output_keys = []
    expected_relpaths = sorted(
        f"numerics/figures/{analysis_id}/{key}.{suffix}"
        for key in output_keys
        for suffix in ("pdf", "png")
    )
    outputs = figure_meta.get("outputs")
    if not isinstance(outputs, list):
        issues.append("figures.meta.json outputs must be an array")
        outputs = []
    recorded_relpaths = [
        item.get("path") for item in outputs if isinstance(item, dict)
    ]
    if recorded_relpaths != sorted(recorded_relpaths):
        issues.append("figures.meta.json outputs must be sorted by path")
    if recorded_relpaths != expected_relpaths:
        issues.append(
            "figures.meta.json outputs do not exactly cover its attested render request"
        )
    for item in outputs:
        if not isinstance(item, dict):
            continue
        relpath = item.get("path")
        if not isinstance(relpath, str):
            continue
        if figure_dir is not None and relpath in expected_relpaths:
            path = directory / Path(relpath).name
            _require_contained(path, directory, "staged figure output", issues)
        else:
            try:
                path = resolve_contained(project, relpath, "figure output")
            except ValueError as exc:
                issues.append(str(exc))
                continue
        try:
            actual = f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"
        except OSError as exc:
            issues.append(f"cannot hash figure output {relpath!r}: {exc}")
            continue
        if path.stat().st_size == 0:
            issues.append(f"figure output {relpath!r} is empty")
        if item.get("sha256") != actual:
            issues.append(f"figure output checksum mismatch for {relpath!r}")

    allowed_names = {Path(path).name for path in expected_relpaths} | {
        "figures.meta.json"
    }
    try:
        actual_names = {path.name for path in directory.iterdir()}
    except OSError as exc:
        issues.append(f"cannot enumerate figure directory: {exc}")
    else:
        if actual_names != allowed_names:
            issues.append(
                "figure directory contents do not exactly match the attested generation: "
                f"expected {sorted(allowed_names)}, got {sorted(actual_names)}"
            )
    return issues


def validate_scan_artifact_pair(
    project_dir: str | Path,
    analysis_id: str,
    scan_config_path: str | Path | None = None,
    repo_root: str | Path | None = None,
    *,
    scan_csv_path: str | Path | None = None,
    scan_meta_path: str | Path | None = None,
    analysis_summary_path: str | Path | None = None,
    historical_scan_config_snapshot: dict[str, Any] | None = None,
) -> list[str]:
    """Return semantic issues for one completed run-scan artifact pair.

    ``repo_root`` is accepted so callers can use one stable API when schema
    validation is layered around this semantic validator.  This foundation
    intentionally performs no schema-registry or dependency-graph work.
    """

    del repo_root  # Reserved for the integrating validator layer.
    issues: list[str] = []
    project = Path(project_dir).resolve()
    if not project.exists() or not project.is_dir():
        return [f"project directory does not exist: {project}"]
    if not isinstance(analysis_id, str) or ANALYSIS_ID_PATTERN.fullmatch(analysis_id) is None:
        issues.append(f"analysis_id must match ^analysis-[0-9]{{3}}$: {analysis_id!r}")

    canonical_config_path = project / "numerics" / "scan-configs" / f"{analysis_id}.json"
    config_path = Path(scan_config_path) if scan_config_path is not None else canonical_config_path
    if historical_scan_config_snapshot is None:
        if not config_path.is_absolute():
            config_path = project / config_path
        if config_path.resolve() != canonical_config_path.resolve():
            issues.append(
                "scan config path does not match the canonical analysis path: "
                f"expected {canonical_config_path}, got {config_path}"
            )
        if config_path.stem != analysis_id:
            issues.append(
                f"scan config filename stem {config_path.stem!r} does not match {analysis_id!r}"
            )
        _require_contained(config_path, project, "scan config", issues)
    elif scan_config_path is not None:
        issues.append(
            "historical scan validation accepts an embedded config snapshot, not a live config path"
        )

    canonical_result_dir = project / "numerics" / "scan-results" / analysis_id
    meta_path = (
        Path(scan_meta_path)
        if scan_meta_path is not None
        else canonical_result_dir / "scan.meta.json"
    )
    csv_path = (
        Path(scan_csv_path)
        if scan_csv_path is not None
        else canonical_result_dir / "scan.csv"
    )
    summary_path = (
        Path(analysis_summary_path)
        if analysis_summary_path is not None
        else project / "numerics" / f"analysis-summary-{analysis_id}.md"
    )
    for path_name, path_value in (
        ("scan metadata", meta_path),
        ("scan CSV", csv_path),
        ("analysis summary", summary_path),
    ):
        if not path_value.is_absolute():
            resolved = project / path_value
            if path_name == "scan metadata":
                meta_path = resolved
            elif path_name == "scan CSV":
                csv_path = resolved
            else:
                summary_path = resolved
    for path, label in (
        (meta_path, "scan metadata"),
        (csv_path, "scan CSV"),
        (summary_path, "analysis summary"),
    ):
        _require_contained(path, project, label, issues)
    for path, expected_name, label in (
        (meta_path, "scan.meta.json", "scan metadata"),
        (csv_path, "scan.csv", "scan CSV"),
        (
            summary_path,
            f"analysis-summary-{analysis_id}.md",
            "analysis summary",
        ),
    ):
        if path.name != expected_name:
            issues.append(
                f"{label} filename must be {expected_name!r}, got {path.name!r}"
            )
    if scan_csv_path is None and canonical_result_dir.name != analysis_id:
        issues.append(
            f"scan result directory {canonical_result_dir.name!r} does not match {analysis_id!r}"
        )

    live_config = (
        historical_scan_config_snapshot
        if historical_scan_config_snapshot is not None
        else _load_json_object(config_path, "scan config", issues)
    )
    if not isinstance(live_config, dict):
        issues.append("historical scan config snapshot must be an object")
        live_config = None
    meta = _load_json_object(meta_path, "scan metadata", issues)
    if live_config is None or meta is None:
        return issues

    if live_config.get("analysis_id") != analysis_id:
        issues.append(
            f"scan config analysis_id {live_config.get('analysis_id')!r} does not match {analysis_id!r}"
        )
    if meta.get("analysis_id") != analysis_id:
        issues.append(
            f"scan metadata analysis_id {meta.get('analysis_id')!r} does not match {analysis_id!r}"
        )
    missing_meta_fields = sorted(RUN_SCAN_META_REQUIRED_FIELDS - set(meta))
    if missing_meta_fields:
        issues.append(
            "scan.meta.json is not complete run-scan metadata; missing fields: "
            f"{missing_meta_fields}"
        )
    snapshot = meta.get("scan_config_snapshot")
    if not isinstance(snapshot, dict):
        issues.append("scan.meta.json is not run-scan metadata: scan_config_snapshot is missing")
        config = live_config
    else:
        if snapshot.get("analysis_id") != analysis_id:
            issues.append(
                "scan_config_snapshot.analysis_id does not match the result directory: "
                f"{snapshot.get('analysis_id')!r} != {analysis_id!r}"
            )
        try:
            live_execution = scan_execution_snapshot(live_config)
            frozen_execution = scan_execution_snapshot(snapshot)
        except ValueError as exc:
            issues.append(str(exc))
        else:
            if not _json_exact_equal(frozen_execution, live_execution):
                issues.append(
                    "scan_config_snapshot execution semantics do not match the live scan config"
                )
        config = snapshot

    source = meta.get("scan_config_source")
    if not isinstance(source, str) or not source:
        issues.append("scan.meta.json scan_config_source must be non-empty exact UTF-8 text")
    else:
        declared_source_hash = meta.get("scan_config_sha256")
        actual_source_hash = f"sha256:{hashlib.sha256(source.encode('utf-8')).hexdigest()}"
        if declared_source_hash != actual_source_hash:
            issues.append(
                "scan_config_sha256 does not match the exact embedded scan_config_source"
            )
        try:
            source_payload = loads_json(source, source="scan.meta.json.scan_config_source")
        except StrictJSONError as exc:
            issues.append(str(exc))
        else:
            if not isinstance(snapshot, dict) or not _json_exact_equal(source_payload, snapshot):
                issues.append(
                    "scan_config_source does not decode exactly to scan_config_snapshot"
                )

    depends_on = config.get("depends_on")
    if not isinstance(depends_on, dict):
        issues.append("scan config depends_on must be an object")
    else:
        for config_key, meta_key in (
            ("model_version", "model_version"),
            ("model_checksum", "model_checksum"),
        ):
            if meta.get(meta_key) != depends_on.get(config_key):
                issues.append(
                    f"scan.meta.json {meta_key} does not match "
                    f"scan config depends_on.{config_key}"
                )
    if not _json_exact_equal(meta.get("seed"), config.get("seed", 0)):
        issues.append("scan.meta.json seed does not match the scan config seed")
    rng = meta.get("rng")
    if not isinstance(rng, dict):
        issues.append("scan.meta.json rng must be an object")
    else:
        expected_rng_keys = {
            "algorithm",
            "algorithm_version",
            "substream_scheme",
            "seed",
            "substreams",
            "consumers",
        }
        if set(rng) != expected_rng_keys:
            issues.append(
                "scan.meta.json rng fields must exactly equal "
                f"{sorted(expected_rng_keys)}"
            )
        for field, expected in (
            ("algorithm", "numpy.random.PCG64"),
            ("algorithm_version", "pcg64-v1"),
            ("substream_scheme", "numpy-seedsequence-v1"),
            ("substreams", {"smoke": 0, "scan": 1}),
        ):
            if not _json_exact_equal(rng.get(field), expected):
                issues.append(
                    f"scan.meta.json rng.{field} does not match the supported contract"
                )
        if not _json_exact_equal(rng.get("seed"), config.get("seed")):
            issues.append("scan.meta.json rng.seed does not match the scan config seed")
        consumers = rng.get("consumers")
        if (
            not isinstance(consumers, list)
            or any(
                not isinstance(item, str)
                or CANONICAL_IDENTIFIER_PATTERN.fullmatch(item) is None
                or keyword.iskeyword(item)
                for item in consumers
            )
            or consumers != sorted(set(consumers))
        ):
            issues.append(
                "scan.meta.json rng.consumers must be sorted unique canonical identifiers"
            )
    if historical_scan_config_snapshot is None and isinstance(rng, dict):
        expected_consumers, consumer_issues = _current_rng_consumers(project, config)
        issues.extend(consumer_issues)
        if not _json_exact_equal(rng.get("consumers"), expected_consumers):
            issues.append(
                "scan.meta.json rng.consumers does not match active custom callable signatures"
            )

    issues.extend(validate_scan_config_namespace(config))
    try:
        expected_columns = expected_scan_columns(config)
    except ValueError:
        expected_columns = []

    axes_result = _build_expected_axes(config, issues)
    fixed_values = _fixed_values(config, issues)
    observable_names = [
        item.get("observable")
        for item in config.get("observables", [])
        if isinstance(item, dict) and isinstance(item.get("observable"), str)
    ]
    constraint_ids = [
        item for item in config.get("constraints_used", []) if isinstance(item, str)
    ]
    if not constraint_ids:
        issues.append("completed scan must contain at least one configured constraint")

    if not csv_path.exists():
        issues.append(f"missing scan CSV: {csv_path}")
        return issues
    if not csv_path.is_file():
        issues.append(f"scan CSV is not a regular file: {csv_path}")
        return issues

    try:
        csv_bytes = csv_path.read_bytes()
    except OSError as exc:
        issues.append(f"scan.csv could not be read for checksum validation: {exc}")
        return issues
    expected_checksum = f"sha256:{hashlib.sha256(csv_bytes).hexdigest()}"
    declared_checksum = meta.get("scan_csv_sha256")
    if declared_checksum is None:
        issues.append("scan.meta.json is missing required scan_csv_sha256")
    elif declared_checksum != expected_checksum:
        issues.append(
            f"scan_csv_sha256 does not match scan.csv: {declared_checksum!r} != {expected_checksum!r}"
        )

    row_count = 0
    n_allowed_recomputed = 0
    n_excluded_recomputed = 0
    coordinate_set: set[tuple[float, ...]] = set()
    coordinate_rows_valid = True
    header_valid = False

    try:
        with csv_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.reader(handle, strict=True)
            try:
                header = next(reader)
            except StopIteration:
                issues.append("scan.csv is empty and has no header")
                header = []

            if len(header) != len(set(header)):
                duplicates = sorted({name for name in header if header.count(name) > 1})
                issues.append(f"scan.csv header contains duplicate columns: {duplicates}")
            if header != expected_columns:
                issues.append(
                    f"scan.csv header/order does not match the scan config: "
                    f"expected {expected_columns}, got {header}"
                )
            else:
                header_valid = True

            column_index = {name: index for index, name in enumerate(header)} if header_valid else {}
            axis_names: list[str] = []
            axes: list[tuple[float, ...]] = []
            expected_point_count: int | None = None
            if axes_result is not None:
                axis_names, axes, expected_point_count = axes_result
            axis_sets = [set(axis) for axis in axes]

            for row in reader:
                row_count += 1
                row_label = f"scan.csv row {row_count + 1}"
                if len(row) != len(header):
                    issues.append(
                        f"{row_label} has {len(row)} cells; expected exactly {len(header)}"
                    )
                    coordinate_rows_valid = False
                    continue
                if any("\n" in cell or "\r" in cell for cell in row):
                    issues.append(f"{row_label} contains a forbidden multi-line cell")
                if not header_valid:
                    coordinate_rows_valid = False
                    continue

                scan_values: list[float] = []
                scan_values_valid = axes_result is not None
                for axis_index, name in enumerate(axis_names):
                    value = _finite_csv_number(
                        row[column_index[name]], f"{row_label} scan parameter {name}", issues
                    )
                    if value is None:
                        scan_values_valid = False
                        continue
                    scan_values.append(value)
                    if value not in axis_sets[axis_index]:
                        issues.append(
                            f"{row_label} scan parameter {name}={value!r} is not on the exact configured grid"
                        )
                        scan_values_valid = False

                if scan_values_valid and len(scan_values) == len(axis_names):
                    coordinate = tuple(scan_values)
                    if coordinate in coordinate_set:
                        issues.append(f"{row_label} duplicates scan coordinate {coordinate!r}")
                        coordinate_rows_valid = False
                    coordinate_set.add(coordinate)
                else:
                    coordinate_rows_valid = False

                for name, expected_value in fixed_values.items():
                    value = _finite_csv_number(
                        row[column_index[name]], f"{row_label} fixed parameter {name}", issues
                    )
                    if value is not None and value != expected_value:
                        issues.append(
                            f"{row_label} fixed parameter {name}={value!r} does not exactly match "
                            f"configured value {expected_value!r}"
                        )

                for name in observable_names:
                    _finite_csv_number(
                        row[column_index[name]], f"{row_label} observable {name}", issues
                    )

                verdicts: list[str] = []
                verdicts_valid = bool(constraint_ids)
                for constraint_id in constraint_ids:
                    verdict = row[column_index[f"{constraint_id}_verdict"]]
                    if verdict not in {"allowed", "excluded"}:
                        issues.append(
                            f"{row_label} {constraint_id}_verdict must be 'allowed' or "
                            f"'excluded', got {verdict!r}"
                        )
                        verdicts_valid = False
                    else:
                        verdicts.append(verdict)
                    for suffix in ("margin", "chi2"):
                        cell = row[column_index[f"{constraint_id}_{suffix}"]]
                        if cell != "":
                            _finite_number(
                                cell,
                                f"{row_label} {constraint_id}_{suffix}",
                                issues,
                            )
                    skip_reason = row[column_index[f"{constraint_id}_skip_reason"]]
                    if skip_reason != "":
                        issues.append(
                            f"{row_label} {constraint_id}_skip_reason must be exactly empty"
                        )

                if verdicts_valid and len(verdicts) == len(constraint_ids):
                    if any(verdict == "excluded" for verdict in verdicts):
                        n_excluded_recomputed += 1
                    else:
                        n_allowed_recomputed += 1

            if expected_point_count is not None:
                if row_count != expected_point_count:
                    issues.append(
                        f"scan.csv row count {row_count} does not equal configured Cartesian "
                        f"grid size {expected_point_count}"
                    )
                if not coordinate_rows_valid or len(coordinate_set) != expected_point_count:
                    issues.append(
                        "scan.csv coordinates do not form one unique complete Cartesian grid"
                    )
    except UnicodeDecodeError as exc:
        issues.append(f"scan.csv is not valid UTF-8: {exc}")
    except (OSError, csv.Error) as exc:
        issues.append(f"scan.csv could not be parsed strictly: {exc}")

    n_points = _metadata_count(meta, "n_points", issues)
    n_allowed = _metadata_count(meta, "n_allowed", issues)
    n_excluded = _metadata_count(meta, "n_excluded", issues)
    n_skipped = _metadata_count(meta, "n_skipped", issues)
    configured_points = axes_result[2] if axes_result is not None else None
    if n_points is not None:
        if n_points != row_count:
            issues.append(
                f"scan.meta.json n_points={n_points} does not match scan.csv row count {row_count}"
            )
        if configured_points is not None and n_points != configured_points:
            issues.append(
                f"scan.meta.json n_points={n_points} does not match configured grid size "
                f"{configured_points}"
            )
    if n_skipped is not None and n_skipped != 0:
        issues.append(f"completed scan requires n_skipped=0, got {n_skipped}")
    if n_allowed is not None and n_allowed != n_allowed_recomputed:
        issues.append(
            f"scan.meta.json n_allowed={n_allowed} does not match CSV-derived count "
            f"{n_allowed_recomputed}"
        )
    if n_excluded is not None and n_excluded != n_excluded_recomputed:
        issues.append(
            f"scan.meta.json n_excluded={n_excluded} does not match CSV-derived count "
            f"{n_excluded_recomputed}"
        )
    if None not in (n_points, n_allowed, n_excluded, n_skipped):
        assert n_points is not None
        assert n_allowed is not None
        assert n_excluded is not None
        assert n_skipped is not None
        if n_allowed + n_excluded + n_skipped != n_points:
            issues.append(
                "scan.meta.json classification counts do not sum exactly to n_points"
            )

    _validate_summary(
        summary_path,
        analysis_id,
        row_count,
        n_allowed_recomputed,
        n_excluded_recomputed,
        issues,
    )
    return issues
