#!/usr/bin/env python3
"""Validate a hep-numerics scan configuration."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any


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
        scan_config_path = args.scan_config.resolve()
        project_dir = args.project_dir.resolve() if args.project_dir is not None else None
        if project_dir is None:
            try:
                project_dir = RUN_SCAN.find_project_dir(scan_config_path.parent)
            except FileNotFoundError:
                project_dir = None
        analysis_id = None
        return project_dir, scan_config_path, analysis_id

    if args.project_dir is None:
        raise ValueError("--project-dir is required when using --analysis-id")

    project_dir = args.project_dir.resolve()
    scan_config_path = project_dir / "numerics" / "scan-configs" / f"{args.analysis_id}.json"
    return project_dir, scan_config_path, args.analysis_id


def load_json(path: Path) -> Any:
    """Load JSON from disk."""

    return json.loads(path.read_text(encoding="utf-8"))


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
) -> dict[str, Any]:
    """Validate one scan-config path and return a structured result."""

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

    if project_dir is None:
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

    model_spec_path = project_dir / "model" / "model-spec.json"
    calc_tasks_path = project_dir / "model" / "calc-tasks.json"
    constraints_path = project_dir / "constraints" / "constraints-data.json"
    custom_observables_path = project_dir / "numerics" / "custom_observables.py"

    project_artifact_details: list[str] = []
    try:
        model_spec = load_json(model_spec_path)
        calc_tasks = load_json(calc_tasks_path)
        constraints_data = load_json(constraints_path)
    except FileNotFoundError as exc:
        project_artifact_details.append(str(exc))
        model_spec = {}
        calc_tasks = {}
        constraints_data = {}
    except json.JSONDecodeError as exc:
        project_artifact_details.append(str(exc))
        model_spec = {}
        calc_tasks = {}
        constraints_data = {}

    if project_artifact_details:
        errors.extend(project_artifact_details)
        checks.append(CheckResult("FAIL", "project artifact loading", project_artifact_details))
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
            "project artifact loading",
            [
                f"loaded {model_spec_path}",
                f"loaded {calc_tasks_path}",
                f"loaded {constraints_path}",
            ],
        )
    )

    model_parameters = {
        parameter["name"]: parameter
        for parameter in model_spec.get("parameters", [])
        if isinstance(parameter, dict) and "name" in parameter
    }
    canonical_parameter_names = set(model_parameters)
    calc_tasks_by_id = {
        task["task_id"]: task
        for task in calc_tasks.get("tasks", [])
        if isinstance(task, dict) and "task_id" in task
    }
    constraints_by_id = {
        constraint["id"]: constraint
        for constraint in constraints_data.get("constraints", [])
        if isinstance(constraint, dict) and "id" in constraint
    }

    parameter_details: list[str] = []
    for name in scan_parameter_names:
        parameter = model_parameters.get(name)
        if parameter is None:
            parameter_details.append(
                f"scan parameter {name!r} is not a model-spec canonical name"
            )
            continue
        if parameter.get("role") != "scan":
            warnings.append(
                f"scan parameter {name!r} has role {parameter.get('role')!r}, expected 'scan'"
            )
    for name in fixed_parameter_names:
        if name not in canonical_parameter_names:
            parameter_details.append(
                f"fixed parameter {name!r} is not a model-spec canonical name"
            )

    for index, figure in enumerate(figure_specs, start=1):
        label = f"figures[{index - 1}]"
        x_name = figure.get("x")
        if x_name is not None and x_name not in canonical_parameter_names:
            parameter_details.append(
                f"{label}.x {x_name!r} is not a model-spec canonical name"
            )
        y_name = figure.get("y")
        if y_name is not None and y_name not in canonical_parameter_names:
            parameter_details.append(
                f"{label}.y {y_name!r} is not a model-spec canonical name"
            )

    if parameter_details:
        errors.extend(parameter_details)
        checks.append(CheckResult("FAIL", "model-spec parameter coverage", parameter_details))
    elif warnings:
        warning_details = [warning for warning in warnings if "scan parameter" in warning]
        if warning_details:
            checks.append(CheckResult("WARN", "model-spec parameter coverage", warning_details))
        else:
            checks.append(
                CheckResult("PASS", "model-spec parameter coverage", ["all scan/fixed parameter names exist in model-spec"])
            )
    else:
        checks.append(
            CheckResult("PASS", "model-spec parameter coverage", ["all scan/fixed parameter names exist in model-spec"])
        )

    constraint_details: list[str] = []
    for constraint_id in constraints_used:
        if constraint_id not in constraints_by_id:
            constraint_details.append(
                f"constraints_used entry {constraint_id!r} is missing from constraints-data.json"
            )

    if constraint_details:
        errors.extend(constraint_details)
        checks.append(CheckResult("FAIL", "constraint lookup", constraint_details))
    else:
        checks.append(
            CheckResult("PASS", "constraint lookup", ["every constraints_used entry exists in constraints-data.json"])
        )

    binding_details: list[str] = []
    custom_module = None
    custom_import_attempted = False

    for binding in observable_bindings:
        observable = binding["observable"]
        source = binding["source"]
        if source["type"] == "task":
            task_id = source["task_id"]
            if task_id not in calc_tasks_by_id:
                binding_details.append(
                    f"observable {observable!r} references unknown calc-task {task_id!r}"
                )
                continue
            result_meta_path = project_dir / "calculations" / task_id / "result-meta.json"
            try:
                result_meta = load_json(result_meta_path)
            except FileNotFoundError:
                binding_details.append(
                    f"observable {observable!r} task {task_id!r} is missing {result_meta_path}"
                )
                continue
            except json.JSONDecodeError as exc:
                binding_details.append(
                    f"observable {observable!r} task {task_id!r} has invalid result-meta JSON: {exc}"
                )
                continue
            if result_meta.get("translation_status") != "complete":
                binding_details.append(
                    f"observable {observable!r} task {task_id!r} has translation_status "
                    f"{result_meta.get('translation_status')!r}, expected 'complete'"
                )
            provenance = result_meta.get("calculation_provenance")
            if provenance == "blocked":
                binding_details.append(
                    f"observable {observable!r} task {task_id!r} has blocked calculation_provenance"
                )
            if provenance in RUN_SCAN.FORMULA_FALLBACK_PROVENANCES:
                message = (
                    f"observable {observable!r} task {task_id!r} uses formula fallback "
                    f"provenance {provenance!r}"
                )
                if scan_config.get("allow_formula_fallback") is not True:
                    binding_details.append(
                        message + "; set allow_formula_fallback=true to opt in explicitly"
                    )
                else:
                    warnings.append(
                        message
                        + f" (benchmark_used_as_input={result_meta.get('benchmark_used_as_input')!r})"
                    )
            if provenance == "package_x_derived":
                if result_meta.get("benchmark_used_as_input") is not False:
                    binding_details.append(
                        f"observable {observable!r} task {task_id!r} is package_x_derived "
                        f"but benchmark_used_as_input is {result_meta.get('benchmark_used_as_input')!r}"
                    )
                if not result_meta.get("package_x_methods"):
                    binding_details.append(
                        f"observable {observable!r} task {task_id!r} is package_x_derived "
                        "but package_x_methods is empty"
                    )
        elif source["type"] == "custom":
            if not custom_import_attempted:
                custom_import_attempted = True
                if not custom_observables_path.exists():
                    binding_details.append(
                        f"custom observable module is missing: {custom_observables_path}"
                    )
                else:
                    try:
                        custom_module = RUN_SCAN.import_module_from_path(
                            "hep_numerics_validate_custom_observables",
                            custom_observables_path,
                        )
                    except Exception as exc:
                        binding_details.append(
                            f"failed to import custom observables from {custom_observables_path}: {exc}"
                        )
            function_name = source["function"]
            if custom_module is not None and not hasattr(custom_module, function_name):
                binding_details.append(
                    f"custom observable function {function_name!r} is missing from {custom_observables_path.name}"
                )

    if binding_details:
        errors.extend(binding_details)
        checks.append(CheckResult("FAIL", "observable binding resolution", binding_details))
    else:
        checks.append(
            CheckResult(
                "PASS",
                "observable binding resolution",
                ["task/custom observable bindings resolve within the current project context"],
            )
        )

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
