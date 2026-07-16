#!/usr/bin/env python3
"""Validate a hep-numerics scan configuration."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from _strict_json import load_json as strict_load_json
from _identity import (
    validate_analysis_id,
    validate_figure_output_keys,
    validate_named_json_path,
)
from _publication_transaction import publication_lock


class CheckResult:
    """One validation check entry."""

    def __init__(self, status: str, title: str, details: list[str] | None = None) -> None:
        self.status = status
        self.title = title
        self.details = details or []


def load_run_scan_module() -> Any:
    """Load the sibling run_scan implementation so helpers stay aligned."""

    script_path = Path(__file__).resolve()
    target = script_path.parent / "run_scan.py"
    spec = importlib.util.spec_from_file_location("hep_numerics_run_scan_helpers", target)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load run_scan helpers from {target}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


RUN_SCAN = load_run_scan_module()


def build_parser() -> argparse.ArgumentParser:
    """Construct the CLI argument parser."""

    parser = argparse.ArgumentParser(
        description=(
            "Validate a hep-numerics scan-config JSON file with schema checks and "
            "project-aware semantic checks when a workspace project is available."
        )
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--scan-config",
        type=Path,
        help="Path to a scan-config JSON file.",
    )
    group.add_argument(
        "--analysis-id",
        help="Analysis identifier under numerics/scan-configs/, for example analysis-001.",
    )
    parser.add_argument(
        "--project-dir",
        type=Path,
        help="Workspace project directory. Required together with --analysis-id.",
    )
    return parser


def resolve_cli_inputs(args: argparse.Namespace) -> tuple[Path | None, Path, str | None]:
    """Resolve the project directory, scan-config path, and analysis ID."""

    if args.scan_config is not None:
        scan_config_path = args.scan_config.absolute()
        project_dir = args.project_dir.resolve() if args.project_dir is not None else None
        if project_dir is None:
            try:
                project_dir = RUN_SCAN.find_project_dir(scan_config_path.parent)
            except FileNotFoundError:
                project_dir = None
        analysis_id = validate_analysis_id(scan_config_path.stem)
        return project_dir, scan_config_path, analysis_id

    if args.project_dir is None:
        raise ValueError("--project-dir is required when using --analysis-id")

    project_dir = args.project_dir.resolve()
    analysis_id = validate_analysis_id(args.analysis_id)
    scan_config_path = validate_named_json_path(
        project_dir / "numerics" / "scan-configs" / f"{analysis_id}.json",
        project_dir / "numerics" / "scan-configs",
        analysis_id,
        "scan-config",
    )
    return project_dir, scan_config_path, analysis_id


def load_json(path: Path) -> Any:
    """Load JSON from disk."""

    return strict_load_json(path)


def format_schema_issue(issue: Any) -> str:
    """Format a JSON Schema issue with canonical-name guidance where helpful."""

    path_parts = list(issue.absolute_path)
    path = ".".join(str(part) for part in path_parts) or "<root>"
    detail = f"{path}: {issue.message}"
    if path_parts and path_parts[-1] == "canonical_name":
        detail += (
            " (expected an ASCII canonical name from "
            "model-spec.json parameters[].name; map LaTeX/Unicode labels before validation)"
        )
    return detail


def validate_scan_config(
    *,
    scan_config_path: Path,
    project_dir: Path | None = None,
    manifest_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate one scan-config path and return a structured result."""

    if project_dir is not None:
        try:
            inputs = RUN_SCAN.load_inputs(
                project_dir=project_dir,
                scan_config_path=scan_config_path,
                manifest_override=manifest_override,
            )
            shared = RUN_SCAN.validate(inputs)
        except Exception as exc:
            detail = f"NUM-PREFLIGHT-000: shared preflight could not load inputs: {exc}"
            return {
                "scan_config_path": scan_config_path,
                "project_dir": project_dir,
                "scan_config": None,
                "checks": [CheckResult("FAIL", "shared runtime preflight", [detail])],
                "errors": [detail],
                "warnings": [],
                "infos": [],
            }
        checks = [
            CheckResult(
                check.status,
                f"{check.code} {check.title}",
                list(check.details),
            )
            for check in shared["report"].checks
        ]
        errors = [
            f"{check.code}: {detail}"
            for check in shared["report"].checks
            if check.status == "FAIL"
            for detail in check.details
        ]
        warnings = [
            f"{check.code}: {detail}"
            for check in shared["report"].checks
            if check.status == "WARN"
            for detail in check.details
        ]
        return {
            "scan_config_path": inputs["paths"]["scan_config"],
            "project_dir": inputs["project_dir"],
            "scan_config": inputs["scan_config"],
            "checks": checks,
            "errors": errors,
            "warnings": warnings,
            "infos": [],
            "issue_codes": [
                check.code
                for check in shared["report"].checks
                if check.status in {"FAIL", "WARN"}
            ],
        }

    try:
        from jsonschema import Draft202012Validator
    except ImportError as exc:  # pragma: no cover - environment issue
        raise RuntimeError(
            "jsonschema is required to validate scan-config files. "
            "Install the dev requirements first."
        ) from exc

    repo_root = RUN_SCAN.resolve_repo_root()
    schema_path = repo_root / "schemas" / "scan-config.schema.json"
    checks: list[CheckResult] = []
    errors: list[str] = []
    warnings: list[str] = []
    infos: list[str] = []

    scan_config: dict[str, Any] | None = None
    schema: dict[str, Any] | None = None

    scan_error = None
    try:
        scan_config = load_json(scan_config_path)
    except FileNotFoundError:
        scan_error = f"missing file: {scan_config_path}"
    except json.JSONDecodeError as exc:
        scan_error = f"invalid JSON in {scan_config_path}: {exc}"

    schema_error = None
    try:
        schema = load_json(schema_path)
    except FileNotFoundError:
        schema_error = f"missing schema: {schema_path}"
    except json.JSONDecodeError as exc:
        schema_error = f"invalid JSON schema in {schema_path}: {exc}"

    schema_details: list[str] = []
    if scan_error is not None:
        schema_details.append(scan_error)
    if schema_error is not None:
        schema_details.append(schema_error)
    if not schema_details and scan_config is not None and schema is not None:
        validator = Draft202012Validator(schema)
        schema_issues = sorted(
            validator.iter_errors(scan_config),
            key=lambda issue: list(issue.absolute_path),
        )
        for issue in schema_issues:
            schema_details.append(format_schema_issue(issue))

    if schema_details:
        errors.extend(schema_details)
        checks.append(CheckResult("FAIL", "scan-config schema validation", schema_details))
        return {
            "scan_config_path": scan_config_path,
            "project_dir": project_dir,
            "scan_config": scan_config,
            "checks": checks,
            "errors": errors,
            "warnings": warnings,
            "infos": infos,
        }

    checks.append(
        CheckResult("PASS", "scan-config schema validation", ["scan-config JSON schema validation passed"])
    )

    assert scan_config is not None  # for type-checkers after early return
    try:
        payload_analysis_id = validate_analysis_id(scan_config.get("analysis_id"))
        if payload_analysis_id != scan_config_path.stem:
            raise ValueError(
                f"scan-config payload analysis_id {payload_analysis_id!r} does not "
                f"match filename stem {scan_config_path.stem!r}"
            )
        if project_dir is not None:
            scan_config_path = validate_named_json_path(
                scan_config_path,
                project_dir / "numerics" / "scan-configs",
                payload_analysis_id,
                "scan-config",
            )
    except ValueError as exc:
        detail = str(exc)
        errors.append(detail)
        checks.append(CheckResult("FAIL", "scan-config identity binding", [detail]))
        return {
            "scan_config_path": scan_config_path,
            "project_dir": project_dir,
            "scan_config": scan_config,
            "checks": checks,
            "errors": errors,
            "warnings": warnings,
            "infos": infos,
        }
    checks.append(
        CheckResult(
            "PASS",
            "scan-config identity binding",
            ["payload analysis_id, filename stem, and contained config path agree"],
        )
    )
    scan_parameter_names = [entry["canonical_name"] for entry in scan_config.get("scan_parameters", [])]
    fixed_parameter_names = [entry["canonical_name"] for entry in scan_config.get("fixed_parameters", [])]
    observable_bindings = scan_config.get("observables", [])
    observable_names = [binding["observable"] for binding in observable_bindings]
    constraints_used = scan_config.get("constraints_used", [])
    figure_specs = scan_config.get("figures", [])

    config_semantic_details: list[str] = []
    duplicate_names = sorted(set(scan_parameter_names) & set(fixed_parameter_names))
    if duplicate_names:
        config_semantic_details.append(
            f"parameters cannot be both scan and fixed: {duplicate_names}"
        )
    for label, names in (
        ("scan_parameters", scan_parameter_names),
        ("fixed_parameters", fixed_parameter_names),
        ("observables", observable_names),
        ("constraints_used", constraints_used),
    ):
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            config_semantic_details.append(f"{label} contains duplicate names: {duplicates}")

    try:
        validate_figure_output_keys(scan_config)
    except ValueError as exc:
        config_semantic_details.append(str(exc))

    depends_on_task_ids = set(scan_config.get("depends_on", {}).get("task_ids", []))
    for binding in observable_bindings:
        source = binding.get("source", {})
        observable = binding.get("observable")
        if source.get("type") == "task":
            task_id = source.get("task_id")
            if task_id not in depends_on_task_ids:
                config_semantic_details.append(
                    f"observable {observable!r} references task {task_id!r} which is not listed in depends_on.task_ids"
                )

    scan_name_set = set(scan_parameter_names)
    observable_name_set = set(observable_names)
    constraint_name_set = set(constraints_used)
    for index, figure in enumerate(figure_specs, start=1):
        kind = figure.get("kind")
        label = f"figures[{index - 1}]"
        if figure.get("x") not in scan_name_set:
            config_semantic_details.append(
                f"{label}.x {figure.get('x')!r} is not covered by scan_parameters"
            )
        active_axes = {figure.get("x")}
        if kind == "exclusion_2d":
            active_axes.add(figure.get("y"))
        expected_hidden = scan_name_set - active_axes
        declared_hidden = set(figure.get("fixed", {}))
        if declared_hidden != expected_hidden:
            config_semantic_details.append(
                f"{label}.fixed must exactly declare hidden scan parameters: "
                f"expected {sorted(expected_hidden)}, got {sorted(declared_hidden)}"
            )
        if kind == "exclusion_2d":
            if figure.get("y") not in scan_name_set:
                config_semantic_details.append(
                    f"{label}.y {figure.get('y')!r} is not covered by scan_parameters"
                )
            missing_constraints = [
                constraint_id
                for constraint_id in figure.get("constraints", [])
                if constraint_id not in constraint_name_set
            ]
            if missing_constraints:
                config_semantic_details.append(
                    f"{label}.constraints references IDs not in constraints_used: {missing_constraints}"
                )
        elif kind == "scan_1d":
            missing_observables = [
                observable for observable in figure.get("observables", [])
                if observable not in observable_name_set
            ]
            if missing_observables:
                config_semantic_details.append(
                    f"{label}.observables references names not in observables: {missing_observables}"
                )

    if config_semantic_details:
        errors.extend(config_semantic_details)
        checks.append(CheckResult("FAIL", "config-internal semantic checks", config_semantic_details))
    else:
        checks.append(
            CheckResult(
                "PASS",
                "config-internal semantic checks",
                ["scan/fixed conflicts and figure references are internally consistent"],
            )
        )

    assert project_dir is None
    info_detail = (
        "project-aware semantic checks were skipped because no workspace project "
        "could be inferred from the scan-config path"
    )
    infos.append(info_detail)
    checks.append(CheckResult("SKIP", "project-aware semantic checks", [info_detail]))
    return {
        "scan_config_path": scan_config_path,
        "project_dir": project_dir,
        "scan_config": scan_config,
        "checks": checks,
        "errors": errors,
        "warnings": warnings,
        "infos": infos,
    }


def print_report(result: dict[str, Any]) -> None:
    """Render the validation report to stdout."""

    print("== Scan Config Validation Report ==")
    for check in result["checks"]:
        print(f"[{check.status}] {check.title}")
        for detail in check.details:
            print(f"  - {detail}")
    for info in result["infos"]:
        print(f"[INFO] {info}")
    for warning in result["warnings"]:
        print(f"[WARN] {warning}")


def exit_code_for_result(result: dict[str, Any]) -> int:
    """Map a validation result to the requested exit-code convention."""

    if result["errors"]:
        return 1
    if result["warnings"]:
        return 2
    return 0


def main() -> int:
    """CLI entrypoint."""

    parser = build_parser()
    args = parser.parse_args()

    try:
        project_dir, scan_config_path, _ = resolve_cli_inputs(args)
        with publication_lock(
            project_dir,
            "scan-config-validation",
        ):
            result = validate_scan_config(
                scan_config_path=scan_config_path,
                project_dir=project_dir,
            )
        print_report(result)
        return exit_code_for_result(result)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
