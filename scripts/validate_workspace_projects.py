#!/usr/bin/env python3
"""Validate workspace project JSON artifacts against repository schemas."""

from __future__ import annotations

import argparse
import ast
import csv
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any


ARTIFACT_SCHEMA_BY_RELATIVE_PATH = {
    "manifest.json": "manifest.schema.json",
    "model/model-spec.json": "model-spec.schema.json",
    "model/calc-tasks.json": "calc-tasks.schema.json",
    "model/benchmarks.json": "benchmarks.schema.json",
    "constraints/constraints-data.json": "constraints-data.schema.json",
    "literature/paper-meta.json": "paper-meta.schema.json",
    "literature/repro-targets.json": "repro-targets.schema.json",
}
JSON_ONLY_ARTIFACT_RELATIVE_PATHS = (
    "literature/paper-extract.json",
)
RESULT_META_SCHEMA_NAME = "result-meta.schema.json"
SCAN_META_SCHEMA_NAME = "scan-meta.schema.json"
REPRODUCTION_RESULT_SCHEMA_NAME = "reproduction-result.schema.json"
TASK_DIR_PATTERN = re.compile(r"^task-\d{3}$")
RUN_DIR_PATTERN = re.compile(r"^run-\d{3}$")
PLACEHOLDER_PATTERN = re.compile(r"\{\{[A-Za-z0-9_]+\}\}")
CALCULATION_REQUIRED_FILES = (
    "request.md",
    "result-summary.md",
    "result.wl",
    "result-python.py",
    "result-meta.json",
    "run-instructions.md",
)
CALCULATION_PLACEHOLDER_FILES = (
    "request.md",
    "result-summary.md",
    "run-instructions.md",
    "result-python.py",
    "result-meta.json",
)
PACKAGE_X_LOOP_MARKERS = (
    "LoopIntegrate",
    "LoopRefine",
    "Projector",
    "LoopRefineSeries",
    "Transverse",
    "Longitudinal",
)
PACKAGE_X_TREE_MARKERS = (
    "Spur",
    "Contract",
    "LoopRefine",
)
BENCHMARK_BACKEND_PATTERNS = (
    re.compile(r"benchmark formula used by result\.wl", re.IGNORECASE),
    re.compile(r"implements (?:the )?.*benchmark.*formula", re.IGNORECASE),
    re.compile(r"result\.wl directly implements .*benchmark", re.IGNORECASE),
)


