#!/usr/bin/env python3
"""Compare project outputs against paper reproduction targets.

Usage:
  python3 scripts/compare_to_reference.py --project-dir workspace/projects/<name> \
      --analysis-id analysis-001 --repro-id run-001 [--target-id fig-3a] \
      [--blocked-targets fig-5,tab-2]

The script writes:
- `reproduction/runs/<repro-id>/reproduction-result.json`, validated against
  `schemas/reproduction-result.schema.json`.
- `reproduction/runs/<repro-id>/diagnostic.md` when a target does not receive a
  mechanical `pass` verdict.
- comparison figures under `reproduction/figures/<repro-id>/`.

HRP commitments:
- Refuse to overwrite an existing `reproduction/runs/<repro-id>/`.
- Publish the immutable run, its figures, and the corresponding manifest entry
  in one project-scoped transaction. Mutable workflow status is never used as
  scientific evidence.
- Do not modify tolerance values, drop points to improve agreement, or compute
  physics interpretations. Verdicts are mechanical labels from fixed inputs.

Determinism:
- Target iteration is sorted by `target_id`.
- Metric dictionaries, verdicts, verdict ceilings, derivation independence, and
  provenance issues are deterministic for identical inputs.
- `started_at`, `finished_at`, and figure metadata may vary between runs.
"""

from __future__ import annotations

import argparse
import copy
from collections import Counter
import csv
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NamedTuple

import numpy as np
import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from _compare_figures import apply_style, relative_generated_files, render_all_figures, render_blocked_overlay
from _compare_metrics import (
    BoundaryComparison,
    SeriesComparison,
    benchmark_point_metrics,
    exclusion_region_metrics,
    exact_decimal_linear_conversion_matches,
    expected_linear_unit_conversion,
    filter_fixed_rows,
    figure_curve_metrics,
    keyed_benchmark_metrics,
    load_csv,
    parametric_curve_metrics,
    scan_table_metrics,
    validate_fixed_parameter_normalization,
)
from _calculation_provenance import derivation_artifact_errors
from _reproduction_result_validation import (
    expected_evidence_axes,
    reproduction_result_semantic_errors,
)
from _reproduction_readiness import (
    _same_json_scalar,
    derive_reproduction_readiness,
    require_consumable_manifest_analysis,
    resolve_tasks_for_target,
    result_meta_paths,
    task_catalog,
)
from _strict_json import StrictJSONError, load_json
from _dependency_graph import (
    build_dependency_graph,
    verify_dependency_graph,
)
from _identity import resolve_contained, validate_analysis_id, validate_repro_id
from _publication_transaction import (
    PublicationTransaction,
    TransactionCommittedCleanupError,
    assert_no_active_transactions,
    capture_identity,
    publication_lock,
)
from _workflow_dependencies import (
    calculation_dependency_specs,
    reproduction_dependency_specs,
)


REPO_ROOT = SCRIPT_DIR.parent
SCHEMAS_DIR = REPO_ROOT / "schemas"

INDEPENDENCE_ORDER = {
    "independent": 0,
    "independent_manual": 1,
    "unknown": 2,
    "tainted": 3,
}


class Inputs(NamedTuple):
    project_dir: Path
    analysis_id: str
    repro_id: str
    run_dir: Path
    repro_targets: dict[str, Any]
    calc_tasks: dict[str, Any]
    scan_csv: Path
    repro_targets_path: Path
    blocked_targets: set[str]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare project scan outputs against paper reproduction targets."
    )
    parser.add_argument("--project-dir", required=True, help="Workspace project directory.")
    parser.add_argument("--analysis-id", required=True, help="Analysis id, e.g. analysis-001.")
    parser.add_argument("--repro-id", required=True, help="Reproduction run id, e.g. run-001.")
    parser.add_argument("--target-id", help="Optional single reproduction target id.")
    parser.add_argument(
        "--blocked-targets",
        default="",
        help=(
            "Deprecated compatibility assertion: comma-separated target ids must "
            "exactly match blockers derived from typed readiness."
        ),
    )
    return parser.parse_args(argv)


def parse_blocked_targets(raw: str) -> set[str]:
    values = [item.strip() for item in raw.split(",") if item.strip()]
    duplicates = sorted(
        value for value, count in Counter(values).items() if count > 1
    )
    if duplicates:
        raise ValueError(
            "--blocked-targets contains duplicate target ids: "
            f"{duplicates}"
        )
    return set(values)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _csv_text_column(path: Path, column: str) -> list[str]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        index = header.index(column)
        values: list[str] = []
        for row_number, row in enumerate(reader, start=2):
            try:
                values.append(row[index])
            except IndexError as exc:
                raise ValueError(
                    f"CSV column {column!r} is missing data at line {row_number}"
                ) from exc
    return values


def _csv_decimal_column(path: Path, column: str) -> list[Decimal]:
    values: list[Decimal] = []
    for row_number, token in enumerate(_csv_text_column(path, column), start=2):
        try:
            value = Decimal(token.strip())
        except InvalidOperation as exc:
            raise ValueError(
                f"CSV column {column!r} has invalid decimal data at line {row_number}"
            ) from exc
        if not value.is_finite():
            raise ValueError(
                f"CSV column {column!r} has non-finite data at line {row_number}"
            )
        values.append(value)
    return values


def _canonical_decimal_is_binary64_stable(value: Decimal) -> bool:
    try:
        decoded = float(value)
    except (OverflowError, ValueError):
        return False
    return math.isfinite(decoded) and Decimal(str(decoded)) == value


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def print_error(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)


def schema_errors(schema_name: str, payload: dict[str, Any]) -> list[str]:
    try:
        from jsonschema import Draft202012Validator
    except ImportError as exc:
        return [f"jsonschema is not installed: {exc}"]

    schema = load_json(SCHEMAS_DIR / schema_name)
    validator = Draft202012Validator(schema)
    messages: list[str] = []
    for err in sorted(validator.iter_errors(payload), key=lambda item: list(item.absolute_path)):
        path = ".".join(str(part) for part in err.absolute_path) or "<root>"
        messages.append(f"{path}: {err.message}")
    return messages


def select_targets(repro_targets: dict[str, Any], target_id: str | None) -> list[dict[str, Any]]:
    targets = sorted(repro_targets.get("targets", []), key=lambda target: str(target.get("id", "")))
    if target_id is None:
        return targets
    selected = [target for target in targets if target.get("id") == target_id]
    if not selected:
        raise ValueError(f"target-id not found in literature/repro-targets.json: {target_id}")
    return selected


def _resolve_under_digitized(project_dir: Path, relpath: object, label: str) -> Path:
    if not isinstance(relpath, str) or not relpath or Path(relpath).is_absolute():
        raise ValueError(f"{label} must be a project-relative path under literature/digitized")
    project_root = project_dir.resolve()
    allowed_root = (project_root / "literature" / "digitized").resolve()
    if not allowed_root.is_relative_to(project_root):
        raise ValueError("literature/digitized resolves outside the project directory")
    resolved = (project_root / relpath).resolve()
    if not resolved.is_relative_to(allowed_root):
        raise ValueError(f"{label} escapes literature/digitized: {relpath}")
    return resolved