class PythonStaticCheck:
    def __init__(self, tree: ast.Module | None, error: str | None) -> None:
        self.tree = tree
        self.error = error


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_validate_scan_config_module(repo_root: Path) -> Any:
    module_path = (
        repo_root
        / ".agents"
        / "skills"
        / "hep-numerics"
        / "scripts"
        / "validate_scan_config.py"
    )
    spec = importlib.util.spec_from_file_location(
        "hep_numerics_workspace_validate_scan_config",
        module_path,
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load validate_scan_config.py from {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate workspace/projects/* JSON artifacts against the repository schemas."
        )
    )
    parser.add_argument(
        "projects",
        nargs="*",
        help=(
            "Optional project names under workspace/projects/. "
            "If omitted, validate every project directory."
        ),
    )
    parser.add_argument(
        "--workspace-root",
        default="workspace/projects",
        help="Workspace projects root relative to the repository root.",
    )
    return parser.parse_args()


def iter_project_dirs(workspace_root: Path, selected_projects: list[str]) -> list[Path]:
    if selected_projects:
        return [workspace_root / name for name in selected_projects]
    return sorted(path for path in workspace_root.iterdir() if path.is_dir())


def format_error_path(path: list[Any]) -> str:
    return ".".join(str(part) for part in path) or "<root>"


def validate_json_data(data: Any, validator: Any) -> list[str]:
    errors = sorted(validator.iter_errors(data), key=lambda err: list(err.absolute_path))
    return [f"{format_error_path(list(err.absolute_path))}: {err.message}" for err in errors]


def find_placeholder_hits(path: Path) -> list[str]:
    hits: list[str] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if PLACEHOLDER_PATTERN.search(line):
            hits.append(f"{path.name}:{lineno}: unresolved placeholder")
    return hits


def parse_python_source(path: Path) -> PythonStaticCheck:
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        return PythonStaticCheck(None, str(exc))
    try:
        return PythonStaticCheck(ast.parse(source, filename=path.as_posix()), None)
    except SyntaxError as exc:
        return PythonStaticCheck(None, f"syntax error: {exc}")


def top_level_functions(tree: ast.Module) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    return {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def function_parameter_names(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    args = node.args
    parameters = [
        *args.posonlyargs,
        *args.args,
        *args.kwonlyargs,
    ]
    return {parameter.arg for parameter in parameters if parameter.arg != "self"}


def has_row_scan_config_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    parameters = [
        *node.args.posonlyargs,
        *node.args.args,
        *node.args.kwonlyargs,
    ]
    return [parameter.arg for parameter in parameters] == ["row", "scan_config"]


def is_not_implemented_expression(node: ast.AST) -> bool:
    if isinstance(node, ast.Call):
        function = node.func
        if isinstance(function, ast.Name):
            return function.id == "NotImplementedError"
        if isinstance(function, ast.Attribute):
            return function.attr == "NotImplementedError"
    if isinstance(node, ast.Name):
        return node.id == "NotImplementedError"
    return False


def raises_not_implemented(node: ast.AST) -> bool:
    if isinstance(node, ast.Raise) and node.exc is not None:
        return is_not_implemented_expression(node.exc)
    return any(
        isinstance(child, ast.Raise)
        and child.exc is not None
        and is_not_implemented_expression(child.exc)
        for child in ast.walk(node)
    )


def task_definitions_by_id(loaded_artifacts: dict[str, Any]) -> dict[str, dict[str, Any]]:
    calc_tasks = loaded_artifacts.get("model/calc-tasks.json")
    if not isinstance(calc_tasks, dict):
        return {}
    return {
        task["task_id"]: task
        for task in calc_tasks.get("tasks", [])
        if isinstance(task, dict) and isinstance(task.get("task_id"), str)
    }


def read_optional_text(path: Path) -> str:
    if not path.exists() or not path.is_file() or path.stat().st_size == 0:
        return ""
    return path.read_text(encoding="utf-8")


def validate_result_provenance(
    task_dir: Path,
    task_label: str,
    task: dict[str, Any] | None,
    result_meta: dict[str, Any],
) -> list[str]:
    issues: list[str] = []
    provenance = result_meta.get("calculation_provenance")
    benchmark_used = result_meta.get("benchmark_used_as_input")
    package_x_methods = result_meta.get("package_x_methods")
    task_type = task.get("type") if isinstance(task, dict) else None

    if provenance == "package_x_derived":
        if benchmark_used is not False:
            issues.append(
                "package_x_derived task must have benchmark_used_as_input == false"
            )
        if not isinstance(package_x_methods, list) or not package_x_methods:
            issues.append("package_x_derived task must list non-empty package_x_methods")

        source_wl = read_optional_text(task_dir / str(result_meta.get("source_wl", "")))
        if task_type == "loop" and not any(
            marker in source_wl for marker in PACKAGE_X_LOOP_MARKERS
        ):
            issues.append(
                "loop task is marked package_x_derived but result.wl does not contain "
                "a Package-X loop route such as LoopIntegrate, LoopRefine, or Projector"
            )
        if task_type == "tree" and not any(
            marker in source_wl for marker in PACKAGE_X_TREE_MARKERS
        ):
            issues.append(
                "tree task is marked package_x_derived but result.wl does not contain "
                "a Package-X tree algebra route such as Spur, Contract, or LoopRefine"
            )

        provenance_text = "\n".join(
            read_optional_text(task_dir / filename)
            for filename in ("request.md", "result-summary.md", "result-python.py")
        )
        for pattern in BENCHMARK_BACKEND_PATTERNS:
            if pattern.search(provenance_text):
                issues.append(
                    "package_x_derived task appears to describe a benchmark formula "
                    "as the computation backend"
                )
                break

    if provenance == "manual_tree_algebra" and task_type == "loop":
        issues.append("loop task cannot use manual_tree_algebra provenance")

    if provenance == "blocked" and result_meta.get("translation_status") == "complete":
        issues.append("blocked provenance cannot have translation_status == 'complete'")

    for issue in issues:
        print(f"FAIL {task_label}/result-meta.json: provenance: {issue}")

    if (
        not issues
        and benchmark_used is True
        and provenance in {"manual_tree_algebra", "literature_formula_imported"}
    ):
        print(
            f"WARN {task_label}/result-meta.json: benchmark_used_as_input is true; "
            "this is an acknowledged non-Package-X backend"
        )

    if not issues and provenance is not None:
        print(f"OK   {task_label}/result-meta.json provenance")

    return issues


def validate_calculation_outputs(
    project_dir: Path,
    validators: dict[str, Any],
    loaded_artifacts: dict[str, Any],
) -> tuple[int, bool]:
    calculations_dir = project_dir / "calculations"
    if not calculations_dir.exists():
        print("SKIP calculations/task-*")
        return 0, False

    failures = 0
    task_dirs: list[Path] = []
    for entry in sorted(calculations_dir.iterdir()):
        if not entry.is_dir():
            continue
        if not TASK_DIR_PATTERN.fullmatch(entry.name):
            failures += 1
            print(
                f"FAIL calculations/{entry.name}: unexpected directory name "
                "(expected task-XXX)"
            )
            continue
        task_dirs.append(entry)

    if not task_dirs:
        print("SKIP calculations/task-*")
        return failures, False

    model_spec = loaded_artifacts.get("model/model-spec.json")
    allowed_parameter_names = {
        parameter["name"] for parameter in model_spec.get("parameters", [])
    } if model_spec else None
    task_by_id = task_definitions_by_id(loaded_artifacts)

    for task_dir in task_dirs:
        task_label = f"calculations/{task_dir.name}"
        required_missing = False

        for filename in CALCULATION_REQUIRED_FILES:
            path = task_dir / filename
            if not path.exists():
                failures += 1
                required_missing = True
                print(f"FAIL {task_label}/{filename}: missing required file")
                continue
            if path.stat().st_size == 0:
                failures += 1
                required_missing = True
                print(f"FAIL {task_label}/{filename}: required file is empty")

        for filename in CALCULATION_PLACEHOLDER_FILES:
            path = task_dir / filename
            if not path.exists() or path.stat().st_size == 0:
                continue
            hits = find_placeholder_hits(path)
            if hits:
                failures += 1
                print(f"FAIL {task_label}/{filename}: unresolved template placeholders")
                for hit in hits:
                    print(f"  - {hit}")

        result_summary_path = task_dir / "result-summary.md"
        if result_summary_path.exists() and result_summary_path.stat().st_size > 0:
            summary_text = result_summary_path.read_text(encoding="utf-8")
            if "## Benchmark Verification" not in summary_text:
                failures += 1
                print(
                    f"FAIL {task_label}/result-summary.md: missing "
                    "'## Benchmark Verification' section"
                )

        result_meta_path = task_dir / "result-meta.json"
        if not result_meta_path.exists() or result_meta_path.stat().st_size == 0:
            continue

        try:
            result_meta = load_json(result_meta_path)
        except json.JSONDecodeError as exc:
            failures += 1
            print(f"FAIL {task_label}/result-meta.json: invalid JSON ({exc})")
            continue

        result_meta_errors = validate_json_data(
            result_meta, validators[RESULT_META_SCHEMA_NAME]
        )
        if result_meta_errors:
            failures += 1
            print(f"FAIL {task_label}/result-meta.json <- {RESULT_META_SCHEMA_NAME}")
            for error in result_meta_errors:
                print(f"  - {error}")
            continue

        print(f"OK   {task_label}/result-meta.json <- {RESULT_META_SCHEMA_NAME}")

        if result_meta.get("task_id") != task_dir.name:
            failures += 1
            print(
                f"FAIL {task_label}/result-meta.json: task_id "
                f"{result_meta.get('task_id')!r} does not match directory name {task_dir.name!r}"
            )

        referenced_python = task_dir / result_meta.get("python_file", "")
        if result_meta.get("python_file") and not referenced_python.exists():
            failures += 1
            print(
                f"FAIL {task_label}/result-meta.json: referenced python_file "
                f"{result_meta.get('python_file')!r} does not exist"
            )
        elif result_meta.get("python_file"):
            static_check = parse_python_source(referenced_python)
            if static_check.error is not None or static_check.tree is None:
                failures += 1
                print(
                    f"FAIL {task_label}/{referenced_python.name}: "
                    f"{static_check.error}"
                )
            else:
                python_function = result_meta.get("python_function")
                functions = top_level_functions(static_check.tree)
                translation_status = result_meta.get("translation_status")
                if translation_status == "failed" and (
                    raises_not_implemented(static_check.tree)
                    or (
                        isinstance(python_function, str)
                        and python_function in functions
                        and raises_not_implemented(functions[python_function])
                    )
                ):
                    print(
                        f"OK   {task_label}/{referenced_python.name}: failed translation "
                        "placeholder raises NotImplementedError"
                    )
                elif not isinstance(python_function, str) or not python_function:
                    failures += 1
                    print(
                        f"FAIL {task_label}/result-meta.json: python_function is missing"
                    )
                elif python_function not in functions:
                    failures += 1
                    print(
                        f"FAIL {task_label}/{referenced_python.name}: python_function "
                        f"{python_function!r} is not defined"
                    )
                else:
                    function_names = function_parameter_names(functions[python_function])
                    if allowed_parameter_names is not None:
                        invalid_function_names = sorted(
                            function_names - allowed_parameter_names
                        )
                        if invalid_function_names:
                            failures += 1
                            print(
                                f"FAIL {task_label}/{referenced_python.name}: "
                                f"python_function {python_function!r} has non-canonical "
                                f"parameters {invalid_function_names}"
                            )
                        else:
                            print(
                                f"OK   {task_label}/{referenced_python.name}: "
                                "python_function matches result-meta and canonical parameters"
                            )
                    else:
                        print(
                            f"OK   {task_label}/{referenced_python.name}: "
                            "python_function matches result-meta"
                        )

        referenced_wl = task_dir / result_meta.get("source_wl", "")
        if result_meta.get("source_wl") and not referenced_wl.exists():
            failures += 1
            print(
                f"FAIL {task_label}/result-meta.json: referenced source_wl "
                f"{result_meta.get('source_wl')!r} does not exist"
            )

        if allowed_parameter_names is not None:
            invalid_names = sorted(
                {
                    parameter["canonical_name"]
                    for parameter in result_meta.get("parameters", [])
                    if parameter["canonical_name"] not in allowed_parameter_names
                }
            )
            if invalid_names:
                failures += 1
                print(
                    f"FAIL {task_label}/result-meta.json: unknown canonical parameter names "
                    f"{invalid_names}"
                )

        provenance_issues = validate_result_provenance(
            task_dir,
            task_label,
            task_by_id.get(task_dir.name),
            result_meta,
        )
        failures += len(provenance_issues)

        if not required_missing:
            print(f"OK   {task_label}/required-files")

    return failures, True


def validate_calculations_artifact(
    project_dir: Path,
    loaded_artifacts: dict[str, Any],
) -> tuple[int, bool]:
    manifest = loaded_artifacts.get("manifest.json")
    if not manifest:
        print("SKIP manifest calculations artifact")
        return 0, False

    calculations = manifest.get("artifacts", {}).get("calculations")
    if not isinstance(calculations, dict):
        print("SKIP manifest calculations artifact")
        return 0, True

    failures = 0
    completed_tasks = calculations.get("completed_tasks", [])
    pending_tasks = calculations.get("pending_tasks", [])
    if not isinstance(completed_tasks, list):
        completed_tasks = []
    if not isinstance(pending_tasks, list):
        pending_tasks = []
    completed_tasks = [task_id for task_id in completed_tasks if isinstance(task_id, str)]
    pending_tasks = [task_id for task_id in pending_tasks if isinstance(task_id, str)]

    for task_id in completed_tasks:
        result_meta_path = project_dir / "calculations" / task_id / "result-meta.json"
        relpath = result_meta_path.relative_to(project_dir).as_posix()
        if not result_meta_path.exists():
            failures += 1
            print(
                "FAIL manifest.json: calculations.completed_tasks references "
                f"missing task {task_id!r} ({relpath})"
            )

    calc_tasks = loaded_artifacts.get("model/calc-tasks.json")
    if calc_tasks:
        declared_task_ids = {
            task.get("task_id")
            for task in calc_tasks.get("tasks", [])
            if isinstance(task, dict) and isinstance(task.get("task_id"), str)
        }

        unknown_completed = sorted(set(completed_tasks) - declared_task_ids)
        if unknown_completed:
            failures += 1
            print(
                "FAIL manifest.json: calculations.completed_tasks contains tasks "
                f"not declared in model/calc-tasks.json: {unknown_completed}"
            )

        unknown_pending = sorted(set(pending_tasks) - declared_task_ids)
        if unknown_pending:
            failures += 1
            print(
                "FAIL manifest.json: calculations.pending_tasks contains tasks "
                f"not declared in model/calc-tasks.json: {unknown_pending}"
            )

        overlap = sorted(set(pending_tasks) & set(completed_tasks))
        if overlap:
            failures += 1
            print(
                "FAIL manifest.json: calculations.pending_tasks overlaps "
                f"calculations.completed_tasks: {overlap}"
            )

    active_model_version = manifest.get("active_model_version")
    if active_model_version is not None:
        for task_id in completed_tasks:
            result_meta_path = project_dir / "calculations" / task_id / "result-meta.json"
            if not result_meta_path.exists() or result_meta_path.stat().st_size == 0:
                continue
            try:
                result_meta = load_json(result_meta_path)
            except json.JSONDecodeError:
                continue
            result_model_version = result_meta.get("depends_on", {}).get("model_version")
            if result_model_version != active_model_version:
                relpath = result_meta_path.relative_to(project_dir).as_posix()
                print(
                    f"WARN {relpath}: depends_on.model_version "
                    f"{result_model_version!r} does not match manifest "
                    f"active_model_version {active_model_version!r} "
                    "(stale calculation)"
                )

    if failures == 0:
        print("OK   manifest.json calculations artifact")

    return failures, True


def validate_json_only_artifacts(project_dir: Path) -> tuple[int, bool]:
    failures = 0
    validated_any = False

    for relpath in JSON_ONLY_ARTIFACT_RELATIVE_PATHS:
        artifact_path = project_dir / relpath
        if not artifact_path.exists():
            print(f"SKIP {relpath}")
            continue

        validated_any = True
        try:
            load_json(artifact_path)
        except json.JSONDecodeError as exc:
            failures += 1
            print(f"FAIL {relpath}: invalid JSON ({exc})")
            continue

        print(f"OK   {relpath}: valid JSON")

    return failures, validated_any


def validate_literature_manifest_files(
    project_dir: Path,
    loaded_artifacts: dict[str, Any],
) -> tuple[int, bool]:
    manifest = loaded_artifacts.get("manifest.json")
    if not manifest:
        print("SKIP manifest literature artifact")
        return 0, False

    artifacts = manifest.get("artifacts", {})
    literature = artifacts.get("literature") if isinstance(artifacts, dict) else None
    if not isinstance(literature, dict):
        print("SKIP manifest literature artifact")
        return 0, True

    failures = 0
    files = literature.get("files", [])
    if not isinstance(files, list):
        files = []

    for relpath in files:
        if not isinstance(relpath, str):
            continue
        artifact_path = project_dir / relpath
        if not artifact_path.exists():
            failures += 1
            print(
                "FAIL manifest.json: literature.files references missing file "
                f"{relpath!r}"
            )

    if failures == 0:
        print("OK   manifest.json literature artifact")

    return failures, True


def validate_reproduction_results(
    project_dir: Path,
    validators: dict[str, Any],
    loaded_artifacts: dict[str, Any],
) -> tuple[int, bool]:
    manifest = loaded_artifacts.get("manifest.json")
    manifest_runs: list[str] = []
    if isinstance(manifest, dict):
        artifacts = manifest.get("artifacts", {})
        reproduction = (
            artifacts.get("reproduction")
            if isinstance(artifacts, dict)
            else None
        )
        runs = reproduction.get("runs", []) if isinstance(reproduction, dict) else []
        if isinstance(runs, list):
            manifest_runs = [item for item in runs if isinstance(item, str)]

    runs_dir = project_dir / "reproduction" / "runs"
    run_dirs = (
        sorted(path for path in runs_dir.iterdir() if path.is_dir())
        if runs_dir.exists()
        else []
    )

    if not run_dirs and not manifest_runs:
        print("SKIP reproduction/runs/*/reproduction-result.json")
        return 0, False

    failures = 0
    validated_any = True

    for repro_id in manifest_runs:
        result_path = runs_dir / repro_id / "reproduction-result.json"
        if not result_path.exists():
            failures += 1
            print(
                "FAIL manifest.json: reproduction.runs references missing "
                f"run {repro_id!r} ({result_path.relative_to(project_dir).as_posix()})"
            )

    for run_dir in run_dirs:
        run_label = f"reproduction/runs/{run_dir.name}"
        if not RUN_DIR_PATTERN.fullmatch(run_dir.name):
            failures += 1
            print(
                f"FAIL {run_label}: unexpected directory name "
                "(expected run-XXX)"
            )
            continue

        result_path = run_dir / "reproduction-result.json"
        relpath = result_path.relative_to(project_dir).as_posix()
        if not result_path.exists():
            failures += 1
            print(f"FAIL {relpath}: missing reproduction-result.json")
            continue

        try:
            result = load_json(result_path)
        except json.JSONDecodeError as exc:
            failures += 1
            print(f"FAIL {relpath}: invalid JSON ({exc})")
            continue

        errors = validate_json_data(
            result, validators[REPRODUCTION_RESULT_SCHEMA_NAME]
        )
        if errors:
            failures += 1
            print(f"FAIL {relpath} <- {REPRODUCTION_RESULT_SCHEMA_NAME}")
            for error in errors:
                print(f"  - {error}")
            continue

        print(f"OK   {relpath} <- {REPRODUCTION_RESULT_SCHEMA_NAME}")

        if result.get("repro_id") != run_dir.name:
            failures += 1
            print(
                f"FAIL {relpath}: repro_id {result.get('repro_id')!r} "
                f"does not match directory name {run_dir.name!r}"
            )

    return failures, validated_any


def validate_analysis_summaries(project_dir: Path) -> tuple[int, bool]:
    scan_results_dir = project_dir / "numerics" / "scan-results"
    if not scan_results_dir.exists():
        print("SKIP numerics/analysis-summary-*.md")
        return 0, False

    analysis_dirs = sorted(path for path in scan_results_dir.iterdir() if path.is_dir())
    if not analysis_dirs:
        print("SKIP numerics/analysis-summary-*.md")
        return 0, False

    failures = 0
    for analysis_dir in analysis_dirs:
        analysis_id = analysis_dir.name
        summary_path = project_dir / "numerics" / f"analysis-summary-{analysis_id}.md"
        relpath = summary_path.relative_to(project_dir).as_posix()
        if not summary_path.exists():
            failures += 1
            print(f"FAIL {relpath}: missing analysis-summary for {analysis_id}")
            continue
        text = summary_path.read_text(encoding="utf-8")
        if not text.strip() or "# " not in text:
            failures += 1
            print(f"FAIL {relpath}: empty analysis-summary or missing '# ' heading")
            continue
        print(f"OK   {relpath}")

    return failures, True


def validate_custom_observables(project_dir: Path) -> tuple[int, bool]:
    custom_path = project_dir / "numerics" / "custom_observables.py"
    if not custom_path.exists():
        print("SKIP numerics/custom_observables.py")
        return 0, False

    relpath = custom_path.relative_to(project_dir).as_posix()
    static_check = parse_python_source(custom_path)
    if static_check.error is not None or static_check.tree is None:
        print(f"FAIL {relpath}: custom_observables parse failed ({static_check.error})")
        return 1, True

    functions = top_level_functions(static_check.tree)
    public_functions = [
        name for name in functions if not name.startswith("_")
    ]
    observable_functions = [
        name for name in public_functions if name.startswith("observable_")
    ]
    if not public_functions:
        print(f"FAIL {relpath}: no top-level observable function definitions found")
        return 1, True

    if observable_functions:
        invalid_signatures = sorted(
            name
            for name in observable_functions
            if not has_row_scan_config_signature(functions[name])
        )
        if invalid_signatures:
            print(
                f"FAIL {relpath}: observable_* functions with invalid signature "
                f"{invalid_signatures}; expected (row, scan_config)"
            )
            return 1, True
        print(f"OK   {relpath}: static AST parse and observable_* functions found")
    else:
        print(f"OK   {relpath}: static AST parse and public observable functions found")
    return 0, True


def validate_scan_configs(
    project_dir: Path,
    validate_scan_config_module: Any,
) -> tuple[int, bool]:
    scan_configs_dir = project_dir / "numerics" / "scan-configs"
    if not scan_configs_dir.exists():
        print("SKIP numerics/scan-configs/*.json")
        return 0, False

    scan_config_paths = sorted(scan_configs_dir.glob("*.json"))
    if not scan_config_paths:
        print("SKIP numerics/scan-configs/*.json")
        return 0, False

    failures = 0
    for scan_config_path in scan_config_paths:
        relpath = scan_config_path.relative_to(project_dir).as_posix()
        try:
            result = validate_scan_config_module.validate_scan_config(
                scan_config_path=scan_config_path,
                project_dir=project_dir,
            )
        except Exception as exc:
            failures += 1
            print(f"FAIL {relpath}: validate_scan_config.py raised {exc}")
            continue

        if result["errors"]:
            failures += 1
            print(f"FAIL {relpath} <- validate_scan_config.py")
            for error in result["errors"]:
                print(f"  - {error}")
            for warning in result["warnings"]:
                print(f"  - warning: {warning}")
        else:
            if result["warnings"]:
                print(f"WARN {relpath} <- validate_scan_config.py")
                for warning in result["warnings"]:
                    print(f"  - {warning}")
            else:
                print(f"OK   {relpath} <- validate_scan_config.py")

    return failures, True


def count_csv_data_rows(scan_csv_path: Path) -> int:
    with scan_csv_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        try:
            next(reader)
        except StopIteration as exc:
            raise ValueError("scan.csv is empty") from exc
        return sum(1 for _ in reader)


def validate_scan_meta_outputs(
    project_dir: Path,
    validators: dict[str, Any],
) -> tuple[int, bool]:
    scan_results_dir = project_dir / "numerics" / "scan-results"
    if not scan_results_dir.exists():
        print("SKIP numerics/scan-results/*/scan.meta.json")
        return 0, False

    analysis_dirs = sorted(path for path in scan_results_dir.iterdir() if path.is_dir())
    if not analysis_dirs:
        print("SKIP numerics/scan-results/*/scan.meta.json")
        return 0, False

    failures = 0
    for analysis_dir in analysis_dirs:
        analysis_id = analysis_dir.name
        meta_path = analysis_dir / "scan.meta.json"
        relpath = meta_path.relative_to(project_dir).as_posix()

        if not meta_path.exists():
            failures += 1
            print(f"FAIL {relpath}: missing scan.meta.json for {analysis_id}")
            continue

        try:
            scan_meta = load_json(meta_path)
        except json.JSONDecodeError as exc:
            failures += 1
            print(f"FAIL {relpath}: invalid JSON ({exc})")
            continue

        errors = validate_json_data(scan_meta, validators[SCAN_META_SCHEMA_NAME])
        if errors:
            failures += 1
            print(f"FAIL {relpath} <- {SCAN_META_SCHEMA_NAME}")
            for error in errors:
                print(f"  - {error}")
            continue

        print(f"OK   {relpath} <- {SCAN_META_SCHEMA_NAME}")

        if scan_meta.get("analysis_id") != analysis_id:
            failures += 1
            print(
                f"FAIL {relpath}: analysis_id {scan_meta.get('analysis_id')!r} "
                f"does not match directory name {analysis_id!r}"
            )

        scan_config_snapshot = scan_meta.get("scan_config_snapshot")
        if not isinstance(scan_config_snapshot, dict):
            continue

        snapshot_analysis_id = scan_config_snapshot.get("analysis_id")
        if snapshot_analysis_id != scan_meta.get("analysis_id"):
            failures += 1
            print(
                f"FAIL {relpath}: scan_config_snapshot.analysis_id "
                f"{snapshot_analysis_id!r} does not match scan_meta analysis_id "
                f"{scan_meta.get('analysis_id')!r}"
            )

        depends_on = scan_config_snapshot.get("depends_on", {})
        if isinstance(depends_on, dict):
            snapshot_model_version = depends_on.get("model_version")
            if snapshot_model_version != scan_meta.get("model_version"):
                failures += 1
                print(
                    f"FAIL {relpath}: model_version {scan_meta.get('model_version')!r} "
                    "does not match "
                    f"scan_config_snapshot.depends_on.model_version {snapshot_model_version!r}"
                )

            snapshot_model_checksum = depends_on.get("model_checksum")
            if snapshot_model_checksum != scan_meta.get("model_checksum"):
                failures += 1
                print(
                    f"FAIL {relpath}: model_checksum {scan_meta.get('model_checksum')!r} "
                    "does not match "
                    f"scan_config_snapshot.depends_on.model_checksum {snapshot_model_checksum!r}"
                )

        n_points = scan_meta.get("n_points")
        classified_points = (
            scan_meta.get("n_allowed", 0)
            + scan_meta.get("n_excluded", 0)
            + scan_meta.get("n_skipped", 0)
        )
        if classified_points != n_points:
            failures += 1
            print(
                f"FAIL {relpath}: n_allowed + n_excluded + n_skipped "
                f"= {classified_points}, expected n_points {n_points}"
            )
        else:
            print(f"OK   {relpath}: classification counts match n_points")

        scan_csv_path = analysis_dir / "scan.csv"
        scan_csv_relpath = scan_csv_path.relative_to(project_dir).as_posix()
        if not scan_csv_path.exists():
            failures += 1
            print(f"FAIL {scan_csv_relpath}: missing scan.csv for run scan meta")
            continue

        try:
            data_row_count = count_csv_data_rows(scan_csv_path)
        except ValueError as exc:
            failures += 1
            print(f"FAIL {scan_csv_relpath}: {exc}")
            continue

        if data_row_count != n_points:
            failures += 1
            print(
                f"FAIL {scan_csv_relpath}: data row count {data_row_count} "
                f"does not match scan.meta.json n_points {n_points}"
            )
        else:
            print(f"OK   {scan_csv_relpath}: data row count matches scan.meta.json")

    return failures, True


def main() -> int:
    try:
        from jsonschema import Draft202012Validator
    except ImportError:
        print(
            "error: jsonschema is not installed in the active Python environment.\n"
            "Create and activate a virtual environment, then install the dev requirements:\n"
            "  python3 -m venv .venv\n"
            "  source .venv/bin/activate\n"
            "  python3 -m pip install -r requirements-dev.txt",
            file=sys.stderr,
        )
        return 1

    args = parse_args()
    repo_root = Path(__file__).resolve().parent.parent
    workspace_root = repo_root / args.workspace_root
    schemas_dir = repo_root / "schemas"
    validate_scan_config_module = load_validate_scan_config_module(repo_root)

    if not workspace_root.exists():
        print(f"error: workspace root not found: {workspace_root}", file=sys.stderr)
        return 1

    validators = {}
    for schema_name in set(ARTIFACT_SCHEMA_BY_RELATIVE_PATH.values()) | {
        RESULT_META_SCHEMA_NAME,
        SCAN_META_SCHEMA_NAME,
        REPRODUCTION_RESULT_SCHEMA_NAME,
    }:
        schema_path = schemas_dir / schema_name
        schema = load_json(schema_path)
        validators[schema_name] = Draft202012Validator(schema)

    project_dirs = iter_project_dirs(workspace_root, args.projects)
    if not project_dirs:
        print(f"error: no project directories found under {workspace_root}", file=sys.stderr)
        return 1

    failures = 0
    for project_dir in project_dirs:
        if not project_dir.exists():
            failures += 1
            print(f"FAIL {project_dir.name}: project directory not found")
            continue

        print(f"[{project_dir.name}]")
        validated_any = False
        loaded_artifacts: dict[str, Any] = {}
        for relpath, schema_name in ARTIFACT_SCHEMA_BY_RELATIVE_PATH.items():
            artifact_path = project_dir / relpath
            if not artifact_path.exists():
                print(f"SKIP {relpath}")
                continue

            validated_any = True
            try:
                data = load_json(artifact_path)
            except json.JSONDecodeError as exc:
                failures += 1
                print(f"FAIL {relpath}: invalid JSON ({exc})")
                continue

            loaded_artifacts[relpath] = data
            validator = validators[schema_name]
            errors = validate_json_data(data, validator)

            if errors:
                failures += 1
                print(f"FAIL {relpath} <- {schema_name}")
                for error in errors:
                    print(f"  - {error}")
            else:
                print(f"OK   {relpath} <- {schema_name}")

        artifact_failures, artifact_validated = validate_calculations_artifact(
            project_dir, loaded_artifacts
        )
        failures += artifact_failures
        validated_any = validated_any or artifact_validated

        json_failures, json_validated = validate_json_only_artifacts(project_dir)
        failures += json_failures
        validated_any = validated_any or json_validated

        literature_failures, literature_validated = validate_literature_manifest_files(
            project_dir, loaded_artifacts
        )
        failures += literature_failures
        validated_any = validated_any or literature_validated

        calculation_failures, calculations_validated = validate_calculation_outputs(
            project_dir, validators, loaded_artifacts
        )
        failures += calculation_failures
        validated_any = validated_any or calculations_validated

        scan_config_failures, scan_configs_validated = validate_scan_configs(
            project_dir, validate_scan_config_module
        )
        failures += scan_config_failures
        validated_any = validated_any or scan_configs_validated

        scan_meta_failures, scan_meta_validated = validate_scan_meta_outputs(
            project_dir, validators
        )
        failures += scan_meta_failures
        validated_any = validated_any or scan_meta_validated

        summary_failures, summaries_validated = validate_analysis_summaries(project_dir)
        failures += summary_failures
        validated_any = validated_any or summaries_validated

        reproduction_failures, reproduction_validated = validate_reproduction_results(
            project_dir, validators, loaded_artifacts
        )
        failures += reproduction_failures
        validated_any = validated_any or reproduction_validated

        custom_failures, custom_validated = validate_custom_observables(project_dir)
        failures += custom_failures
        validated_any = validated_any or custom_validated

        if not validated_any:
            print("SKIP no known JSON artifacts found")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