def validate_target_normalization(
    project_dir: Path,
    target: dict[str, Any],
    *,
    scan_csv: Path | None = None,
    paper_id: str | None = None,
) -> None:
    if target.get("kind") == "formula":
        formula_path = _resolve_under_digitized(
            project_dir,
            target.get("data_file"),
            f"target {target.get('id')!r} data_file",
        )
        reject_generated_reference_alias(
            project_dir,
            formula_path,
            label=f"target {target.get('id')!r} formula reference",
            scan_csv=scan_csv,
        )
        if (
            not formula_path.exists()
            or not formula_path.is_file()
            or formula_path.stat().st_size <= 0
        ):
            raise ValueError(
                f"target {target.get('id')!r} formula reference is missing or empty: "
                f"{formula_path}"
            )
        if formula_path.suffix.lower() != ".json":
            raise ValueError("formula reference evidence must be a JSON file")
        try:
            formula_evidence = load_json(formula_path)
        except (OSError, StrictJSONError) as exc:
            raise ValueError(f"formula reference evidence is invalid JSON: {exc}") from exc
        if not isinstance(formula_evidence, dict) or not formula_evidence:
            raise ValueError("formula reference evidence must be a non-empty JSON object")
        formula_schema_errors = schema_errors(
            "formula-reference.schema.json",
            formula_evidence,
        )
        if formula_schema_errors:
            raise ValueError(
                "formula reference evidence failed schema validation: "
                + "; ".join(formula_schema_errors)
            )
        if formula_evidence.get("paper_id") != paper_id:
            raise ValueError("formula reference paper_id does not match repro-targets")
        if formula_evidence.get("target_id") != target.get("id"):
            raise ValueError("formula reference target_id does not match target")
        for field in ("expression", "source_locator"):
            value = formula_evidence.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"formula reference {field} must contain non-whitespace text")
        if any(
            token in formula_evidence["source_locator"]
            for token in ("numerics/", "reproduction/", "scan.csv")
        ):
            raise ValueError("formula reference source_locator points at generated project data")
        try:
            datetime.strptime(
                str(formula_evidence.get("acquired_at")),
                "%Y-%m-%dT%H:%M:%SZ",
            )
        except ValueError as exc:
            raise ValueError("formula reference acquired_at is not a valid UTC timestamp") from exc
        return
    target_id = target.get("id")
    normalization = target.get("normalization")
    if not isinstance(normalization, dict):
        raise ValueError(f"target {target_id!r} is missing normalization metadata")
    fixed_unit_changed = validate_fixed_parameter_normalization(target, normalization)
    if normalization.get("method") == "identity" and fixed_unit_changed:
        raise ValueError(
            f"target {target_id!r} identity normalization converts fixed parameter units"
        )
    acquisition = normalization.get("acquisition")
    if not isinstance(acquisition, dict):
        raise ValueError(f"target {target_id!r} lacks acquisition provenance")
    source_type = acquisition.get("source_type")
    method = acquisition.get("method")
    if (source_type == "synthetic_fixture") != (method == "synthetic_fixture"):
        raise ValueError(
            f"target {target_id!r} synthetic acquisition type/method must agree"
        )
    locator = acquisition.get("source_locator")
    if not isinstance(locator, str) or not locator.strip():
        raise ValueError(f"target {target_id!r} acquisition source_locator is missing")
    if any(token in locator for token in ("numerics/", "reproduction/", "scan.csv")):
        raise ValueError(
            f"target {target_id!r} acquisition locator points at generated project data"
        )
    if paper_id and acquisition.get("paper_id") != paper_id:
        raise ValueError(
            f"target {target_id!r} acquisition paper_id must exactly match the target set"
        )
    acquired_at = acquisition.get("acquired_at")
    if not isinstance(acquired_at, str):
        raise ValueError(f"target {target_id!r} acquisition acquired_at is missing")
    try:
        datetime.strptime(acquired_at, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise ValueError(
            f"target {target_id!r} acquisition acquired_at must be a valid UTC timestamp"
        ) from exc

    canonical_path = _resolve_under_digitized(
        project_dir, target.get("data_file"), f"target {target_id!r} data_file"
    )
    source_path = _resolve_under_digitized(
        project_dir,
        normalization.get("source_data_file"),
        f"target {target_id!r} normalization.source_data_file",
    )
    record_path = _resolve_under_digitized(
        project_dir,
        normalization.get("record_file"),
        f"target {target_id!r} normalization.record_file",
    )
    if len({canonical_path, source_path, record_path}) != 3:
        raise ValueError(
            f"target {target_id!r} normalization must retain distinct raw, canonical, "
            "and conversion-record files"
        )
    for label, path in (
        ("canonical data", canonical_path),
        ("raw source data", source_path),
        ("conversion record", record_path),
    ):
        if not path.exists() or not path.is_file() or path.stat().st_size <= 0:
            raise ValueError(f"target {target_id!r} {label} file is missing or empty: {path}")
    named_paths = [
        ("canonical", canonical_path),
        ("raw", source_path),
        ("record", record_path),
    ]
    for left_index, (left_label, left_path) in enumerate(named_paths):
        for right_label, right_path in named_paths[left_index + 1 :]:
            try:
                same_file = left_path.samefile(right_path)
            except OSError:
                same_file = False
            if same_file:
                raise ValueError(
                    f"target {target_id!r} {left_label} and {right_label} evidence "
                    "must be distinct filesystem objects"
                )
    for evidence_label, evidence_path in named_paths:
        reject_generated_reference_alias(
            project_dir,
            evidence_path,
            label=f"target {target_id!r} {evidence_label} evidence",
            scan_csv=scan_csv,
        )

    try:
        record = load_json(record_path)
    except (OSError, StrictJSONError) as exc:
        raise ValueError(
            f"target {target_id!r} normalization record is invalid JSON: {exc}"
        ) from exc
    record_schema_errors = schema_errors("normalization-record.schema.json", record)
    if record_schema_errors:
        raise ValueError(
            f"target {target_id!r} normalization record failed schema validation: "
            + "; ".join(record_schema_errors)
        )
    expected_fields = {
        "status": normalization.get("status"),
        "method": normalization.get("method"),
        "source_data_file": normalization.get("source_data_file"),
        "canonical_data_file": target.get("data_file"),
        "source_units": normalization.get("source_units"),
        "canonical_units": normalization.get("canonical_units"),
        "conversions": normalization.get("conversions"),
        "fixed_parameters": normalization.get("fixed_parameters"),
        "acquisition": normalization.get("acquisition"),
        "source_checksum": sha256_file(source_path),
        "canonical_checksum": sha256_file(canonical_path),
    }
    mismatches = [
        key for key, expected in expected_fields.items() if record.get(key) != expected
    ]
    if mismatches:
        raise ValueError(
            f"target {target_id!r} normalization record does not bind current data: "
            f"{mismatches}"
        )
    expected_record_keys = set(expected_fields)
    if set(record) != expected_record_keys:
        raise ValueError(
            f"target {target_id!r} normalization record keys must be exactly "
            f"{sorted(expected_record_keys)}"
        )

    try:
        source_frame = load_csv(source_path)
        canonical_frame = load_csv(canonical_path)
    except (OSError, ValueError) as exc:
        raise ValueError(
            f"target {target_id!r} normalization data cannot be verified: {exc}"
        ) from exc
    if list(source_frame.columns) != list(canonical_frame.columns) or len(
        source_frame
    ) != len(canonical_frame):
        raise ValueError(
            f"target {target_id!r} raw and canonical data must have identical row/column shape"
        )

    source_units = normalization.get("source_units")
    canonical_units = normalization.get("canonical_units")
    conversions = normalization.get("conversions")
    if not all(
        isinstance(mapping, dict)
        for mapping in (source_units, canonical_units, conversions)
    ):
        raise ValueError(
            f"target {target_id!r} normalization unit/conversion mappings must be objects"
        )
    if not (set(source_units) == set(canonical_units) == set(conversions)):
        raise ValueError(
            f"target {target_id!r} source_units, canonical_units, and conversions "
            "must cover exactly the same columns"
        )
    unknown_normalized_columns = sorted(set(conversions) - set(source_frame.columns))
    if unknown_normalized_columns:
        raise ValueError(
            f"target {target_id!r} normalization declares columns absent from data: "
            f"{unknown_normalized_columns}"
        )
    kind = target.get("kind")
    required_columns = {str(target.get("x_param")), str(target.get("y_param"))}
    if kind == "parametric_curve":
        required_columns.add(str(target.get("curve_parameter")))
    if kind in {"benchmark_point", "keyed_benchmark_set", "scan_table"}:
        required_columns.update(str(item) for item in target.get("match_columns", []))
        required_columns.update(str(item) for item in target.get("observables", []))
    if kind == "exclusion_region" and target.get("boundary", {}).get(
        "mode"
    ) == "observable_threshold":
        required_columns.add(str(target.get("boundary", {}).get("observable")))
    required_columns.discard("None")
    missing_normalized_columns = sorted(required_columns - set(conversions))
    if missing_normalized_columns:
        raise ValueError(
            f"target {target_id!r} normalization misses compared columns: "
            f"{missing_normalized_columns}"
        )

    if normalization.get("method") == "identity":
        if normalization.get("source_units") != normalization.get("canonical_units"):
            raise ValueError(
                f"target {target_id!r} identity normalization requires identical "
                "source_units and canonical_units"
            )
        if not isinstance(conversions, dict) or not conversions:
            raise ValueError(
                f"target {target_id!r} identity normalization requires explicit "
                "factor=1, offset=0 column records"
            )
        for column, conversion in sorted(conversions.items()):
            if column not in source_frame.columns or conversion != {
                "operation": "linear",
                "factor": 1.0,
                "offset": 0.0,
            }:
                raise ValueError(
                    f"target {target_id!r} identity conversion for {column!r} must "
                    "be linear with factor=1 and offset=0"
                )
        source_units = normalization.get("source_units", {})
        table_equal = True
        for column in source_frame.columns:
            if source_units.get(column) == "categorical":
                column_equal = _csv_text_column(
                    source_path, str(column)
                ) == _csv_text_column(canonical_path, str(column))
            elif column in source_units:
                source_decimals = _csv_decimal_column(source_path, str(column))
                canonical_decimals = _csv_decimal_column(
                    canonical_path, str(column)
                )
                column_equal = source_decimals == canonical_decimals and all(
                    _canonical_decimal_is_binary64_stable(value)
                    for value in canonical_decimals
                )
            else:
                column_equal = _csv_text_column(
                    source_path, str(column)
                ) == _csv_text_column(canonical_path, str(column))
            table_equal = table_equal and column_equal
        if not table_equal:
            raise ValueError(
                f"target {target_id!r} identity normalization changed tabular values"
            )
    elif normalization.get("method") == "converted":
        conversions = normalization.get("conversions")
        if not isinstance(conversions, dict) or not conversions:
            raise ValueError(
                f"target {target_id!r} converted normalization requires non-empty "
                "machine-verifiable conversions"
            )
        converted_columns: set[str] = set()
        source_units = normalization.get("source_units", {})
        canonical_units = normalization.get("canonical_units", {})
        changed_unit_columns = {
            column
            for column in set(source_units) | set(canonical_units)
            if source_units.get(column) != canonical_units.get(column)
        }
        missing_unit_conversions = sorted(changed_unit_columns - set(conversions))
        if missing_unit_conversions:
            raise ValueError(
                f"target {target_id!r} lacks conversions for changed-unit columns: "
                f"{missing_unit_conversions}"
            )
        for column, conversion in sorted(conversions.items()):
            if not isinstance(conversion, dict):
                raise ValueError(
                    f"target {target_id!r} conversion for {column!r} must be an object"
                )
            required = {"operation", "factor", "offset"}
            if set(conversion) != required:
                raise ValueError(
                    f"target {target_id!r} conversion for {column!r} must contain exactly "
                    f"{sorted(required)}"
                )
            if conversion.get("operation") != "linear":
                raise ValueError(
                    f"target {target_id!r} conversion for {column!r} must be linear"
                )
            factor = conversion.get("factor")
            offset = conversion.get("offset")
            if (
                not isinstance(factor, (int, float))
                or isinstance(factor, bool)
                or not math.isfinite(float(factor))
                or not isinstance(offset, (int, float))
                or isinstance(offset, bool)
                or not math.isfinite(float(offset))
                or float(factor) == 0.0
            ):
                raise ValueError(
                    f"target {target_id!r} conversion for {column!r} requires a finite "
                    "nonzero factor and finite offset"
                )
            if column in converted_columns or column not in source_frame.columns:
                raise ValueError(
                    f"target {target_id!r} conversion has duplicate/unknown column "
                    f"{column!r}"
                )
            converted_columns.add(str(column))
            if column not in source_units or column not in canonical_units:
                raise ValueError(
                    f"target {target_id!r} conversion for {column!r} lacks source/canonical units"
                )
            expected_conversion = expected_linear_unit_conversion(
                str(source_units[column]),
                str(canonical_units[column]),
            )
            if expected_conversion is None or not (
                float(factor) == expected_conversion[0]
                and float(offset) == expected_conversion[1]
            ):
                raise ValueError(
                    f"target {target_id!r} conversion for {column!r} is not an "
                    "allowlisted dimension-preserving unit conversion"
                )
            if (
                source_units[column] == "categorical"
                and canonical_units[column] == "categorical"
            ):
                if (
                    float(factor) != 1.0
                    or float(offset) != 0.0
                    or _csv_text_column(source_path, str(column))
                    != _csv_text_column(canonical_path, str(column))
                ):
                    raise ValueError(
                        f"target {target_id!r} categorical conversion for {column!r} "
                        "must be exact identity"
                    )
                continue
            if (
                pd.api.types.is_bool_dtype(source_frame[column].dtype)
                or source_frame[column].map(
                    lambda value: isinstance(value, (bool, np.bool_))
                ).any()
                or pd.api.types.is_bool_dtype(canonical_frame[column].dtype)
                or canonical_frame[column].map(
                    lambda value: isinstance(value, (bool, np.bool_))
                ).any()
            ):
                raise ValueError(
                    f"target {target_id!r} conversion for {column!r} contains boolean data"
                )
            source_values = pd.to_numeric(source_frame[column], errors="coerce").to_numpy(
                dtype=float, na_value=np.nan
            )
            canonical_values = pd.to_numeric(
                canonical_frame[column], errors="coerce"
            ).to_numpy(dtype=float, na_value=np.nan)
            source_decimals = _csv_decimal_column(source_path, str(column))
            canonical_decimals = _csv_decimal_column(canonical_path, str(column))
            factor_decimal = Decimal(str(factor))
            offset_decimal = Decimal(str(offset))
            decimal_conversion_matches = True
            for source_decimal, canonical_decimal in zip(
                source_decimals,
                canonical_decimals,
                strict=True,
            ):
                if not _canonical_decimal_is_binary64_stable(
                    canonical_decimal
                ) or not exact_decimal_linear_conversion_matches(
                    source_decimal,
                    factor_decimal,
                    offset_decimal,
                    canonical_decimal,
                ):
                    decimal_conversion_matches = False
                    break
            if (
                not np.isfinite(canonical_values).all()
                or not decimal_conversion_matches
            ):
                raise ValueError(
                    f"target {target_id!r} conversion for {column!r} does not reproduce "
                    "canonical data"
                )
        for column in source_frame.columns:
            if column not in converted_columns:
                if source_units.get(column) == canonical_units.get(column) == "categorical":
                    unchanged = _csv_text_column(
                        source_path, str(column)
                    ) == _csv_text_column(canonical_path, str(column))
                elif column in source_units and column in canonical_units:
                    source_decimals = _csv_decimal_column(source_path, str(column))
                    canonical_decimals = _csv_decimal_column(
                        canonical_path, str(column)
                    )
                    unchanged = source_decimals == canonical_decimals and all(
                        _canonical_decimal_is_binary64_stable(value)
                        for value in canonical_decimals
                    )
                else:
                    unchanged = _csv_text_column(
                        source_path, str(column)
                    ) == _csv_text_column(canonical_path, str(column))
                if not unchanged:
                    raise ValueError(
                        f"target {target_id!r} undeclared conversion changed column {column!r}"
                    )
        if not changed_unit_columns and not fixed_unit_changed:
            raise ValueError(
                f"target {target_id!r} converted normalization has no unit change"
            )


def validate_inputs(args: argparse.Namespace) -> Inputs:
    analysis_id = validate_analysis_id(args.analysis_id, "analysis-id")
    repro_id = validate_repro_id(args.repro_id, "repro-id")
    project_dir = Path(args.project_dir).resolve()
    if not project_dir.exists() or not project_dir.is_dir():
        raise ValueError(f"project directory does not exist: {project_dir}")
    assert_no_active_transactions(project_dir)

    manifest_path = project_dir / "manifest.json"
    if not manifest_path.exists():
        raise ValueError(f"project directory is missing manifest.json: {project_dir}")

    resolve_contained(project_dir, "reproduction", "reproduction root")
    resolve_contained(project_dir, "reproduction/runs", "reproduction runs root")
    resolve_contained(project_dir, "reproduction/figures", "reproduction figures root")
    run_dir = resolve_contained(
        project_dir,
        f"reproduction/runs/{repro_id}",
        "reproduction run directory",
    )
    if run_dir.exists():
        raise ValueError(f"reproduction run already exists and will not be overwritten: {run_dir}")

    repro_targets_path = project_dir / "literature" / "repro-targets.json"
    if not repro_targets_path.exists():
        raise ValueError(f"missing literature/repro-targets.json: {repro_targets_path}")
    repro_targets = load_json(repro_targets_path)
    errors = schema_errors("repro-targets.schema.json", repro_targets)
    if errors:
        details = "\n  - ".join(errors)
        raise ValueError(f"literature/repro-targets.json failed schema validation:\n  - {details}")
    target_ids = [target.get("id") for target in repro_targets.get("targets", [])]
    duplicate_target_ids = sorted(
        str(target_id)
        for target_id, count in Counter(target_ids).items()
        if count > 1
    )
    if duplicate_target_ids:
        raise ValueError(
            "literature/repro-targets.json contains duplicate target ids: "
            f"{duplicate_target_ids}"
        )

    selected_targets = select_targets(repro_targets, args.target_id)
    readiness = derive_reproduction_readiness(
        project_dir,
        analysis_id,
        target_id=args.target_id,
        repo_root=REPO_ROOT,
        reference_validator=lambda current_project, target, paper_id: (
            validate_target_normalization(
                current_project,
                target,
                paper_id=paper_id,
            )
        ),
    )
    not_ready_targets = [
        item
        for item in readiness["targets"]
        if item["disposition"] == "not_ready"
    ]
    if not_ready_targets:
        raise ValueError(
            "selected reproduction targets are not ready; dispatch the owner of "
            "each typed requirement before comparison: "
            + json.dumps(not_ready_targets, sort_keys=True, allow_nan=False)
        )
    blocked_targets = {
        str(item["target_id"])
        for item in readiness["targets"]
        if item["disposition"] == "blocked"
    }
    requested_blocked_targets = parse_blocked_targets(args.blocked_targets)
    unknown_blocked_targets = sorted(
        requested_blocked_targets - set(str(item) for item in target_ids)
    )
    if unknown_blocked_targets:
        raise ValueError(
            "--blocked-targets contains ids not declared in repro-targets.json: "
            f"{unknown_blocked_targets}"
        )
    if requested_blocked_targets and requested_blocked_targets != blocked_targets:
        raise ValueError(
            "deprecated --blocked-targets must exactly match the typed readiness "
            f"report; expected {sorted(blocked_targets)}"
        )
    requires_computation = any(
        target.get("kind") != "formula" for target in selected_targets
    )
    if requires_computation:
        calc_tasks = load_json(project_dir / "model" / "calc-tasks.json")
        if not isinstance(calc_tasks, dict):
            raise ValueError("model/calc-tasks.json must contain an object")
    else:
        calc_tasks = {
            "model_name": "not_applicable",
            "model_version": "not_applicable",
            "tasks": [],
        }
    scan_csv = resolve_contained(
        project_dir,
        f"numerics/scan-results/{analysis_id}/scan.csv",
        "scan CSV",
    )
    for target in selected_targets:
        validate_target_normalization(
            project_dir,
            target,
            scan_csv=scan_csv,
            paper_id=str(repro_targets.get("paper_id", "")),
        )
    for target in selected_targets:
        if target.get("kind") != "formula":
            resolve_digitized_path(project_dir, target, scan_csv=scan_csv)

    return Inputs(
        project_dir=project_dir,
        analysis_id=analysis_id,
        repro_id=repro_id,
        run_dir=run_dir,
        repro_targets=repro_targets,
        calc_tasks=calc_tasks,
        scan_csv=scan_csv,
        repro_targets_path=repro_targets_path,
        blocked_targets=blocked_targets,
    )


def score_task(
    task_id: str,
    task: dict[str, Any] | None,
    meta_path: Path | None,
) -> tuple[str, dict[str, str] | None, dict[str, Any] | None]:
    if task is None:
        return "unknown", {
            "task_id": task_id,
            "state": "unknown",
            "reason": "task_not_in_catalog",
        }, None
    if meta_path is None or not meta_path.exists():
        return "unknown", {
            "task_id": task_id,
            "state": "unknown",
            "reason": "result_meta_missing",
        }, None

    try:
        meta = load_json(meta_path)
    except (OSError, StrictJSONError):
        return "unknown", {
            "task_id": task_id,
            "state": "unknown",
            "reason": "result_meta_missing",
        }, None

    meta_schema_errors = schema_errors("result-meta.schema.json", meta)
    if meta_schema_errors:
        return "unknown", {
            "task_id": task_id,
            "state": "unknown",
            "reason": "result_meta_schema_invalid",
        }, meta

    if meta.get("input_provenance", {}).get("verification_status") != "verified":
        return "unknown", {
            "task_id": task_id,
            "state": "unknown",
            "reason": "input_provenance_unverified",
        }, meta
    project_dir = meta_path.parents[2]
    repo_root = Path(__file__).resolve().parent.parent
    try:
        expected_dependencies = calculation_dependency_specs(
            project_dir,
            repo_root,
            task_id,
            meta,
        )
    except (OSError, ValueError):
        return "unknown", {
            "task_id": task_id,
            "state": "unknown",
            "reason": "input_provenance_unverified",
        }, meta
    if verify_dependency_graph(
        meta.get("input_provenance"),
        project_dir,
        repo_root,
        expected_specs=expected_dependencies,
    ):
        return "unknown", {
            "task_id": task_id,
            "state": "unknown",
            "reason": "input_provenance_unverified",
        }, meta

    provenance = meta.get("calculation_provenance")
    benchmark_used = meta.get("benchmark_used_as_input")
    task_type = (task or {}).get("type")
    loop_order = (task or {}).get("loop_order")

    if provenance == "blocked":
        return "unknown", {
            "task_id": task_id,
            "state": "unknown",
            "reason": "provenance_blocked",
        }, meta
    if benchmark_used is True:
        return "tainted", {
            "task_id": task_id,
            "state": "tainted",
            "reason": "benchmark_used_as_input",
        }, meta
    if provenance == "literature_formula_imported":
        return "tainted", {
            "task_id": task_id,
            "state": "tainted",
            "reason": "literature_formula_imported",
        }, meta
    benchmark_status = meta.get("benchmark_status")
    if benchmark_status == "fail":
        return "unknown", {
            "task_id": task_id,
            "state": "unknown",
            "reason": "benchmark_validation_failed",
        }, meta
    if benchmark_status == "skip":
        return "unknown", {
            "task_id": task_id,
            "state": "unknown",
            "reason": "benchmark_validation_skipped",
        }, meta
    if provenance == "manual_tree_algebra" and benchmark_used is False:
        if task_type == "loop" or (isinstance(loop_order, int) and loop_order >= 1):
            return "unknown", {
                "task_id": task_id,
                "state": "unknown",
                "reason": "unsupported_manual_loop",
            }, meta

    artifact_errors = derivation_artifact_errors(
        meta_path.parent,
        task_id,
        task,
        meta,
    )
    if artifact_errors:
        return "unknown", {
            "task_id": task_id,
            "state": "unknown",
            "reason": "derivation_artifacts_unverified",
        }, meta

    if provenance == "package_x_derived" and benchmark_used is False:
        return "unknown", {
            "task_id": task_id,
            "state": "unknown",
            "reason": "derivation_evidence_not_runtime_verified",
        }, meta
    if provenance == "manual_tree_algebra" and benchmark_used is False:
        if task_type == "tree" and loop_order == 0:
            return "manual", {
                "task_id": task_id,
                "state": "manual",
                "reason": "manual_tree_algebra_on_tree_task",
            }, meta

    return "unknown", {
        "task_id": task_id,
        "state": "unknown",
        "reason": "provenance_blocked",
    }, meta


def aggregate_target_independence(task_states: list[str]) -> str:
    if not task_states:
        return "unknown"
    if "tainted" in task_states:
        return "tainted"
    if "unknown" in task_states:
        return "unknown"
    if "manual" in task_states:
        return "independent_manual"
    return "independent"


def aggregate_run_independence(results: list[dict[str, Any]]) -> str:
    return max(
        (str(result["derivation_independence"]) for result in results),
        key=lambda value: INDEPENDENCE_ORDER[value],
    )


def verdict_ceiling(independence: str) -> str:
    if independence == "independent":
        return "pass"
    return "needs_human_review"


def metric_value_for_tolerance(metrics: dict[str, Any], tolerance_kind: str) -> float | None:
    if tolerance_kind == "relative":
        for key in ("max_relative_error", "relative_error", "max_column_relative_error"):
            if key in metrics:
                try:
                    value = float(metrics[key])
                except (TypeError, ValueError):
                    return None
                return value if math.isfinite(value) else None
    if tolerance_kind == "absolute":
        for key in ("max_absolute_error", "absolute_error", "max_hausdorff_distance"):
            if key in metrics:
                try:
                    value = float(metrics[key])
                except (TypeError, ValueError):
                    return None
                return value if math.isfinite(value) else None
    if tolerance_kind == "normalized_distance":
        if "max_normalized_hausdorff_distance" in metrics:
            try:
                value = float(metrics["max_normalized_hausdorff_distance"])
            except (TypeError, ValueError):
                return None
            return value if math.isfinite(value) else None
    return None


def compute_verdict(
    *,
    blocked: bool,
    target_kind: str,
    tolerance: dict[str, Any],
    metrics: dict[str, Any],
    completeness: dict[str, Any] | None,
    ceiling: str,
) -> str:
    if blocked:
        return "blocked"
    if target_kind == "formula":
        return "needs_human_review"
    if tolerance.get("kind") == "qualitative":
        return "needs_human_review"

    if target_kind in {"scan_table", "keyed_benchmark_set"}:
        if completeness is None or completeness.get("complete") is not True:
            return "blocked"
        if completeness.get("row_coverage") != 1.0:
            return "blocked"
        if completeness.get("value_coverage") != 1.0:
            return "blocked"

    if target_kind in {"parametric_curve", "exclusion_region"}:
        required_integrity = {"closed_topology_match": 1}
        if target_kind == "exclusion_region":
            required_integrity.update(
                {
                "component_count_match": 1,
                "component_coverage_ratio": 1.0,
                "face_assignment_defined": 1,
                "face_parent_topology_match": 1,
                "face_probe_coverage_ratio": 1.0,
                "excluded_probe_match": 1,
                }
            )
        for key, expected in required_integrity.items():
            value = metrics.get(key)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                return "blocked"
            if not math.isfinite(float(value)):
                return "blocked"
            if float(value) != float(expected):
                return "fail"
        decision_defined = metrics.get("distance_decision_defined")
        within_tolerance = metrics.get("distance_within_tolerance_proven")
        exceeds_tolerance = metrics.get("distance_exceeds_tolerance_proven")
        if (
            decision_defined not in {0, 1}
            or within_tolerance not in {0, 1}
            or exceeds_tolerance not in {0, 1}
            or within_tolerance + exceeds_tolerance != decision_defined
        ):
            return "blocked"
        raw_tolerance_value = tolerance.get("value")
        lower_bound = metrics.get("max_normalized_hausdorff_distance_lower_bound")
        upper_bound = metrics.get("max_normalized_hausdorff_distance_upper_bound")
        if any(
            not isinstance(value, (int, float)) or isinstance(value, bool)
            for value in (raw_tolerance_value, lower_bound, upper_bound)
        ):
            return "blocked"
        tolerance_value = float(raw_tolerance_value)
        lower_value = float(lower_bound)
        upper_value = float(upper_bound)
        if (
            not all(
                math.isfinite(value)
                for value in (tolerance_value, lower_value, upper_value)
            )
            or tolerance_value < 0
            or lower_value < 0
            or lower_value > upper_value
        ):
            return "blocked"
        expected_within = int(upper_value <= tolerance_value)
        expected_exceeds = int(lower_value > tolerance_value)
        expected_decision = int(expected_within == 1 or expected_exceeds == 1)
        if (
            within_tolerance != expected_within
            or exceeds_tolerance != expected_exceeds
            or decision_defined != expected_decision
        ):
            return "blocked"
        if decision_defined == 0:
            return "blocked"
        if exceeds_tolerance == 1:
            return "fail"

    n_points = metrics.get("n_points_compared")
    if not isinstance(n_points, (int, float)) or isinstance(n_points, bool):
        return "blocked"
    if not math.isfinite(float(n_points)) or int(n_points) <= 0:
        return "blocked"

    metric_value = metric_value_for_tolerance(metrics, str(tolerance.get("kind")))
    if tolerance.get("kind") == "relative" and metrics.get(
        "relative_error_defined"
    ) != 1:
        return "blocked"
    if metric_value is None or tolerance.get("value") is None:
        return "blocked"
    raw_tolerance_value = tolerance["value"]
    if not isinstance(raw_tolerance_value, (int, float)) or isinstance(
        raw_tolerance_value, bool
    ):
        return "blocked"
    tolerance_value = float(raw_tolerance_value)
    if not math.isfinite(tolerance_value) or tolerance_value < 0:
        return "blocked"
    if metric_value > tolerance_value:
        return "fail"
    if ceiling == "pass":
        return "pass"
    return "needs_human_review"


def resolve_digitized_path(
    project_dir: Path,
    target: dict[str, Any],
    *,
    scan_csv: Path | None = None,
) -> Path:
    data_file = str(target.get("data_file", ""))
    resolved = _resolve_under_digitized(
        project_dir,
        data_file,
        f"target {target.get('id')!r} data_file",
    )
    project_root = project_dir.resolve()

    reject_generated_reference_alias(
        project_dir,
        resolved,
        label=f"target {target.get('id')!r} digitized reference {data_file}",
        scan_csv=scan_csv,
    )

    return resolved


def reject_generated_reference_alias(
    project_dir: Path,
    reference: Path,
    *,
    label: str,
    scan_csv: Path | None = None,
) -> None:
    if not reference.exists() or not reference.is_file():
        return
    project_root = project_dir.resolve()
    generated_candidates: set[Path] = set()
    if scan_csv is not None and scan_csv.exists():
        generated_candidates.add(scan_csv.resolve())
    for generated_root in (
        project_root / "numerics",
        project_root / "reproduction",
    ):
        if generated_root.exists():
            generated_candidates.update(
                path.resolve()
                for path in generated_root.rglob("*")
                if path.is_file()
            )

    reference_hash = sha256_file(reference)
    reference_size = reference.stat().st_size
    for generated in sorted(generated_candidates):
        if reference == generated:
            conflict = "same resolved path"
        else:
            try:
                same_inode = reference.samefile(generated)
            except OSError:
                same_inode = False
            if same_inode:
                conflict = "same filesystem object"
            elif (
                generated.stat().st_size == reference_size
                and sha256_file(generated) == reference_hash
            ):
                conflict = "same SHA-256 content"
            else:
                continue
        raise ValueError(
            f"{label} is not independent: it has {conflict} as generated file "
            f"{generated.relative_to(project_root).as_posix()}"
        )


def read_digitized(
    project_dir: Path,
    target: dict[str, Any],
    *,
    scan_csv: Path | None = None,
) -> pd.DataFrame | None:
    data_file = str(target.get("data_file", ""))
    if not data_file:
        return None
    path = resolve_digitized_path(project_dir, target, scan_csv=scan_csv)
    if not path.exists() or not path.is_file():
        return None
    return load_csv(path)


def compute_metrics(
    scan_df: pd.DataFrame,
    digitized_df: pd.DataFrame | None,
    target: dict[str, Any],
) -> tuple[
    dict[str, Any],
    SeriesComparison | BoundaryComparison | None,
    dict[str, Any] | None,
    list[str],
    bool,
]:
    if target["kind"] == "formula":
        return {}, None, None, ["formula_target_requires_human_review"], False
    if digitized_df is None:
        return (
            {},
            None,
            None,
            [f"missing_digitized_data_file: {target.get('data_file')}"],
            True,
        )

    completeness: dict[str, Any] | None = None
    warnings: list[str] = []
    blocked = False
    try:
        if target["kind"] == "benchmark_point":
            metrics, comparison = benchmark_point_metrics(scan_df, digitized_df, target)
        elif target["kind"] == "keyed_benchmark_set":
            result = keyed_benchmark_metrics(scan_df, digitized_df, target)
            metrics = result.metrics
            comparison = result.comparison
            completeness = result.completeness
            blocked = not bool(completeness["complete"])
            if blocked:
                warnings.extend(
                    f"metric_computation_blocked: {reason}"
                    for reason in completeness["blocking_reasons"]
                )
        elif target["kind"] == "figure_curve":
            metrics, comparison = figure_curve_metrics(scan_df, digitized_df, target)
        elif target["kind"] == "parametric_curve":
            metrics, comparison = parametric_curve_metrics(scan_df, digitized_df, target)
        elif target["kind"] == "scan_table":
            result = scan_table_metrics(scan_df, digitized_df, target)
            metrics = result.metrics
            comparison = result.comparison
            completeness = result.completeness
            blocked = not bool(completeness["complete"])
            if blocked:
                warnings.extend(
                    f"metric_computation_blocked: {reason}"
                    for reason in completeness["blocking_reasons"]
                )
        elif target["kind"] == "exclusion_region":
            metrics, comparison = exclusion_region_metrics(scan_df, digitized_df, target)
        else:
            return (
                {},
                None,
                None,
                [f"unsupported_target_kind: {target['kind']}"],
                True,
            )
    except (ValueError, KeyError, TypeError) as exc:
        return {}, None, completeness, [f"metric_computation_blocked: {exc}"], True

    non_finite_metrics = sorted(
        key
        for key, value in metrics.items()
        if isinstance(value, (int, float))
        and not isinstance(value, bool)
        and not math.isfinite(float(value))
    )
    if non_finite_metrics:
        reason = f"non_finite_metrics: {','.join(non_finite_metrics)}"
        if completeness is not None:
            completeness = dict(completeness)
            completeness["complete"] = False
            completeness["blocking_reasons"] = sorted(
                set([*completeness["blocking_reasons"], reason])
            )
        return {}, None, completeness, [f"metric_computation_blocked: {reason}"], True

    return (
        dict(sorted(metrics.items())),
        comparison,
        completeness,
        sorted(set(warnings)),
        blocked,
    )


def unavailable_scan_table_completeness(
    target: dict[str, Any],
    digitized_df: pd.DataFrame | None,
    reason: str,
) -> dict[str, Any]:
    match_columns = [str(item) for item in target.get("match_columns", [])]
    observables = [str(item) for item in target.get("observables", [])]
    reference_rows = len(digitized_df) if digitized_df is not None else 0
    expected_values = reference_rows * len(observables)
    return {
        "complete": False,
        "match_columns": match_columns,
        "reference_rows": reference_rows,
        "matched_reference_rows": 0,
        "missing_reference_rows": reference_rows,
        "row_coverage": 0.0,
        "observables_expected": observables,
        "observables_compared": [],
        "expected_values": expected_values,
        "compared_values": 0,
        "value_coverage": 0.0,
        "blocking_reasons": [reason],
    }


def reference_is_generated_projection(
    scan_df: pd.DataFrame,
    digitized_df: pd.DataFrame,
    target: dict[str, Any],
) -> bool:
    """Detect exact scientific self-comparisons hidden by CSV formatting/projection."""

    kind = target.get("kind")
    if kind in {"benchmark_point", "keyed_benchmark_set", "scan_table"}:
        columns = [
            *[str(item) for item in target.get("match_columns", [])],
            *[str(item) for item in target.get("observables", [])],
        ]
    elif kind in {"figure_curve", "parametric_curve", "exclusion_region"}:
        columns = [str(target.get("x_param")), str(target.get("y_param"))]
        if kind == "parametric_curve":
            columns.append(str(target.get("curve_parameter")))
    else:
        return False
    columns.extend(str(item) for item in target.get("fixed", {}))
    columns = list(dict.fromkeys(columns))
    if not columns or any(
        column not in scan_df.columns or column not in digitized_df.columns
        for column in columns
    ):
        return False
    try:
        scan = filter_fixed_rows(scan_df, target.get("fixed", {}))[columns].copy()
        reference = filter_fixed_rows(
            digitized_df,
            target.get("fixed", {}),
        )[columns].copy()
    except ValueError:
        return False
    if scan.empty or reference.empty:
        return False
    for column in columns:
        scan_numeric = pd.to_numeric(scan[column], errors="coerce")
        reference_numeric = pd.to_numeric(reference[column], errors="coerce")
        if scan_numeric.notna().all() and reference_numeric.notna().all():
            scan[column] = scan_numeric.astype(float)
            reference[column] = reference_numeric.astype(float)
        else:
            scan[column] = scan[column].astype(str)
            reference[column] = reference[column].astype(str)
    scan_rows = {
        tuple(row)
        for row in scan.drop_duplicates().itertuples(index=False, name=None)
    }
    reference_rows = {
        tuple(row)
        for row in reference.drop_duplicates().itertuples(index=False, name=None)
    }
    return bool(reference_rows) and reference_rows <= scan_rows


def existing_generated_files(
    project_dir: Path,
    generated_files: dict[str, dict[str, str]],
) -> dict[str, dict[str, str]]:
    """Declare only complete file pairs that were actually generated."""

    evidence: dict[str, dict[str, str]] = {}
    for group, pair in generated_files.items():
        paths = {extension: project_dir / pair[extension] for extension in ("pdf", "png")}
        if not all(
            path.exists() and path.is_file() and path.stat().st_size > 0
            for path in paths.values()
        ):
            continue
        evidence[group] = {
            "pdf": pair["pdf"],
            "pdf_sha256": sha256_file(paths["pdf"]),
            "png": pair["png"],
            "png_sha256": sha256_file(paths["png"]),
        }
    return evidence


def build_target_result(
    *,
    inputs: Inputs,
    target: dict[str, Any],
    scan_df: pd.DataFrame | None,
    calc_task_by_id: dict[str, dict[str, Any]],
    meta_paths: dict[str, Path],
    output_project_dir: Path,
) -> dict[str, Any]:
    target_id = str(target["id"])
    if target["kind"] == "formula":
        task_ids: list[str] = []
        unmatched_observables: list[str] = []
        task_states = ["unknown"]
        issues: list[dict[str, str]] = [
            {
                "state": "unknown",
                "reason": "formula_reference_only",
            }
        ]
    else:
        task_ids, unmatched_observables = resolve_tasks_for_target(
            target, calc_task_by_id, meta_paths
        )
        task_states = []
        issues = []
        for task_id in task_ids:
            state, issue, meta = score_task(
                task_id,
                calc_task_by_id.get(task_id),
                meta_paths.get(task_id),
            )
            task_states.append(state)
            if issue is not None:
                issues.append(issue)
        for observable in unmatched_observables:
            task_states.append("unknown")
            issues.append(
                {
                    "observable": observable,
                    "state": "unknown",
                    "reason": "observable_task_unmatched",
                }
            )

    independence = aggregate_target_independence(task_states)
    ceiling = verdict_ceiling(independence)
    generated = relative_generated_files(inputs.repro_id, target_id)
    warnings: list[str] = []
    metrics: dict[str, Any] = {}
    comparison_data: SeriesComparison | None = None
    completeness: dict[str, Any] | None = None
    blocked = False
    digitized_df = (
        None
        if target["kind"] == "formula"
        else read_digitized(
            inputs.project_dir,
            target,
            scan_csv=inputs.scan_csv,
        )
    )

    if independence == "tainted":
        blocked = True
        warnings.append(
            "blocked_by_tainted_derivation: literature or benchmark evidence was used "
            "as a computational backend"
        )
    elif target_id in inputs.blocked_targets:
        blocked = True
        warnings.append("blocked_by_orchestrator: missing scan_config_hints, no scan attempted")
    else:
        if target["kind"] == "formula":
            (
                metrics,
                comparison_data,
                completeness,
                metric_warnings,
                blocked,
            ) = compute_metrics(pd.DataFrame(), None, target)
            warnings.extend(metric_warnings)
        elif scan_df is None:
            blocked = True
            warnings.append("metric_computation_blocked: missing scan.csv")
        else:
            (
                metrics,
                comparison_data,
                completeness,
                metric_warnings,
                blocked,
            ) = compute_metrics(scan_df, digitized_df, target)
            warnings.extend(metric_warnings)

    if target["kind"] in {"scan_table", "keyed_benchmark_set"} and completeness is None:
        if target_id in inputs.blocked_targets:
            completeness_reason = "comparison_not_run:blocked_by_orchestrator"
        elif independence == "tainted":
            completeness_reason = "comparison_not_run:tainted_derivation"
        elif scan_df is None:
            completeness_reason = "comparison_not_run:missing_scan_csv"
        elif digitized_df is None:
            completeness_reason = "comparison_not_run:missing_digitized_data"
        else:
            completeness_reason = "comparison_not_run:metric_computation_failed"
        completeness = unavailable_scan_table_completeness(
            target,
            digitized_df,
            completeness_reason,
        )

    if not task_ids:
        warnings.append("no_tasks_matched_target_observables")
    if unmatched_observables:
        warnings.append(
            "unmatched_target_observables: " + ",".join(unmatched_observables)
        )

    if (
        not blocked
        and scan_df is not None
        and digitized_df is not None
        and reference_is_generated_projection(scan_df, digitized_df, target)
    ):
        warnings.append(
            "reference_exact_agreement_notice: canonical rows exactly match a generated "
            "scan projection; acquisition evidence, not agreement alone, determines independence"
        )

    reference_evidence, comparison_evidence = expected_evidence_axes(target)
    if reference_evidence == "synthetic":
        warnings.append(
            "synthetic_reference_evidence: synthetic acquisition cannot support a pass ceiling"
        )

    boundary_mode = target.get("boundary", {}).get("mode")
    if target.get("kind") == "exclusion_region" and boundary_mode in {
        "precomputed_boundary",
        "constraint_verdict_transition",
    }:
        warnings.append(
            "boundary_provenance_requires_human_review: selected boundary mode is not "
            "mechanically bound to a calculation task"
        )
    ceiling = (
        "pass"
        if independence == "independent"
        and reference_evidence == "independent_snapshot"
        and comparison_evidence == "machine_verifiable"
        else "needs_human_review"
    )

    verdict = compute_verdict(
        blocked=blocked,
        target_kind=str(target["kind"]),
        tolerance=target["tolerance"],
        metrics=metrics,
        completeness=completeness,
        ceiling=ceiling,
    )
    if target["kind"] != "formula":
        if verdict == "blocked":
            render_blocked_overlay(
                project_dir=output_project_dir,
                generated_files=generated,
                target=target,
                digitized_df=digitized_df,
                reason=(
                    "blocked by orchestrator"
                    if target_id in inputs.blocked_targets
                    else "insufficient or undefined comparison evidence"
                ),
            )
        else:
            render_all_figures(
                project_dir=output_project_dir,
                generated_files=generated,
                target=target,
                comparison=comparison_data,
            )
    comparison_payload: dict[str, Any] = {
        "kind": target["kind"],
        "metrics": metrics,
    }
    if target["kind"] == "figure_curve":
        comparison_payload["interpolation_method"] = "piecewise_linear_union_knots"
    if target["kind"] == "parametric_curve":
        comparison_payload["geometry_method"] = (
            "normalized_continuous_polyline_hausdorff"
        )
    if completeness is not None:
        comparison_payload["completeness"] = completeness
    return {
        "target_id": target_id,
        "tasks_used": task_ids,
        "derivation_independence": independence,
        "reference_evidence": reference_evidence,
        "comparison_evidence": comparison_evidence,
        "provenance_issues": issues,
        "comparison": comparison_payload,
        "tolerance": target["tolerance"],
        "verdict": verdict,
        "verdict_ceiling": ceiling,
        "generated_files": existing_generated_files(output_project_dir, generated),
        "warnings": sorted(set(warnings)),
        "notes": "",
    }


def digitized_checksums(project_dir: Path, targets: list[dict[str, Any]]) -> dict[str, str]:
    checksums: dict[str, str] = {}
    for target in targets:
        normalization = target.get("normalization", {})
        declared_files = {
            str(target.get("data_file", "")),
            str(normalization.get("source_data_file", "")),
            str(normalization.get("record_file", "")),
        }
        for relpath in sorted(declared_files - {""}):
            path = _resolve_under_digitized(
                project_dir,
                relpath,
                f"target {target.get('id')!r} dependency file",
            )
            if path.exists() and path.is_file():
                checksums[relpath] = sha256_file(path)
    return dict(sorted(checksums.items()))


def model_dependency(project_dir: Path) -> dict[str, Any]:
    """Return the exact active model identity consumed by the comparator."""

    model_path = project_dir / "model" / "model-spec.json"
    model_spec = load_json(model_path)
    if not isinstance(model_spec, dict):
        raise ValueError("model/model-spec.json must contain an object")
    return {
        "version": model_spec.get("version"),
        "checksum": sha256_file(model_path),
    }


def build_depends_on(
    inputs: Inputs,
    targets: list[dict[str, Any]],
    task_ids: list[str],
    *,
    include_scan: bool,
) -> dict[str, Any]:
    uses_computation = any(target.get("kind") != "formula" for target in targets)
    model = (
        model_dependency(inputs.project_dir)
        if uses_computation
        else {"version": None, "checksum": None}
    )
    scan_meta = inputs.scan_csv.parent / "scan.meta.json"
    return {
        "model": model,
        "calculations": {
            "tasks": sorted(set(task_ids)),
            "model_version": model["version"],
        },
        "numerics": {
            "analysis_id": inputs.analysis_id,
            "scan_meta_checksum": sha256_file(scan_meta)
            if include_scan and scan_meta.exists()
            else None,
            "scan_csv_checksum": sha256_file(inputs.scan_csv)
            if include_scan and inputs.scan_csv.exists()
            else None,
        },
        "literature": {
            "repro_targets_checksum": sha256_file(inputs.repro_targets_path),
            "paper_extract_checksum": sha256_file(
                inputs.project_dir / "literature" / "paper-extract.json"
            ),
            "digitized_files_checksums": digitized_checksums(inputs.project_dir, targets),
        },
    }


def write_diagnostic(run_dir: Path, results: list[dict[str, Any]]) -> str | None:
    flagged = [
        result for result in results
        if result["verdict"] in {"fail", "needs_human_review", "blocked"}
    ]
    if not flagged:
        return None
    path = run_dir / "diagnostic.md"
    lines = [
        "# Reproduction Diagnostic",
        "",
        "This diagnostic is generated mechanically from comparison verdicts.",
        "",
    ]
    for result in flagged:
        lines.extend([
            f"## {result['target_id']}",
            "",
            f"- verdict: `{result['verdict']}`",
            f"- verdict_ceiling: `{result['verdict_ceiling']}`",
            f"- derivation_independence: `{result['derivation_independence']}`",
            f"- metrics: `{json.dumps(result['comparison']['metrics'], sort_keys=True)}`",
            f"- warnings: `{json.dumps(result['warnings'], sort_keys=True)}`",
            "",
        ])
    path.write_text("\n".join(lines), encoding="utf-8")
    return f"reproduction/runs/{run_dir.name}/diagnostic.md"


def build_run_result(
    inputs: Inputs,
    targets: list[dict[str, Any]],
    *,
    output_project_dir: Path,
    output_run_dir: Path,
    input_provenance: dict[str, Any],
    include_scan: bool,
) -> dict[str, Any]:
    apply_style(inputs.project_dir)
    calc_task_by_id = task_catalog(inputs.calc_tasks)
    meta_paths = result_meta_paths(inputs.project_dir)
    scan_df = load_csv(inputs.scan_csv) if include_scan else None

    results: list[dict[str, Any]] = []
    all_task_ids: list[str] = []
    for target in targets:
        result = build_target_result(
            inputs=inputs,
            target=target,
            scan_df=scan_df,
            calc_task_by_id=calc_task_by_id,
            meta_paths=meta_paths,
            output_project_dir=output_project_dir,
        )
        all_task_ids.extend(result["tasks_used"])
        results.append(result)

    payload: dict[str, Any] = {
        "repro_id": inputs.repro_id,
        "paper_id": inputs.repro_targets["paper_id"],
        "started_at": utc_now(),
        "finished_at": utc_now(),
        "depends_on": build_depends_on(
            inputs,
            targets,
            all_task_ids,
            include_scan=include_scan,
        ),
        "input_provenance": input_provenance,
        "run_summary": {
            "derivation_independence_aggregate": aggregate_run_independence(results),
            "n_targets_total": len(results),
            "n_targets_pass": sum(1 for item in results if item["verdict"] == "pass"),
            "n_targets_fail": sum(1 for item in results if item["verdict"] == "fail"),
            "n_targets_needs_human_review": sum(
                1 for item in results if item["verdict"] == "needs_human_review"
            ),
            "n_targets_blocked": sum(1 for item in results if item["verdict"] == "blocked"),
        },
        "results": results,
        "notes": "",
    }
    diagnostic_file = write_diagnostic(output_run_dir, results)
    if diagnostic_file is not None:
        payload["diagnostic_file"] = diagnostic_file
    return payload


def build_reproduction_manifest(
    manifest: dict[str, Any],
    payload: dict[str, Any],
    *,
    analysis_id: str,
) -> dict[str, Any]:
    """Merge one immutable comparison run into manifest v2.

    The run payload remains the authoritative per-run dependency record.  The
    aggregate reproduction dependency projection intentionally describes the
    newly published run and is a routing hint only; older runs retain their
    exact dependencies in their immutable ``reproduction-result.json`` files.
    """

    if manifest.get("manifest_version") != 2:
        raise ValueError(
            "manifest.json must be migrated to manifest_version 2 before "
            "publishing a reproduction run"
        )
    candidate = copy.deepcopy(manifest)
    artifacts = candidate.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ValueError("manifest.json artifacts must be an object")
    current = artifacts.get("reproduction")
    if current is not None and not isinstance(current, dict):
        raise ValueError("manifest.json artifacts.reproduction must be an object")
    current_runs = current.get("runs", []) if isinstance(current, dict) else []
    if not isinstance(current_runs, list) or any(
        not isinstance(item, str) for item in current_runs
    ):
        raise ValueError("manifest.json artifacts.reproduction.runs must be a string list")

    repro_id = str(payload["repro_id"])
    if repro_id in current_runs:
        raise ValueError(
            f"manifest.json already records immutable reproduction run {repro_id!r}"
        )
    finished_at = str(payload["finished_at"])
    model = payload.get("depends_on", {}).get("model")
    literature = payload.get("depends_on", {}).get("literature")
    if not isinstance(model, dict) or not isinstance(literature, dict):
        raise ValueError("reproduction result is missing manifest dependency inputs")
    manifest_model = artifacts.get("model")
    uses_model = isinstance(model.get("version"), str) or isinstance(
        model.get("checksum"), str
    )
    if uses_model:
        if (
            not isinstance(manifest_model, dict)
            or candidate.get("active_model_version") != model.get("version")
            or manifest_model.get("version") != model.get("version")
            or manifest_model.get("checksum") != model.get("checksum")
        ):
            raise ValueError(
                "manifest active model changed after the reproduction input snapshot"
            )
    elif model != {"version": None, "checksum": None}:
        raise ValueError(
            "model-free reproduction dependency must use explicit null identity"
        )
    literature_checksum = literature.get("repro_targets_checksum")
    if not isinstance(literature_checksum, str):
        raise ValueError("reproduction result lacks repro_targets_checksum")
    numerics = payload.get("depends_on", {}).get("numerics")
    if not isinstance(numerics, dict):
        raise ValueError("reproduction result lacks numerics dependency projection")
    uses_scan = any(
        isinstance(numerics.get(key), str)
        for key in ("scan_meta_checksum", "scan_csv_checksum")
    )
    if uses_scan:
        if numerics.get("analysis_id") != analysis_id:
            raise ValueError(
                "reproduction result numerics analysis_id does not match the selected "
                "manifest analysis"
            )
        require_consumable_manifest_analysis(candidate, analysis_id)

    artifacts["reproduction"] = {
        "status": "done",
        "runs": sorted([*current_runs, repro_id]),
        "depends_on": {
            "model": {
                "version": model.get("version"),
                "checksum": model.get("checksum"),
            },
            "literature": {"checksum": literature_checksum},
            "numerics": {"analyses": [analysis_id] if uses_scan else []},
        },
        "produced_by": "compare_to_reference.py",
        "timestamp": finished_at,
    }
    candidate["last_updated"] = finished_at[:10]
    history = candidate.get("history")
    if not isinstance(history, list):
        raise ValueError("manifest.json history must be an array")
    history.append(
        {
            "action": "reproduction_run_complete",
            "event_id": uuid.uuid4().hex,
            "repro_id": repro_id,
            "timestamp": finished_at,
            "by": "compare_to_reference.py",
        }
    )
    errors = schema_errors("manifest.schema.json", candidate)
    if errors:
        raise ValueError(
            "generated manifest.json failed schema validation: " + "; ".join(errors)
        )
    return candidate


def validate_output(
    payload: dict[str, Any],
    inputs: Inputs,
    *,
    evidence_project_dir: Path,
    evidence_run_dir: Path,
) -> None:
    errors = schema_errors("reproduction-result.schema.json", payload)
    target_by_id = {
        str(target.get("id")): target
        for target in inputs.repro_targets.get("targets", [])
        if isinstance(target, dict)
    }
    selected_targets = [
        target_by_id[str(result.get("target_id"))]
        for result in payload.get("results", [])
        if str(result.get("target_id")) in target_by_id
    ]
    task_ids = [
        str(task_id)
        for result in payload.get("results", [])
        for task_id in result.get("tasks_used", [])
    ]
    include_scan = any(
        target.get("kind") != "formula"
        and str(target.get("id")) not in inputs.blocked_targets
        for target in selected_targets
    )
    try:
        expected_dependencies = reproduction_dependency_specs(
            inputs.project_dir,
            Path(__file__).resolve().parent.parent,
            selected_targets,
            task_ids,
            analysis_id=inputs.analysis_id,
            include_scan=include_scan,
        )
    except (OSError, ValueError) as exc:
        errors.append(f"cannot derive reproduction dependency coverage: {exc}")
    else:
        errors.extend(
            "input_provenance: " + issue
            for issue in verify_dependency_graph(
                payload.get("input_provenance"),
                inputs.project_dir,
                Path(__file__).resolve().parent.parent,
                expected_specs=expected_dependencies,
            )
        )
    errors.extend(
        reproduction_result_semantic_errors(
            payload,
            project_dir=evidence_project_dir,
            expected_run_dir=evidence_run_dir,
            scientific_project_dir=inputs.project_dir,
        )
    )
    if errors:
        details = "\n  - ".join(errors)
        raise ValueError(f"generated reproduction-result.json failed schema validation:\n  - {details}")


def run(argv: list[str] | None = None) -> int:
    np.random.seed(0)
    try:
        args = parse_args(argv)
        snapshot_project_dir = Path(args.project_dir).expanduser().resolve()
        if not snapshot_project_dir.is_dir():
            raise ValueError(
                f"project directory does not exist: {snapshot_project_dir}"
            )
        with publication_lock(
            snapshot_project_dir,
            "reproduction-input-snapshot",
        ):
            inputs = validate_inputs(args)
            targets = select_targets(inputs.repro_targets, args.target_id)
            calc_task_by_id = task_catalog(inputs.calc_tasks)
            meta_paths = result_meta_paths(inputs.project_dir)
            dependency_task_ids = sorted(
                {
                    task_id
                    for target in targets
                    if target.get("kind") != "formula"
                    for task_id in resolve_tasks_for_target(
                        target,
                        calc_task_by_id,
                        meta_paths,
                    )[0]
                }
            )
            include_scan = any(
                target.get("kind") != "formula"
                and str(target.get("id")) not in inputs.blocked_targets
                for target in targets
            )
            dependency_specs = reproduction_dependency_specs(
                inputs.project_dir,
                Path(__file__).resolve().parent.parent,
                targets,
                dependency_task_ids,
                analysis_id=inputs.analysis_id,
                include_scan=include_scan,
            )
            # Bind the same coherent generation that validation loaded.
            input_provenance = build_dependency_graph(
                inputs.project_dir,
                Path(__file__).resolve().parent.parent,
                dependency_specs,
            )
        reproduction_root = inputs.project_dir / "reproduction"
        final_figures = reproduction_root / "figures" / inputs.repro_id
        if final_figures.exists():
            raise ValueError(
                f"reproduction figure directory already exists and will not be overwritten: "
                f"{final_figures}"
            )
        reproduction_root.mkdir(parents=True, exist_ok=True)
        inputs.run_dir.parent.mkdir(parents=True, exist_ok=True)
        final_figures.parent.mkdir(parents=True, exist_ok=True)
        with PublicationTransaction.begin(
            inputs.project_dir,
            f"reproduction-{inputs.repro_id}",
        ) as transaction:
            manifest_path = inputs.project_dir / "manifest.json"
            manifest_identity = capture_identity(manifest_path)
            current_manifest = load_json(manifest_path)
            if not isinstance(current_manifest, dict):
                raise ValueError("manifest.json must contain an object")
            if capture_identity(manifest_path) != manifest_identity:
                raise ValueError("manifest.json changed while its publication base was read")
            manifest_errors = schema_errors("manifest.schema.json", current_manifest)
            if manifest_errors:
                raise ValueError(
                    "manifest.json failed schema validation before publication: "
                    + "; ".join(manifest_errors)
                )
            staging_root = transaction.staging_dir
            staging_run = transaction.stage_path(
                f"reproduction/runs/{inputs.repro_id}"
            )
            staging_run.mkdir(parents=True)
            payload = build_run_result(
                inputs,
                targets,
                output_project_dir=staging_root,
                output_run_dir=staging_run,
                input_provenance=input_provenance,
                include_scan=include_scan,
            )
            validate_output(
                payload,
                inputs,
                evidence_project_dir=staging_root,
                evidence_run_dir=staging_run,
            )
            write_json(staging_run / "reproduction-result.json", payload)
            manifest_candidate = build_reproduction_manifest(
                current_manifest,
                payload,
                analysis_id=inputs.analysis_id,
            )
            staging_manifest = transaction.stage_path("manifest.json")
            write_json(staging_manifest, manifest_candidate)

            staging_figures = (
                staging_root / "reproduction" / "figures" / inputs.repro_id
            )
            if staging_figures.exists():
                transaction.add(
                    staging_figures,
                    final_figures,
                    mode="create_only",
                    expected_before=capture_identity(final_figures),
                )
            transaction.add(
                staging_run,
                inputs.run_dir,
                mode="create_only",
                expected_before=capture_identity(inputs.run_dir),
            )
            # Manifest is deliberately the final destination: readers holding
            # the project publication lock can never observe it referencing a
            # run tree that has not yet been published.
            transaction.add(
                staging_manifest,
                manifest_path,
                mode="replace",
                expected_before=manifest_identity,
            )

            def verify_publication_inputs() -> None:
                publication_provenance_issues = verify_dependency_graph(
                    input_provenance,
                    inputs.project_dir,
                    Path(__file__).resolve().parent.parent,
                    expected_specs=dependency_specs,
                )
                if publication_provenance_issues:
                    raise ValueError(
                        "input provenance drifted immediately before publication: "
                        + "; ".join(publication_provenance_issues)
                    )

            def verify_published_state() -> None:
                validate_output(
                    payload,
                    inputs,
                    evidence_project_dir=inputs.project_dir,
                    evidence_run_dir=inputs.run_dir,
                )
                published_manifest = load_json(manifest_path)
                if published_manifest != manifest_candidate:
                    raise ValueError(
                        "published manifest does not match the validated candidate"
                    )

            transaction.commit(
                pre_publish_check=verify_publication_inputs,
                post_publish_check=verify_published_state,
            )
    except TransactionCommittedCleanupError as exc:
        print(
            "warning: publication committed successfully, but private cleanup "
            f"is pending for transaction {exc.transaction_id}: {exc.cleanup_error}. "
            "Do not retry this command; use recover_publication_transactions.py "
            "for the same publication anchor.",
            file=sys.stderr,
        )
        return 0
    except Exception as exc:
        print_error(str(exc))
        return 1

    print(
        "wrote reproduction-result.json for "
        f"{inputs.repro_id}: {len(payload['results'])} result(s), "
        f"{payload['run_summary']['n_targets_pass']} pass, "
        f"{payload['run_summary']['n_targets_fail']} fail, "
        f"{payload['run_summary']['n_targets_needs_human_review']} needs_human_review, "
        f"{payload['run_summary']['n_targets_blocked']} blocked"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
