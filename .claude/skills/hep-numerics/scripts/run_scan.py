#!/usr/bin/env python3
"""Run a hep-numerics parameter scan from a scan-config JSON file."""

from __future__ import annotations

import argparse
import ast
import csv
import importlib.metadata
import importlib.util
import inspect
import itertools
import json
import math
import re
import sys
import textwrap
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np


ALLOWED_IMPLEMENTATION_STATUSES = {"direct", "interpolated", "manual_only"}
ALLOWED_INTERPOLATION_METHODS = {
    "linear",
    "loglog_linear",
    "log_x_linear",
    "log_y_linear",
}
ALLOWED_EXTRAPOLATION_POLICIES = {"forbidden", "nearest"}
FORMULA_FALLBACK_PROVENANCES = {
    "literature_formula_imported",
    "manual_tree_algebra",
}
SAFE_FUNCTIONS = {
    "abs": np.abs,
    "sqrt": np.sqrt,
    "sin": np.sin,
    "cos": np.cos,
    "tan": np.tan,
    "exp": np.exp,
    "log": np.log,
    "log10": np.log10,
}


class CheckResult:
    """One compliance-check result entry."""

    def __init__(self, number: int, title: str, status: str, details: list[str] | None = None) -> None:
        self.number = number
        self.title = title
        self.status = status
        self.details = details or []


class ValidationReport:
    """Aggregated compliance report."""

    def __init__(self) -> None:
        self.checks: list[CheckResult] = []

    @property
    def has_errors(self) -> bool:
        return any(check.status == "FAIL" for check in self.checks)


class SafeExpressionEvaluator(ast.NodeVisitor):
    """Evaluate a tightly-whitelisted math expression."""

    def __init__(self, names: dict[str, float]) -> None:
        self.names = names

    def visit_Expression(self, node: ast.Expression) -> float:
        return self.visit(node.body)

    def visit_Constant(self, node: ast.Constant) -> float:
        if isinstance(node.value, (int, float)):
            return float(node.value)
        raise ValueError(f"unsupported constant {node.value!r}")

    def visit_Num(self, node: ast.Num) -> float:  # pragma: no cover - py311 compatibility
        return float(node.n)

    def visit_Name(self, node: ast.Name) -> float:
        if node.id not in self.names:
            raise ValueError(f"unknown name {node.id!r}")
        return float(self.names[node.id])

    def visit_BinOp(self, node: ast.BinOp) -> float:
        left = self.visit(node.left)
        right = self.visit(node.right)
        op = node.op
        if isinstance(op, ast.Add):
            return left + right
        if isinstance(op, ast.Sub):
            return left - right
        if isinstance(op, ast.Mult):
            return left * right
        if isinstance(op, ast.Div):
            return left / right
        if isinstance(op, ast.Pow):
            return left**right
        if isinstance(op, ast.Mod):
            return left % right
        raise ValueError(f"unsupported binary operator {type(op).__name__}")

    def visit_UnaryOp(self, node: ast.UnaryOp) -> float:
        operand = self.visit(node.operand)
        op = node.op
        if isinstance(op, ast.UAdd):
            return +operand
        if isinstance(op, ast.USub):
            return -operand
        raise ValueError(f"unsupported unary operator {type(op).__name__}")

    def visit_Call(self, node: ast.Call) -> float:
        if not isinstance(node.func, ast.Name):
            raise ValueError("only simple function calls are allowed")
        function_name = node.func.id
        if function_name not in SAFE_FUNCTIONS:
            raise ValueError(f"unsupported function {function_name!r}")
        if node.keywords:
            raise ValueError("keyword arguments are not allowed")
        args = [self.visit(arg) for arg in node.args]
        return float(SAFE_FUNCTIONS[function_name](*args))

    def generic_visit(self, node: ast.AST) -> float:
        raise ValueError(f"unsupported AST node {type(node).__name__}")


class SafeExpressionValidator(ast.NodeVisitor):
    """Validate that an AST uses only whitelisted node types."""

    def visit_Expression(self, node: ast.Expression) -> None:
        self.visit(node.body)

    def visit_Constant(self, node: ast.Constant) -> None:
        if not isinstance(node.value, (int, float)):
            raise ValueError(f"unsupported constant {node.value!r}")

    def visit_Num(self, node: ast.Num) -> None:  # pragma: no cover - py311 compatibility
        if not isinstance(node.n, (int, float)):
            raise ValueError(f"unsupported numeric literal {node.n!r}")

    def visit_Name(self, node: ast.Name) -> None:
        if not isinstance(node.ctx, ast.Load):
            raise ValueError("only variable loads are allowed")

    def visit_BinOp(self, node: ast.BinOp) -> None:
        if not isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod)):
            raise ValueError(f"unsupported binary operator {type(node.op).__name__}")
        self.visit(node.left)
        self.visit(node.right)

    def visit_UnaryOp(self, node: ast.UnaryOp) -> None:
        if not isinstance(node.op, (ast.UAdd, ast.USub)):
            raise ValueError(f"unsupported unary operator {type(node.op).__name__}")
        self.visit(node.operand)

    def visit_Call(self, node: ast.Call) -> None:
        if not isinstance(node.func, ast.Name):
            raise ValueError("only simple function calls are allowed")
        if node.func.id not in SAFE_FUNCTIONS:
            raise ValueError(f"unsupported function {node.func.id!r}")
        if node.keywords:
            raise ValueError("keyword arguments are not allowed")
        for arg in node.args:
            self.visit(arg)

    def generic_visit(self, node: ast.AST) -> None:
        raise ValueError(f"unsupported AST node {type(node).__name__}")


class CompiledFormula:
    """A compiled safe-eval formula."""

    def __init__(self, expression: str, tree: ast.Expression, constants: dict[str, float]) -> None:
        self.expression = expression
        self.tree = tree
        self.constants = constants

    def evaluate(self, parameters: dict[str, float]) -> float:
        evaluator = SafeExpressionEvaluator({**self.constants, **parameters})
        return float(evaluator.visit(self.tree))


def resolve_repo_root() -> Path:
    """Infer the repository root from the current skill layout."""

    script_dir = Path(__file__).resolve().parent
    skill_dir = script_dir.parent
    skills_dir = skill_dir.parent
    platform_dir = skills_dir.parent

    if (
        script_dir.name == "scripts"
        and skill_dir.name == "hep-numerics"
        and skills_dir.name == "skills"
        and platform_dir.name in {".agents", ".claude"}
    ):
        return platform_dir.parent

    raise RuntimeError(
        "Cannot infer repository root from the current skill layout. "
        "Expected the script under "
        "<repo>/.agents/skills/hep-numerics/scripts/ or "
        "<repo>/.claude/skills/hep-numerics/scripts/."
    )


def resolve_skill_dir() -> Path:
    """Infer the current hep-numerics skill directory from the script path."""

    script_dir = Path(__file__).resolve().parent
    skill_dir = script_dir.parent
    if script_dir.name != "scripts" or skill_dir.name != "hep-numerics":
        raise RuntimeError(
            "Cannot infer hep-numerics skill directory from the current script path."
        )
    return skill_dir


def load_template(name: str) -> str:
    """Load one skill-local template file."""

    template_path = resolve_skill_dir() / "templates" / name
    return template_path.read_text(encoding="utf-8")


def load_manifest_helpers() -> object:
    """Load the sibling manifest helper module from disk."""

    helper_path = Path(__file__).resolve().parent / "_manifest.py"
    spec = importlib.util.spec_from_file_location("hep_numerics_manifest_helpers", helper_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load manifest helpers from {helper_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


MANIFEST = load_manifest_helpers()


def render_custom_observables_template(project_name: str) -> str:
    """Render the project-level custom_observables.py template."""

    return load_template("custom_observables.py.tmpl").format(project_name=project_name)


def render_analysis_summary_template(**context: str) -> str:
    """Render the analysis summary template."""

    return load_template("analysis-summary.md.tmpl").format(**context)


def import_module_from_path(module_name: str, path: Path) -> Any:
    """Import a Python module from a concrete filesystem path."""

    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load module spec from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def load_json_file(path: Path) -> Any:
    """Load JSON from disk."""

    return json.loads(path.read_text(encoding="utf-8"))


def safe_load_json(path: Path) -> tuple[Any | None, str | None]:
    """Load JSON while capturing filesystem and decode issues."""

    try:
        return load_json_file(path), None
    except FileNotFoundError:
        return None, f"missing file: {path}"
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON in {path}: {exc}"


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


def find_project_dir(start: Path) -> Path:
    """Walk upward until a project root with manifest.json is found."""

    candidate = start.resolve()
    for current in (candidate, *candidate.parents):
        if (current / "manifest.json").exists():
            return current
    raise FileNotFoundError(
        f"could not infer project directory from {start}; no manifest.json found"
    )


def build_parser() -> argparse.ArgumentParser:
    """Construct the CLI argument parser."""

    parser = argparse.ArgumentParser(
        description=(
            "Run a hep-numerics scan from numerics/scan-configs/{analysis_id}.json "
            "or from an explicit scan-config path."
        )
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--scan-config",
        type=Path,
        help="Path to a scan-config JSON file. The project root is inferred from it.",
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


def resolve_cli_inputs(args: argparse.Namespace) -> tuple[Path, Path, str]:
    """Resolve the project directory, scan-config path, and analysis ID."""

    if args.scan_config is not None:
        scan_config_path = args.scan_config.resolve()
        project_dir = find_project_dir(scan_config_path.parent)
        scan_config, error = safe_load_json(scan_config_path)
        if error is not None:
            raise FileNotFoundError(error)
        analysis_id = scan_config.get("analysis_id")
        if not isinstance(analysis_id, str) or not analysis_id:
            raise ValueError(
                f"scan-config at {scan_config_path} does not contain a valid analysis_id"
            )
        return project_dir, scan_config_path, analysis_id

    if args.project_dir is None:
        raise ValueError("--project-dir is required when using --analysis-id")

    project_dir = args.project_dir.resolve()
    analysis_id = args.analysis_id
    scan_config_path = project_dir / "numerics" / "scan-configs" / f"{analysis_id}.json"
    return project_dir, scan_config_path, analysis_id


def load_inputs(
    *,
    project_dir: Path | None = None,
    analysis_id: str | None = None,
    scan_config_path: Path | None = None,
) -> dict[str, Any]:
    """Load the scan config plus all project artifacts needed for validation/run."""

    if scan_config_path is None:
        if project_dir is None or analysis_id is None:
            raise ValueError(
                "load_inputs requires either scan_config_path or both project_dir and analysis_id"
            )
        scan_config_path = (
            project_dir.resolve() / "numerics" / "scan-configs" / f"{analysis_id}.json"
        )
    else:
        scan_config_path = scan_config_path.resolve()

    if project_dir is None:
        project_dir = find_project_dir(scan_config_path.parent)
    else:
        project_dir = project_dir.resolve()

    repo_root = resolve_repo_root()
    paths = {
        "scan_config": scan_config_path,
        "schema": repo_root / "schemas" / "scan-config.schema.json",
        "result_meta_schema": repo_root / "schemas" / "result-meta.schema.json",
        "manifest": project_dir / "manifest.json",
        "model_spec": project_dir / "model" / "model-spec.json",
        "calc_tasks": project_dir / "model" / "calc-tasks.json",
        "constraints_data": project_dir / "constraints" / "constraints-data.json",
        "custom_observables": project_dir / "numerics" / "custom_observables.py",
    }

    data: dict[str, Any] = {
        "repo_root": repo_root,
        "project_dir": project_dir,
        "analysis_id": analysis_id,
        "paths": paths,
    }
    data["scan_config"], data["scan_config_error"] = safe_load_json(paths["scan_config"])
    data["schema"], data["schema_error"] = safe_load_json(paths["schema"])
    data["result_meta_schema"], data["result_meta_schema_error"] = safe_load_json(
        paths["result_meta_schema"]
    )
    data["manifest"], data["manifest_error"] = safe_load_json(paths["manifest"])
    data["model_spec"], data["model_spec_error"] = safe_load_json(paths["model_spec"])
    data["calc_tasks"], data["calc_tasks_error"] = safe_load_json(paths["calc_tasks"])
    data["constraints_data"], data["constraints_data_error"] = safe_load_json(
        paths["constraints_data"]
    )

    scan_config = data["scan_config"] or {}
    if data["analysis_id"] is None and isinstance(scan_config, dict):
        data["analysis_id"] = scan_config.get("analysis_id")

    model_spec = data["model_spec"] or {}
    calc_tasks = data["calc_tasks"] or {}
    constraints_data = data["constraints_data"] or {}

    data["model_parameters_by_name"] = {
        parameter["name"]: parameter
        for parameter in model_spec.get("parameters", [])
        if isinstance(parameter, dict) and "name" in parameter
    }
    data["calc_tasks_by_id"] = {
        task["task_id"]: task
        for task in calc_tasks.get("tasks", [])
        if isinstance(task, dict) and "task_id" in task
    }
    data["constraints_by_id"] = {
        constraint["id"]: constraint
        for constraint in constraints_data.get("constraints", [])
        if isinstance(constraint, dict) and "id" in constraint
    }

    relevant_task_ids: set[str] = set()
    depends_on = scan_config.get("depends_on", {})
    if isinstance(depends_on, dict):
        relevant_task_ids.update(
            task_id for task_id in depends_on.get("task_ids", []) if isinstance(task_id, str)
        )
    for binding in scan_config.get("observables", []):
        source = binding.get("source", {}) if isinstance(binding, dict) else {}
        if source.get("type") == "task":
            relevant_task_ids.add(source["task_id"])
    for constraint_id in scan_config.get("constraints_used", []):
        constraint = data["constraints_by_id"].get(constraint_id)
        if not constraint:
            continue
        computed_by = constraint.get("computed_by", {})
        if computed_by.get("type") == "task":
            relevant_task_ids.add(computed_by["task_id"])
        elif computed_by.get("type") == "derived":
            relevant_task_ids.update(computed_by.get("depends_on_tasks", []))

    result_meta_by_task: dict[str, Any | None] = {}
    result_meta_errors: dict[str, str | None] = {}
    result_meta_paths: dict[str, Path] = {}
    result_python_paths: dict[str, Path] = {}
    for task_id in sorted(relevant_task_ids):
        task_dir = project_dir / "calculations" / task_id
        result_meta_path = task_dir / "result-meta.json"
        result_meta_paths[task_id] = result_meta_path
        result_meta, error = safe_load_json(result_meta_path)
        result_meta_by_task[task_id] = result_meta
        result_meta_errors[task_id] = error
        if isinstance(result_meta, dict) and result_meta.get("python_file"):
            result_python_paths[task_id] = task_dir / result_meta["python_file"]
        else:
            result_python_paths[task_id] = task_dir / "result-python.py"

    data["relevant_task_ids"] = sorted(relevant_task_ids)
    data["result_meta_by_task"] = result_meta_by_task
    data["result_meta_errors"] = result_meta_errors
    data["result_meta_paths"] = result_meta_paths
    data["result_python_paths"] = result_python_paths
    return data


def representative_parameter_values(inputs: dict[str, Any]) -> dict[str, float]:
    """Pick a deterministic smoke-test parameter point from config + model defaults."""

    values: dict[str, float] = {}
    model_parameters = inputs["model_parameters_by_name"]
    scan_config = inputs["scan_config"] or {}

    for parameter in scan_config.get("scan_parameters", []):
        name = parameter["canonical_name"]
        start, stop = parameter["range"]
        if parameter["scale"] == "log" and start > 0 and stop > 0:
            values[name] = float(np.sqrt(start * stop))
        else:
            values[name] = float((start + stop) / 2.0)

    for parameter in scan_config.get("fixed_parameters", []):
        values[parameter["canonical_name"]] = float(parameter["value"])

    for name, parameter in model_parameters.items():
        if name in values:
            continue
        if parameter.get("role") == "fixed" and "value" in parameter:
            values[name] = float(parameter["value"])
            continue
        suggested_range = parameter.get("suggested_range")
        if isinstance(suggested_range, list) and len(suggested_range) == 2:
            start, stop = suggested_range
            if isinstance(start, (int, float)) and isinstance(stop, (int, float)):
                if start > 0 and stop > 0:
                    values[name] = float(np.sqrt(start * stop))
                else:
                    values[name] = float((start + stop) / 2.0)

    return values


def build_function_call_kwargs(
    function: Callable[..., Any],
    parameters: dict[str, float],
    *,
    allowed_parameter_names: set[str] | None = None,
    include_task_outputs: dict[str, Callable[..., Any]] | None = None,
) -> dict[str, Any]:
    """Filter parameters to what a callable can accept."""

    signature = inspect.signature(function)
    kwargs: dict[str, Any] = {}
    accepts_var_keyword = any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    )
    missing_required: list[str] = []

    for name, parameter in signature.parameters.items():
        if name == "task_outputs":
            if include_task_outputs is not None:
                kwargs[name] = include_task_outputs
            elif parameter.default is inspect.Signature.empty:
                missing_required.append(name)
            continue

        if parameter.kind not in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        ):
            continue

        if allowed_parameter_names is not None and name not in allowed_parameter_names:
            if parameter.default is inspect.Signature.empty:
                missing_required.append(name)
            continue

        if name in parameters:
            kwargs[name] = parameters[name]
        elif parameter.default is inspect.Signature.empty:
            missing_required.append(name)

    if accepts_var_keyword:
        for name, value in parameters.items():
            if allowed_parameter_names is None or name in allowed_parameter_names:
                kwargs.setdefault(name, value)

    if missing_required:
        missing = ", ".join(sorted(missing_required))
        raise TypeError(f"missing required arguments for {function.__name__}: {missing}")

    return kwargs


def read_xy_csv(path: Path, x_name: str, y_name: str) -> tuple[np.ndarray, np.ndarray]:
    """Read x/y columns from a CSV file."""

    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))

    if not rows:
        raise ValueError(f"interpolation CSV is empty: {path}")

    header_is_text = False
    try:
        [float(value) for value in rows[0][:2]]
    except ValueError:
        header_is_text = True

    data_rows = rows
    x_index = 0
    y_index = 1
    if header_is_text:
        header = rows[0]
        data_rows = rows[1:]
        if x_name in header and y_name in header:
            x_index = header.index(x_name)
            y_index = header.index(y_name)
        elif len(header) < 2:
            raise ValueError(f"interpolation CSV needs at least two columns: {path}")

    if len(data_rows) < 2:
        raise ValueError(f"interpolation CSV needs at least two data rows: {path}")

    x_values: list[float] = []
    y_values: list[float] = []
    for row in data_rows:
        if len(row) <= max(x_index, y_index):
            raise ValueError(f"row has fewer than two usable columns in {path}")
        x_values.append(float(row[x_index]))
        y_values.append(float(row[y_index]))

    pairs = sorted(zip(x_values, y_values), key=lambda item: item[0])
    xs = np.array([item[0] for item in pairs], dtype=float)
    ys = np.array([item[1] for item in pairs], dtype=float)
    return xs, ys


def compile_parameter_combination(formula: str) -> CompiledFormula:
    """Compile a pure Python-like parameter-combination expression safely."""

    expression = formula.strip()
    if not expression:
        raise ValueError("formula did not contain a usable expression")
    if "\n" in expression:
        raise ValueError("formula must be a single-line expression")
    tree = ast.parse(expression, mode="eval")
    SafeExpressionValidator().visit(tree)
    return CompiledFormula(expression=expression, tree=tree, constants={})


def compile_constraint_parameter_combination(
    formula: str,
    *,
    observable_name: str,
) -> CompiledFormula:
    """Compile a parameter-combination formula, accepting a narrow annotated form."""

    try:
        return compile_parameter_combination(formula)
    except Exception as strict_error:
        lhs, separator, rhs = formula.strip().partition("=")
        if separator != "=" or lhs.strip() != observable_name:
            raise strict_error

        expression = rhs.split(";", 1)[0].strip()
        expression = re.sub(r"\s+where\b.*$", "", expression, flags=re.IGNORECASE).strip()
        expression = re.sub(r"\s+\([A-Za-z][^()]*\)\s*$", "", expression).strip()
        expression = expression.replace("^", "**")
        try:
            return compile_parameter_combination(expression)
        except Exception:
            raise strict_error


def ensure_custom_observables_file(project_dir: Path) -> Path:
    """Create a project-level custom_observables.py skeleton if it does not exist."""

    path = project_dir / "numerics" / "custom_observables.py"
    if path.exists():
        return path

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_custom_observables_template(project_dir.name), encoding="utf-8")
    return path


def append_custom_observable_stub(
    project_dir: Path,
    observable_name: str,
    formula: str,
    parameter_names: list[str],
) -> Path:
    """Append a not-implemented custom observable stub if it is not already present."""

    path = ensure_custom_observables_file(project_dir)
    function_name = observable_name
    existing = path.read_text(encoding="utf-8")
    signature = ",\n    ".join(f"{name}: float" for name in parameter_names)
    if f"def {function_name}(" in existing:
        return path

    stub = textwrap.dedent(
        f"""


        def {function_name}(
            *,
            {signature}
        ) -> float:
            \"\"\"
            Auto-generated fallback for observable `{observable_name}`.

            Original formula:
                {formula}
            \"\"\"
            raise NotImplementedError(
                "{function_name} is not yet implemented; provide a manual custom observable"
            )
        """
    )
    path.write_text(existing.rstrip() + stub + "\n", encoding="utf-8")
    return path


def validate(inputs: dict[str, Any]) -> dict[str, Any]:
    """Run the full Step-2 compliance check suite and return a structured report."""

    try:
        from jsonschema import Draft202012Validator
    except ImportError as exc:  # pragma: no cover - environment issue
        raise RuntimeError(
            "jsonschema is required to run hep-numerics. Install the dev requirements first."
        ) from exc

    report = ValidationReport()
    runtime: dict[str, Any] = {
        "task_backends": {},
        "task_parameter_names": {},
        "task_meta_by_id": {},
        "task_python_paths": {},
        "formula_fallback_tasks": [],
        "custom_module": None,
        "custom_backends": {},
        "interpolation_tables": {},
    }

    scan_config = inputs["scan_config"] or {}
    manifest = inputs["manifest"] or {}
    model_spec = inputs["model_spec"] or {}
    constraints_by_id = inputs["constraints_by_id"]
    model_parameters = inputs["model_parameters_by_name"]
    active_model_version = manifest.get("active_model_version")
    model_checksum = manifest.get("artifacts", {}).get("model", {}).get("checksum")

    def add_check(number: int, title: str, status: str, details: list[str]) -> None:
        report.checks.append(CheckResult(number=number, title=title, status=status, details=details))

    check_1_details: list[str] = []
    schema_ok = False
    if inputs["scan_config_error"] is not None:
        check_1_details.append(inputs["scan_config_error"])
    if inputs["schema_error"] is not None:
        check_1_details.append(inputs["schema_error"])
    if not check_1_details:
        validator = Draft202012Validator(inputs["schema"])
        errors = sorted(
            validator.iter_errors(scan_config),
            key=lambda error: list(error.absolute_path),
        )
        if errors:
            for error in errors:
                check_1_details.append(format_schema_issue(error))
        else:
            schema_ok = True
            check_1_details.append("scan-config JSON schema validation passed")
    add_check(1, "scan-config schema validation", "PASS" if schema_ok else "FAIL", check_1_details)

    check_2_details: list[str] = []
    manifest_ok = False
    if inputs["manifest_error"] is not None:
        check_2_details.append(inputs["manifest_error"])
    else:
        config_depends_on = scan_config.get("depends_on", {})
        if active_model_version != config_depends_on.get("model_version"):
            check_2_details.append(
                "manifest active_model_version "
                f"{active_model_version!r} != scan-config depends_on.model_version "
                f"{config_depends_on.get('model_version')!r}"
            )
        if model_checksum != config_depends_on.get("model_checksum"):
            check_2_details.append(
                "manifest artifacts.model.checksum "
                f"{model_checksum!r} != scan-config depends_on.model_checksum "
                f"{config_depends_on.get('model_checksum')!r}"
            )
        if not check_2_details:
            manifest_ok = True
            check_2_details.append("manifest model version/checksum match the scan-config snapshot")
    add_check(2, "manifest/model snapshot consistency", "PASS" if manifest_ok else "FAIL", check_2_details)

    check_3_details: list[str] = []
    parameters_ok = False
    if inputs["model_spec_error"] is not None:
        check_3_details.append(inputs["model_spec_error"])
    else:
        scan_names = [entry["canonical_name"] for entry in scan_config.get("scan_parameters", [])]
        fixed_names = [entry["canonical_name"] for entry in scan_config.get("fixed_parameters", [])]
        canonical_parameter_names = set(model_parameters)
        duplicate_names = sorted(set(scan_names) & set(fixed_names))
        if duplicate_names:
            check_3_details.append(
                f"parameters cannot be both scan and fixed: {duplicate_names}"
            )
        for entry in scan_config.get("scan_parameters", []):
            name = entry["canonical_name"]
            parameter = model_parameters.get(name)
            if parameter is None:
                check_3_details.append(
                    f"scan parameter {name!r} is not a model-spec canonical name"
                )
                continue
            if parameter.get("role") != "scan":
                check_3_details.append(
                    f"scan parameter {name!r} has role {parameter.get('role')!r}, expected 'scan'"
                )
            if entry.get("scale") == "log" and (entry["range"][0] <= 0 or entry["range"][1] <= 0):
                check_3_details.append(
                    f"scan parameter {name!r} uses log scale but range {entry['range']} is not strictly positive"
                )
        for entry in scan_config.get("fixed_parameters", []):
            name = entry["canonical_name"]
            if name not in canonical_parameter_names:
                check_3_details.append(
                    f"fixed parameter {name!r} is not a model-spec canonical name"
                )
        for index, figure in enumerate(scan_config.get("figures", []), start=1):
            label = f"figures[{index - 1}]"
            x_name = figure.get("x")
            if x_name is not None and x_name not in canonical_parameter_names:
                check_3_details.append(
                    f"{label}.x {x_name!r} is not a model-spec canonical name"
                )
            y_name = figure.get("y")
            if y_name is not None and y_name not in canonical_parameter_names:
                check_3_details.append(
                    f"{label}.y {y_name!r} is not a model-spec canonical name"
                )
        if not check_3_details:
            parameters_ok = True
            check_3_details.append("scan/fixed parameter names are consistent with model-spec")
    add_check(3, "parameter coverage and role checks", "PASS" if parameters_ok else "FAIL", check_3_details)

    check_4_details: list[str] = []
    bindings_ok = False
    observable_bindings = scan_config.get("observables", [])
    observable_names: set[str] = set()
    custom_binding_names: list[str] = []
    task_binding_ids: list[str] = []
    depends_on_task_ids = set(scan_config.get("depends_on", {}).get("task_ids", []))
    for binding in observable_bindings:
        observable = binding.get("observable")
        source = binding.get("source", {})
        if observable in observable_names:
            check_4_details.append(f"duplicate observable binding for {observable!r}")
        else:
            observable_names.add(observable)
        if source.get("type") == "task":
            task_id = source.get("task_id")
            task_binding_ids.append(task_id)
            if task_id not in depends_on_task_ids:
                check_4_details.append(
                    f"observable {observable!r} references task {task_id!r} which is not listed in depends_on.task_ids"
                )
            if task_id not in inputs["calc_tasks_by_id"]:
                check_4_details.append(
                    f"observable {observable!r} references unknown calc-task {task_id!r}"
                )
        elif source.get("type") == "custom":
            custom_binding_names.append(source.get("function"))
        else:
            check_4_details.append(f"observable {observable!r} has unsupported source {source!r}")
    if not check_4_details:
        bindings_ok = True
        check_4_details.append("observable bindings have valid task/custom references")
    add_check(4, "observable binding integrity", "PASS" if bindings_ok else "FAIL", check_4_details)

    check_5_details: list[str] = []
    constraints_ok = False
    available_observables = set(observable_names)
    available_parameter_names = set(model_parameters)
    for constraint_id in scan_config.get("constraints_used", []):
        constraint = constraints_by_id.get(constraint_id)
        if constraint is None:
            check_5_details.append(f"constraint {constraint_id!r} is missing from constraints-data.json")
            continue
        status = constraint.get("implementation_status")
        if status not in ALLOWED_IMPLEMENTATION_STATUSES:
            check_5_details.append(
                f"constraint {constraint_id!r} has unsupported implementation_status {status!r}"
            )
        observable = constraint.get("observable")
        computed_by = constraint.get("computed_by", {})
        if (
            observable not in available_observables
            and observable not in available_parameter_names
            and computed_by.get("type") not in {"parameter_combination", "external"}
            and status != "manual_only"
        ):
            check_5_details.append(
                f"constraint {constraint_id!r} observable {observable!r} is not covered by scan observables or parameters"
            )
    if not check_5_details:
        constraints_ok = True
        check_5_details.append("constraints_used entries are present and have usable observable coverage")
    add_check(5, "constraint selection and observable coverage", "PASS" if constraints_ok else "FAIL", check_5_details)

    check_6_details: list[str] = []
    formula_fallback_details: list[str] = []
    tasks_ok = False
    result_meta_validator = None
    if inputs["result_meta_schema_error"] is None:
        result_meta_validator = Draft202012Validator(inputs["result_meta_schema"])
    else:
        check_6_details.append(inputs["result_meta_schema_error"])

    for task_id in sorted(set(task_binding_ids)):
        error = inputs["result_meta_errors"].get(task_id)
        if error is not None:
            check_6_details.append(f"{task_id}: {error}")
            continue

        result_meta = inputs["result_meta_by_task"].get(task_id)
        if result_meta_validator is not None and result_meta is not None:
            errors = sorted(
                result_meta_validator.iter_errors(result_meta),
                key=lambda issue: list(issue.absolute_path),
            )
            for issue in errors:
                path = ".".join(str(part) for part in issue.absolute_path) or "<root>"
                check_6_details.append(f"{task_id}: result-meta {path}: {issue.message}")
        if not isinstance(result_meta, dict):
            continue

        if result_meta.get("translation_status") != "complete":
            check_6_details.append(
                f"{task_id}: translation_status is {result_meta.get('translation_status')!r}, expected 'complete'"
            )
        provenance = result_meta.get("calculation_provenance")
        if provenance == "blocked":
            check_6_details.append(
                f"{task_id}: calculation_provenance is 'blocked', expected a usable backend"
            )
        if provenance in FORMULA_FALLBACK_PROVENANCES:
            fallback_entry = {
                "task_id": task_id,
                "observable": result_meta.get("observable"),
                "calculation_provenance": provenance,
                "benchmark_used_as_input": result_meta.get("benchmark_used_as_input"),
            }
            runtime["formula_fallback_tasks"].append(fallback_entry)
            fallback_detail = (
                f"{task_id}: uses formula fallback provenance {provenance!r}"
                f" for observable {result_meta.get('observable')!r}"
            )
            if scan_config.get("allow_formula_fallback") is not True:
                check_6_details.append(
                    fallback_detail + "; set scan-config allow_formula_fallback=true to opt in explicitly"
                )
            else:
                formula_fallback_details.append(fallback_detail + " (explicitly allowed)")
        if provenance == "package_x_derived":
            if result_meta.get("benchmark_used_as_input") is not False:
                check_6_details.append(
                    f"{task_id}: package_x_derived backend has benchmark_used_as_input "
                    f"{result_meta.get('benchmark_used_as_input')!r}, expected false"
                )
            if not result_meta.get("package_x_methods"):
                check_6_details.append(
                    f"{task_id}: package_x_derived backend must list package_x_methods"
                )
        if result_meta.get("depends_on", {}).get("model_version") != active_model_version:
            check_6_details.append(
                f"{task_id}: depends_on.model_version "
                f"{result_meta.get('depends_on', {}).get('model_version')!r} != {active_model_version!r}"
            )
        python_path = inputs["result_python_paths"][task_id]
        if not python_path.exists():
            check_6_details.append(f"{task_id}: missing Python implementation {python_path}")
            continue
        function_name = result_meta.get("python_function")
        if not isinstance(function_name, str):
            check_6_details.append(f"{task_id}: result-meta.python_function is missing")
            continue
        try:
            module = import_module_from_path(f"hep_numerics_task_{task_id}", python_path)
        except Exception as exc:
            check_6_details.append(f"{task_id}: failed to import {python_path}: {exc}")
            continue
        if not hasattr(module, function_name):
            check_6_details.append(
                f"{task_id}: function {function_name!r} is missing from {python_path.name}"
            )
            continue
        binding_observable = next(
            (
                binding["observable"]
                for binding in observable_bindings
                if binding.get("source", {}).get("type") == "task"
                and binding.get("source", {}).get("task_id") == task_id
            ),
            None,
        )
        if (
            isinstance(binding_observable, str)
            and isinstance(result_meta.get("observable"), str)
            and result_meta.get("observable") != binding_observable
        ):
            check_6_details.append(
                f"{task_id}: result-meta observable {result_meta.get('observable')!r} "
                f"does not match binding observable {binding_observable!r}"
            )
            continue

        runtime["task_backends"][task_id] = getattr(module, function_name)
        runtime["task_parameter_names"][task_id] = {
            parameter["canonical_name"]
            for parameter in result_meta.get("parameters", [])
            if isinstance(parameter, dict) and "canonical_name" in parameter
        }
        runtime["task_meta_by_id"][task_id] = result_meta
        runtime["task_python_paths"][task_id] = python_path

    if not task_binding_ids:
        add_check(6, "task backend readiness", "SKIP", ["no task-backed observables selected"])
    else:
        if not check_6_details:
            tasks_ok = True
            check_6_details.append("task-backed observables have importable complete Python implementations")
            check_6_details.extend(formula_fallback_details)
        add_check(6, "task backend readiness", "PASS" if tasks_ok else "FAIL", check_6_details)

    check_7_details: list[str] = []
    customs_ok = False
    custom_path = inputs["paths"]["custom_observables"]
    custom_module = None
    if not custom_binding_names:
        add_check(7, "custom observable readiness", "SKIP", ["no custom observables selected"])
    else:
        if not custom_path.exists():
            check_7_details.append(f"missing custom observables module: {custom_path}")
        else:
            try:
                custom_module = import_module_from_path("hep_numerics_custom_observables", custom_path)
                runtime["custom_module"] = custom_module
            except Exception as exc:
                check_7_details.append(f"failed to import {custom_path}: {exc}")

        if custom_module is not None:
            smoke_parameters = representative_parameter_values(inputs)
            dummy_task_outputs = {
                task_id: (lambda **_: 0.0)
                for task_id in runtime["task_backends"]
            }
            for function_name in custom_binding_names:
                if not hasattr(custom_module, function_name):
                    check_7_details.append(
                        f"custom observable function {function_name!r} is missing from {custom_path.name}"
                    )
                    continue
                function = getattr(custom_module, function_name)
                try:
                    kwargs = build_function_call_kwargs(
                        function,
                        smoke_parameters,
                        include_task_outputs=dummy_task_outputs,
                    )
                    function(**kwargs)
                except NotImplementedError as exc:
                    check_7_details.append(
                        f"custom observable {function_name!r} is not implemented: {exc}"
                    )
                    continue
                except Exception as exc:
                    check_7_details.append(
                        f"custom observable {function_name!r} failed smoke test: {exc}"
                    )
                    continue
                runtime["custom_backends"][function_name] = function

        if not check_7_details:
            customs_ok = True
            check_7_details.append(
                "custom observables imported successfully and passed the smoke test"
            )
        add_check(7, "custom observable readiness", "PASS" if customs_ok else "FAIL", check_7_details)

    check_8_details: list[str] = []
    interpolation_ok = False
    interpolated_constraints = [
        inputs["constraints_by_id"][constraint_id]
        for constraint_id in scan_config.get("constraints_used", [])
        if constraint_id in inputs["constraints_by_id"]
        and inputs["constraints_by_id"][constraint_id].get("implementation_status") == "interpolated"
    ]
    for constraint in interpolated_constraints:
        interpolation = constraint.get("interpolation")
        if not isinstance(interpolation, dict):
            check_8_details.append(
                f"{constraint['id']}: missing interpolation metadata for interpolated constraint"
            )
            continue
        method = interpolation.get("method")
        if method not in ALLOWED_INTERPOLATION_METHODS:
            check_8_details.append(
                f"{constraint['id']}: unsupported interpolation method {method!r}"
            )
        valid_range = interpolation.get("valid_range")
        if (
            not isinstance(valid_range, list)
            or len(valid_range) != 2
            or valid_range[0] >= valid_range[1]
        ):
            check_8_details.append(
                f"{constraint['id']}: invalid valid_range {valid_range!r}"
            )
        policy = interpolation.get("extrapolation_policy")
        if policy not in ALLOWED_EXTRAPOLATION_POLICIES:
            check_8_details.append(
                f"{constraint['id']}: unsupported extrapolation_policy {policy!r}"
            )
        interpolation_path = Path(interpolation["file"])
        if not interpolation_path.is_absolute():
            interpolation_path = inputs["project_dir"] / interpolation_path
        try:
            xs, ys = read_xy_csv(
                interpolation_path,
                interpolation.get("x_parameter", ""),
                interpolation.get("y_quantity", ""),
            )
        except Exception as exc:
            check_8_details.append(f"{constraint['id']}: failed to read interpolation CSV: {exc}")
            continue
        if method in {"loglog_linear", "log_x_linear"} and np.any(xs <= 0):
            check_8_details.append(
                f"{constraint['id']}: interpolation x-values must be > 0 for {method}"
            )
        if method in {"loglog_linear", "log_y_linear"} and np.any(ys <= 0):
            check_8_details.append(
                f"{constraint['id']}: interpolation y-values must be > 0 for {method}"
            )
        runtime["interpolation_tables"][constraint["id"]] = {
            "x": xs,
            "y": ys,
            "path": interpolation_path,
        }

    if not interpolated_constraints:
        add_check(8, "interpolation asset readiness", "SKIP", ["no interpolated constraints selected"])
    else:
        if not check_8_details:
            interpolation_ok = True
            check_8_details.append("interpolated constraints have readable local interpolation assets")
        add_check(
            8,
            "interpolation asset readiness",
            "PASS" if interpolation_ok else "FAIL",
            check_8_details,
        )

    return {"report": report, "runtime": runtime}


def print_compliance_report(report: ValidationReport) -> None:
    """Emit the Step-2 compliance report."""

    print("== Step 2 Compliance Report ==")
    for check in report.checks:
        print(f"[{check.status}] {check.number}. {check.title}")
        for detail in check.details:
            print(f"  - {detail}")


def build_grid(scan_parameters: list[dict[str, Any]]) -> tuple[list[np.ndarray], int]:
    """Build one axis array per scan parameter and return the total number of points."""

    axes: list[np.ndarray] = []
    total_points = 1
    for parameter in scan_parameters:
        start, stop = parameter["range"]
        grid = int(parameter["grid"])
        if parameter["scale"] == "log":
            axis = np.logspace(np.log10(start), np.log10(stop), num=grid)
        else:
            axis = np.linspace(start, stop, num=grid)
        axes.append(axis)
        total_points *= grid
    return axes, total_points


def interpolate_limit(
    constraint: dict[str, Any],
    parameters: dict[str, float],
    interpolation_tables: dict[str, dict[str, Any]],
) -> tuple[float | None, str | None]:
    """Interpolate a limit curve for one constraint."""

    interpolation = constraint["interpolation"]
    table = interpolation_tables[constraint["id"]]
    xs = table["x"]
    ys = table["y"]
    x_parameter = interpolation["x_parameter"]
    x_value = float(parameters[x_parameter])
    valid_min, valid_max = interpolation["valid_range"]
    policy = interpolation["extrapolation_policy"]

    if x_value < valid_min or x_value > valid_max:
        if policy == "forbidden":
            return None, "out of interpolation range"
        x_value = min(max(x_value, valid_min), valid_max)

    method = interpolation["method"]
    x_eval = x_value
    x_nodes = xs
    y_nodes = ys
    if method in {"loglog_linear", "log_x_linear"}:
        x_eval = math.log10(x_value)
        x_nodes = np.log10(xs)
    if method in {"loglog_linear", "log_y_linear"}:
        y_nodes = np.log10(ys)

    y_value = float(np.interp(x_eval, x_nodes, y_nodes))
    if method in {"loglog_linear", "log_y_linear"}:
        y_value = float(10**y_value)
    return y_value, None


def evaluate_constraint(
    constraint: dict[str, Any],
    prediction: float | None,
    *,
    parameters: dict[str, float] | None = None,
    interpolation_tables: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Evaluate one constraint into verdict/margin/chi2/skip_reason."""

    status = constraint.get("implementation_status")
    if status == "manual_only":
        return {
            "verdict": "skipped",
            "margin": None,
            "chi2": None,
            "skip_reason": "manual_only constraint",
        }

    if prediction is None:
        return {
            "verdict": "skipped",
            "margin": None,
            "chi2": None,
            "skip_reason": "prediction unavailable",
        }

    if status == "interpolated":
        if parameters is None or interpolation_tables is None:
            raise ValueError("interpolated evaluation requires parameters and interpolation_tables")
        limit_value, skip_reason = interpolate_limit(constraint, parameters, interpolation_tables)
        if skip_reason is not None:
            return {
                "verdict": "skipped",
                "margin": None,
                "chi2": None,
                "skip_reason": skip_reason,
            }
        working_constraint = dict(constraint)
        working_constraint["limit_value"] = limit_value
    else:
        working_constraint = constraint

    constraint_type = working_constraint["type"]
    if constraint_type == "measurement":
        central = float(working_constraint["central_value"])
        uncertainty = float(working_constraint["uncertainty"])
        sigma = float(working_constraint["sigma"])
        margin = (central - float(prediction)) / uncertainty
        chi2 = ((float(prediction) - central) / uncertainty) ** 2
        verdict = "allowed" if abs(margin) <= sigma else "excluded"
        return {"verdict": verdict, "margin": margin, "chi2": chi2, "skip_reason": None}

    if constraint_type == "upper_limit":
        limit = float(working_constraint["limit_value"])
        normalizer = abs(limit) if limit != 0 else 1.0
        margin = (limit - float(prediction)) / normalizer
        verdict = "allowed" if float(prediction) <= limit else "excluded"
        return {"verdict": verdict, "margin": margin, "chi2": None, "skip_reason": None}

    if constraint_type == "lower_limit":
        limit = float(working_constraint["limit_value"])
        normalizer = abs(limit) if limit != 0 else 1.0
        margin = (float(prediction) - limit) / normalizer
        verdict = "allowed" if float(prediction) >= limit else "excluded"
        return {"verdict": verdict, "margin": margin, "chi2": None, "skip_reason": None}

    if constraint_type == "allowed_band":
        low = float(working_constraint["limit_value_min"])
        high = float(working_constraint["limit_value_max"])
        margin = min(high - float(prediction), float(prediction) - low)
        verdict = "allowed" if low <= float(prediction) <= high else "excluded"
        return {"verdict": verdict, "margin": margin, "chi2": None, "skip_reason": None}

    if constraint_type == "ratio":
        if "limit_value_min" in working_constraint and "limit_value_max" in working_constraint:
            low = float(working_constraint["limit_value_min"])
            high = float(working_constraint["limit_value_max"])
            margin = min(high - float(prediction), float(prediction) - low)
            verdict = "allowed" if low <= float(prediction) <= high else "excluded"
            return {"verdict": verdict, "margin": margin, "chi2": None, "skip_reason": None}
        if "limit_value" in working_constraint:
            limit = float(working_constraint["limit_value"])
            normalizer = abs(limit) if limit != 0 else 1.0
            margin = (limit - float(prediction)) / normalizer
            verdict = "allowed" if float(prediction) <= limit else "excluded"
            return {"verdict": verdict, "margin": margin, "chi2": None, "skip_reason": None}
        raise ValueError("ratio constraint requires either limit_value or limit_value_min/max")

    raise ValueError(f"unsupported constraint type {constraint_type!r}")


def resolve_constraint_prediction(
    constraint: dict[str, Any],
    parameters: dict[str, float],
    observables: dict[str, float | None],
    runtime: dict[str, Any],
) -> float | None:
    """Resolve the prediction used to evaluate one constraint."""

    observable_name = constraint["observable"]
    if observable_name in observables:
        return observables[observable_name]
    if observable_name in parameters:
        return parameters[observable_name]

    computed_by = constraint.get("computed_by", {})
    computed_type = computed_by.get("type")
    if computed_type == "task":
        task_id = computed_by["task_id"]
        function = runtime["task_backends"][task_id]
        kwargs = build_function_call_kwargs(
            function,
            parameters,
            allowed_parameter_names=runtime["task_parameter_names"][task_id],
        )
        return float(function(**kwargs))
    if computed_type == "parameter_combination":
        evaluator = runtime["formula_evaluators"].get(constraint["id"])
        if evaluator is not None:
            return float(evaluator.evaluate(parameters))

        fallback = runtime.get("parameter_combination_backends", {}).get(constraint["id"])
        if fallback is not None:
            kwargs = build_function_call_kwargs(
                fallback,
                parameters,
                include_task_outputs=runtime["task_backends"],
            )
            return float(fallback(**kwargs))

        raise RuntimeError(
            f"no evaluator or custom fallback is available for parameter_combination constraint {constraint['id']}"
        )
    if computed_type == "external":
        return None
    if computed_type == "derived":
        return observables.get(observable_name)
    return None


def evaluate_point(
    parameters: dict[str, float],
    inputs: dict[str, Any],
    runtime: dict[str, Any],
) -> dict[str, Any]:
    """Evaluate all observables and constraints for one parameter point."""

    scan_config = inputs["scan_config"]
    constraints_by_id = inputs["constraints_by_id"]
    warnings: list[str] = []
    observables: dict[str, float | None] = {}

    for binding in scan_config.get("observables", []):
        observable = binding["observable"]
        source = binding["source"]
        try:
            if source["type"] == "task":
                task_id = source["task_id"]
                function = runtime["task_backends"][task_id]
                kwargs = build_function_call_kwargs(
                    function,
                    parameters,
                    allowed_parameter_names=runtime["task_parameter_names"][task_id],
                )
                observables[observable] = float(function(**kwargs))
            elif source["type"] == "custom":
                function = runtime["custom_backends"][source["function"]]
                kwargs = build_function_call_kwargs(
                    function,
                    parameters,
                    include_task_outputs=runtime["task_backends"],
                )
                observables[observable] = float(function(**kwargs))
            else:
                raise ValueError(f"unsupported observable source {source!r}")
        except Exception as exc:
            observables[observable] = None
            warnings.append(f"observable {observable}: {exc}")

    constraint_results: dict[str, dict[str, Any]] = {}
    point_failed = False
    for constraint_id in scan_config.get("constraints_used", []):
        constraint = constraints_by_id[constraint_id]
        try:
            prediction = resolve_constraint_prediction(constraint, parameters, observables, runtime)
            result = evaluate_constraint(
                constraint,
                prediction,
                parameters=parameters,
                interpolation_tables=runtime["interpolation_tables"],
            )
        except Exception as exc:
            point_failed = True
            message = f"constraint {constraint_id}: {exc}"
            warnings.append(message)
            result = {
                "verdict": "skipped",
                "margin": None,
                "chi2": None,
                "skip_reason": str(exc),
            }
        if result["verdict"] == "skipped" and result.get("skip_reason"):
            point_failed = True
        constraint_results[constraint_id] = result

    any_excluded = any(result["verdict"] == "excluded" for result in constraint_results.values())
    any_allowed = any(result["verdict"] == "allowed" for result in constraint_results.values())
    if any_excluded:
        point_status = "excluded"
    elif any_allowed:
        point_status = "allowed"
    else:
        point_status = "skipped"

    row: dict[str, Any] = {}
    for parameter in scan_config.get("scan_parameters", []):
        row[parameter["canonical_name"]] = parameters[parameter["canonical_name"]]
    for parameter in scan_config.get("fixed_parameters", []):
        row[parameter["canonical_name"]] = parameters[parameter["canonical_name"]]
    for binding in scan_config.get("observables", []):
        observable = binding["observable"]
        row[observable] = observables.get(observable)
    for constraint_id in scan_config.get("constraints_used", []):
        result = constraint_results[constraint_id]
        row[f"{constraint_id}_verdict"] = result["verdict"]
        row[f"{constraint_id}_margin"] = result["margin"]
        row[f"{constraint_id}_chi2"] = result["chi2"]
        row[f"{constraint_id}_skip_reason"] = result["skip_reason"]

    return {
        "row": row,
        "warnings": warnings,
        "point_status": point_status,
        "point_failed": point_failed,
    }


def is_missing_value(value: Any) -> bool:
    """Return whether a scan row value should be treated as missing."""

    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    try:
        return bool(np.isnan(value))
    except TypeError:
        return False


def coerce_float(value: Any) -> float | None:
    """Convert a scan row value to float, returning None for blanks/NaN."""

    if is_missing_value(value):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric):
        return None
    return numeric


def format_number(value: Any) -> str:
    """Format numbers compactly for human-readable summaries."""

    numeric = coerce_float(value)
    if numeric is None:
        return "n/a"
    if numeric == 0:
        return "0"
    magnitude = abs(numeric)
    if magnitude >= 1.0e4 or magnitude < 1.0e-3:
        return f"{numeric:.3e}"
    if numeric.is_integer() and magnitude >= 1:
        return f"{numeric:.0f}"
    return f"{numeric:.6g}"


def format_percent(count: int, total: int) -> str:
    """Render a percentage with one decimal place."""

    if total <= 0:
        return "0.0"
    return f"{(100.0 * count / total):.1f}"


def shorten_checksum(checksum: str | None) -> str:
    """Reduce a manifest checksum to a short digest for prose output."""

    if not checksum:
        return "unknown"
    digest = checksum.split(":", 1)[-1]
    return digest[:12]


def markdown_cell(value: Any) -> str:
    """Escape markdown table cell content."""

    text = "n/a" if value is None else str(value)
    return text.replace("\n", " ").replace("|", "\\|")


def collect_formula_fallbacks_from_scan_config(
    project_dir: Path,
    scan_config: dict[str, Any],
) -> list[dict[str, Any]]:
    """Collect fallback provenance for task-backed observables in a scan-config."""

    fallbacks: list[dict[str, Any]] = []
    for binding in scan_config.get("observables", []):
        source = binding.get("source", {})
        if source.get("type") != "task":
            continue
        task_id = source.get("task_id")
        if not isinstance(task_id, str):
            continue
        result_meta_path = project_dir / "calculations" / task_id / "result-meta.json"
        try:
            result_meta = load_json_file(result_meta_path)
        except Exception:
            continue
        if not isinstance(result_meta, dict):
            continue
        provenance = result_meta.get("calculation_provenance")
        if provenance not in FORMULA_FALLBACK_PROVENANCES:
            continue
        fallbacks.append(
            {
                "task_id": task_id,
                "observable": result_meta.get("observable") or binding.get("observable"),
                "calculation_provenance": provenance,
                "benchmark_used_as_input": result_meta.get("benchmark_used_as_input"),
            }
        )
    return fallbacks


def first_sentence(text: str | None, *, max_length: int = 220) -> str:
    """Return the first sentence-like fragment of a longer note."""

    if not text:
        return ""
    cleaned = " ".join(str(text).split())
    if not cleaned:
        return ""
    sentence = re.split(r"(?<=[.!?])\s+", cleaned, maxsplit=1)[0]
    if len(sentence) <= max_length:
        return sentence
    return sentence[: max_length - 3].rstrip() + "..."


def relative_to_project(path: Path, project_dir: Path) -> str:
    """Render a path relative to the project directory when possible."""

    resolved = path.resolve()
    try:
        return resolved.relative_to(project_dir.resolve()).as_posix()
    except ValueError:
        return str(resolved)


def point_status_from_row(row: dict[str, Any], constraint_ids: list[str]) -> str:
    """Reconstruct one point status from persisted verdict columns."""

    verdicts = [row.get(f"{constraint_id}_verdict") for constraint_id in constraint_ids]
    if any(verdict == "excluded" for verdict in verdicts):
        return "excluded"
    if any(verdict == "allowed" for verdict in verdicts):
        return "allowed"
    return "skipped"


def count_point_statuses(rows: list[dict[str, Any]], constraint_ids: list[str]) -> dict[str, int]:
    """Count allowed/excluded/skipped points from persisted scan rows."""

    counts = {"allowed": 0, "excluded": 0, "skipped": 0}
    for row in rows:
        counts[point_status_from_row(row, constraint_ids)] += 1
    return counts


def write_analysis_summary(
    inputs: dict[str, Any],
    rows: list[dict[str, Any]],
    counts: dict[str, int],
    csv_path: Path,
    figure_paths: list[Path],
) -> Path:
    """Write numerics/analysis-summary-{analysis_id}.md for one completed scan."""

    project_dir = inputs["project_dir"]
    scan_config = inputs["scan_config"]
    analysis_id = scan_config["analysis_id"]
    model_parameters = inputs["model_parameters_by_name"]
    constraints_by_id = inputs["constraints_by_id"]
    constraint_ids = list(scan_config.get("constraints_used", []))
    total_points = len(rows)

    meta_path = csv_path.parent / "scan.meta.json"
    meta = load_json_file(meta_path) if meta_path.exists() else {}
    environment = meta.get("environment", {})
    generated_at = meta.get("finished_at") or datetime.now(timezone.utc).replace(
        microsecond=0
    ).isoformat().replace("+00:00", "Z")
    formula_fallbacks = (
        inputs.get("formula_fallback_tasks")
        or meta.get("formula_fallbacks")
        or collect_formula_fallbacks_from_scan_config(project_dir, scan_config)
    )
    if formula_fallbacks:
        formula_fallback_lines = [
            "| task | observable | provenance | benchmark_used_as_input |",
            "| --- | --- | --- | --- |",
        ]
        for fallback in formula_fallbacks:
            formula_fallback_lines.append(
                f"| {markdown_cell(fallback.get('task_id'))} | "
                f"{markdown_cell(fallback.get('observable'))} | "
                f"{markdown_cell(fallback.get('calculation_provenance'))} | "
                f"{markdown_cell(fallback.get('benchmark_used_as_input'))} |"
            )
        formula_fallback_block = "\n".join(formula_fallback_lines)
    else:
        formula_fallback_block = "- No formula fallback backends were used."

    scanned_parameters: list[str] = []
    for parameter in scan_config.get("scan_parameters", []):
        name = parameter["canonical_name"]
        unit = model_parameters.get(name, {}).get("unit")
        unit_suffix = f" {unit}" if unit else ""
        scanned_parameters.append(
            f"{name} in [{format_number(parameter['range'][0])}, {format_number(parameter['range'][1])}]"
            f"{unit_suffix} ({int(parameter['grid'])} points, {parameter['scale']})"
        )
    scanned_parameters_text = (
        "; ".join(scanned_parameters) if scanned_parameters else "No scanned parameters were configured."
    )

    fixed_parameters: list[str] = []
    for parameter in scan_config.get("fixed_parameters", []):
        name = parameter["canonical_name"]
        unit = model_parameters.get(name, {}).get("unit")
        unit_suffix = f" {unit}" if unit else ""
        fixed_parameters.append(f"{name} = {format_number(parameter['value'])}{unit_suffix}")
    fixed_parameters_text = "; ".join(fixed_parameters) if fixed_parameters else "None."

    skip_reasons = Counter()
    constraint_skip_reasons: dict[str, Counter[str]] = {}
    for constraint_id in constraint_ids:
        counter: Counter[str] = Counter()
        column = f"{constraint_id}_skip_reason"
        for row in rows:
            reason = row.get(column)
            if is_missing_value(reason):
                continue
            counter[str(reason)] += 1
            skip_reasons[str(reason)] += 1
        if counter:
            constraint_skip_reasons[constraint_id] = counter

    if skip_reasons:
        skip_reason_lines = [
            "| skip reason | count |",
            "| --- | ---: |",
        ]
        for reason, count in skip_reasons.most_common():
            skip_reason_lines.append(f"| {markdown_cell(reason)} | {count} |")
        skip_reasons_table = "\n".join(skip_reason_lines)
    else:
        skip_reasons_table = "No per-constraint skip reasons were recorded."

    observable_lines = [
        "| observable | source type | min | max | median |",
        "| --- | --- | ---: | ---: | ---: |",
    ]
    for binding in scan_config.get("observables", []):
        observable = binding["observable"]
        values = [
            numeric
            for numeric in (coerce_float(row.get(observable)) for row in rows)
            if numeric is not None
        ]
        if values:
            minimum = format_number(min(values))
            maximum = format_number(max(values))
            median = format_number(float(np.median(values)))
        else:
            minimum = maximum = median = "n/a"
        observable_lines.append(
            f"| {markdown_cell(observable)} | {markdown_cell(binding['source']['type'])} | "
            f"{minimum} | {maximum} | {median} |"
        )
    observables_table = "\n".join(observable_lines)

    constraint_lines = [
        "| id | name | status | verdict summary |",
        "| --- | --- | --- | --- |",
    ]
    constraint_summaries: list[dict[str, Any]] = []
    for constraint_id in constraint_ids:
        verdict_counter = Counter()
        for row in rows:
            verdict = row.get(f"{constraint_id}_verdict")
            if is_missing_value(verdict):
                continue
            verdict_counter[str(verdict)] += 1

        excluded = verdict_counter.get("excluded", 0)
        allowed = verdict_counter.get("allowed", 0)
        skipped = verdict_counter.get("skipped", 0)
        summary = (
            f"excluded {excluded}/{total_points} ({format_percent(excluded, total_points)}%), "
            f"allowed {allowed}, skipped {skipped}"
        )
        constraint = constraints_by_id.get(constraint_id, {})
        constraint_lines.append(
            f"| {markdown_cell(constraint_id)} | {markdown_cell(constraint.get('name', constraint_id))} | "
            f"{markdown_cell(constraint.get('implementation_status', 'unknown'))} | "
            f"{markdown_cell(summary)} |"
        )
        constraint_summaries.append(
            {
                "id": constraint_id,
                "name": constraint.get("name", constraint_id),
                "implementation_status": constraint.get("implementation_status", "unknown"),
                "excluded": excluded,
                "allowed": allowed,
                "skipped": skipped,
            }
        )
    constraints_table = "\n".join(constraint_lines)

    if figure_paths:
        figures_block = "\n".join(
            f"- {relative_to_project(path, project_dir)}" for path in sorted(set(figure_paths))
        )
    elif scan_config.get("figures"):
        figures_block = (
            "- Figure specs are present in the scan-config, but no rendered files were present "
            "when this summary was written."
        )
    else:
        figures_block = "- No figures were configured for this analysis."

    key_findings: list[str] = []
    if total_points == 0:
        key_findings.append("No scan points were evaluated.")
    elif counts.get("allowed", 0) > 0:
        key_findings.append(
            f"{counts['allowed']} of {total_points} scanned points "
            f"({format_percent(counts['allowed'], total_points)}%) remain allowed after the implemented constraints."
        )
    elif counts.get("excluded", 0) > 0:
        key_findings.append("No scanned point remains allowed after the implemented constraints.")
    else:
        key_findings.append(
            "All scanned points ended up skipped, so the automated exclusion picture is incomplete."
        )

    strongest_constraint = max(
        (
            summary
            for summary in constraint_summaries
            if summary["implementation_status"] != "manual_only"
        ),
        key=lambda summary: summary["excluded"],
        default=None,
    )
    if strongest_constraint is not None and strongest_constraint["excluded"] > 0:
        key_findings.append(
            f"The dominant exclusion comes from {strongest_constraint['name']} "
            f"({strongest_constraint['id']}), which excludes "
            f"{format_percent(strongest_constraint['excluded'], total_points)}% of the scanned points."
        )

    allowed_rows = [
        row for row in rows if point_status_from_row(row, constraint_ids) == "allowed"
    ]
    allowed_ranges: list[str] = []
    for parameter in scan_config.get("scan_parameters", [])[:2]:
        name = parameter["canonical_name"]
        values = [
            numeric
            for numeric in (coerce_float(row.get(name)) for row in allowed_rows)
            if numeric is not None
        ]
        if not values:
            continue
        unit = model_parameters.get(name, {}).get("unit")
        unit_suffix = f" {unit}" if unit else ""
        allowed_ranges.append(
            f"{name} in [{format_number(min(values))}, {format_number(max(values))}]{unit_suffix}"
        )
    if allowed_ranges:
        key_findings.append("Allowed points appear in " + "; ".join(allowed_ranges) + ".")

    if skip_reasons:
        key_findings.append(
            "Skipped evaluations are present and should be checked against the notes below before drawing physics conclusions."
        )
    key_findings_text = " ".join(key_findings)

    external_lines: list[str] = []
    for summary in constraint_summaries:
        constraint = constraints_by_id.get(summary["id"], {})
        reasons = constraint_skip_reasons.get(summary["id"], Counter())
        tags: list[str] = []
        if summary["implementation_status"] == "manual_only":
            tags.append("manual_only")
        if constraint.get("computed_by", {}).get("type") == "external":
            tags.append("external")
        if reasons:
            tags.append(
                "skipped points: "
                + ", ".join(
                    f"{reason} ({count})" for reason, count in reasons.most_common()
                )
            )
        if not tags:
            continue
        note = first_sentence(constraint.get("notes"))
        details = "; ".join(tags + ([note] if note else []))
        external_lines.append(
            f"- {summary['id']} ({constraint.get('name', summary['id'])}): {details}"
        )
    external_constraints_block = (
        "\n".join(external_lines)
        if external_lines
        else "- No external or skipped constraints to report beyond the main table."
    )

    scan_config_path = (
        inputs.get("scan_config_path")
        or inputs.get("paths", {}).get("scan_config")
        or project_dir / "numerics" / "scan-configs" / f"{analysis_id}.json"
    )
    environment_line = (
        f"Python {environment.get('python', sys.version.split()[0])}, "
        f"numpy {environment.get('numpy', 'unavailable')}, "
        f"scipy {environment.get('scipy', 'unavailable')}, "
        f"matplotlib {environment.get('matplotlib', 'unavailable')}"
    )

    summary_path = project_dir / "numerics" / f"analysis-summary-{analysis_id}.md"
    summary_path.write_text(
        render_analysis_summary_template(
            analysis_id=analysis_id,
            description=scan_config.get("description", "No description provided"),
            generated_at=generated_at,
            model_version=scan_config["depends_on"]["model_version"],
            model_checksum=shorten_checksum(scan_config["depends_on"]["model_checksum"]),
            scanned_parameters=scanned_parameters_text,
            fixed_parameters=fixed_parameters_text,
            total_points=str(total_points),
            allowed_points=str(counts.get("allowed", 0)),
            allowed_percent=format_percent(counts.get("allowed", 0), total_points),
            excluded_points=str(counts.get("excluded", 0)),
            excluded_percent=format_percent(counts.get("excluded", 0), total_points),
            skipped_points=str(counts.get("skipped", 0)),
            skipped_percent=format_percent(counts.get("skipped", 0), total_points),
            skip_reasons_table=skip_reasons_table,
            observables_table=observables_table,
            formula_fallback_block=formula_fallback_block,
            constraints_table=constraints_table,
            figures_block=figures_block,
            key_findings=key_findings_text,
            external_constraints_block=external_constraints_block,
            scan_config_path=relative_to_project(Path(scan_config_path), project_dir),
            seed=str(scan_config.get("seed", 0)),
            scan_csv_path=relative_to_project(csv_path, project_dir),
            environment_line=environment_line,
        ),
        encoding="utf-8",
    )
    return summary_path


def write_outputs(
    inputs: dict[str, Any],
    rows: list[dict[str, Any]],
    *,
    history_action: str,
    started_at: datetime,
    finished_at: datetime,
    counts: dict[str, int],
    warnings: list[str],
) -> tuple[Path, Path]:
    """Write scan.csv and scan.meta.json to numerics/scan-results/{analysis_id}/."""

    scan_config = inputs["scan_config"]
    analysis_id = scan_config["analysis_id"]
    output_dir = inputs["project_dir"] / "numerics" / "scan-results" / analysis_id
    output_dir.mkdir(parents=True, exist_ok=True)

    columns: list[str] = []
    columns.extend(parameter["canonical_name"] for parameter in scan_config.get("scan_parameters", []))
    columns.extend(parameter["canonical_name"] for parameter in scan_config.get("fixed_parameters", []))
    columns.extend(binding["observable"] for binding in scan_config.get("observables", []))
    for constraint_id in scan_config.get("constraints_used", []):
        columns.extend(
            [
                f"{constraint_id}_verdict",
                f"{constraint_id}_margin",
                f"{constraint_id}_chi2",
                f"{constraint_id}_skip_reason",
            ]
        )

    csv_path = output_dir / "scan.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            rendered_row = {}
            for column in columns:
                value = row.get(column)
                rendered_row[column] = "" if value is None else value
            writer.writerow(rendered_row)

    environment = {}
    for package in ("numpy", "scipy", "matplotlib"):
        try:
            environment[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            environment[package] = "unavailable"

    started_at_iso = started_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    finished_at_iso = finished_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    meta = {
        "analysis_id": analysis_id,
        "history_action": history_action,
        "scan_config_snapshot": scan_config,
        "model_version": scan_config["depends_on"]["model_version"],
        "model_checksum": scan_config["depends_on"]["model_checksum"],
        "seed": scan_config.get("seed", 0),
        "started_at": started_at_iso,
        "finished_at": finished_at_iso,
        "timing_seconds": (finished_at - started_at).total_seconds(),
        "timing": {
            "started_at": started_at_iso,
            "finished_at": finished_at_iso,
            "seconds": (finished_at - started_at).total_seconds(),
        },
        "n_points": len(rows),
        "n_allowed": counts.get("allowed", 0),
        "n_excluded": counts.get("excluded", 0),
        "n_skipped": counts.get("skipped", 0),
        "environment": {
            "python": sys.version.split()[0],
            **environment,
        },
        "formula_fallbacks": inputs.get("formula_fallback_tasks", []),
        "warnings": warnings,
    }

    meta_path = output_dir / "scan.meta.json"
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return csv_path, meta_path


def prepare_runtime(inputs: dict[str, Any], runtime: dict[str, Any]) -> dict[str, Any]:
    """Prepare formula evaluators and any runtime-only helpers."""

    runtime = dict(runtime)
    runtime.setdefault("formula_evaluators", {})
    runtime.setdefault("parameter_combination_backends", {})
    scan_config = inputs["scan_config"]
    parameter_names = list(inputs["model_parameters_by_name"])
    configured_parameter_names = {
        entry["canonical_name"]
        for entry in [
            *scan_config.get("scan_parameters", []),
            *scan_config.get("fixed_parameters", []),
        ]
    }

    for constraint_id in scan_config.get("constraints_used", []):
        constraint = inputs["constraints_by_id"][constraint_id]
        computed_by = constraint.get("computed_by", {})
        if computed_by.get("type") != "parameter_combination":
            continue
        if constraint.get("observable") in configured_parameter_names:
            continue
        try:
            runtime["formula_evaluators"][constraint_id] = compile_constraint_parameter_combination(
                computed_by["formula"],
                observable_name=constraint.get("observable", ""),
            )
        except Exception as exc:
            fallback = runtime.get("custom_backends", {}).get(constraint["observable"])
            if fallback is not None:
                runtime["parameter_combination_backends"][constraint_id] = fallback
                continue
            stub_path = append_custom_observable_stub(
                inputs["project_dir"],
                constraint["observable"],
                computed_by["formula"],
                parameter_names,
            )
            raise RuntimeError(
                f"parameter_combination formula for {constraint_id} could not be parsed safely: {exc}. "
                f"A manual stub was written to {stub_path}"
            ) from exc

    return runtime


def determine_scan_history_action(project_dir: Path, analysis_id: str) -> str:
    """Classify this scan as a first complete run or a rerun."""

    scan_csv_path = project_dir / "numerics" / "scan-results" / analysis_id / "scan.csv"
    if scan_csv_path.exists():
        return "numerics_analysis_rerun"
    return "numerics_analysis_complete"


def main() -> int:
    """CLI entrypoint."""

    parser = build_parser()
    args = parser.parse_args()

    try:
        project_dir, scan_config_path, analysis_id = resolve_cli_inputs(args)
        inputs = load_inputs(
            project_dir=project_dir,
            analysis_id=analysis_id,
            scan_config_path=scan_config_path,
        )
        validation = validate(inputs)
        report = validation["report"]
        print_compliance_report(report)
        if report.has_errors:
            print("run_scan aborted: compliance checks failed; no outputs were written.")
            return 1

        inputs["formula_fallback_tasks"] = validation["runtime"].get("formula_fallback_tasks", [])
        runtime = prepare_runtime(inputs, validation["runtime"])

        scan_config = inputs["scan_config"]
        axes, total_points = build_grid(scan_config.get("scan_parameters", []))
        fixed_parameters = {
            entry["canonical_name"]: float(entry["value"])
            for entry in scan_config.get("fixed_parameters", [])
        }

        rows: list[dict[str, Any]] = []
        warnings: list[str] = [
            (
                "formula fallback enabled for "
                f"{fallback.get('task_id')} ({fallback.get('calculation_provenance')})"
            )
            for fallback in inputs.get("formula_fallback_tasks", [])
        ]
        counts = {"allowed": 0, "excluded": 0, "skipped": 0}
        failed_points = 0
        started_at = datetime.now(timezone.utc)

        for index, point in enumerate(itertools.product(*axes), start=1):
            parameters = dict(fixed_parameters)
            for parameter_spec, value in zip(scan_config.get("scan_parameters", []), point, strict=True):
                parameters[parameter_spec["canonical_name"]] = float(value)

            result = evaluate_point(parameters, inputs, runtime)
            rows.append(result["row"])
            counts[result["point_status"]] += 1
            if result["point_failed"]:
                failed_points += 1
            for warning in result["warnings"]:
                warnings.append(f"point {index}/{total_points}: {warning}")

            if index % 1000 == 0 or index == total_points:
                print(f"progress: evaluated {index}/{total_points} points")

        failure_rate = failed_points / total_points if total_points else 0.0
        if failure_rate > 0.01:
            warnings.append(
                f"warning: {failed_points} / {total_points} points had skipped evaluations "
                f"({failure_rate:.2%} > 1%)"
            )

        finished_at = datetime.now(timezone.utc)
        history_action = determine_scan_history_action(inputs["project_dir"], scan_config["analysis_id"])
        csv_path, meta_path = write_outputs(
            inputs,
            rows,
            history_action=history_action,
            started_at=started_at,
            finished_at=finished_at,
            counts=counts,
            warnings=warnings,
        )
        summary_path = write_analysis_summary(
            inputs,
            rows,
            counts,
            csv_path,
            [],
        )
        manifest_path = MANIFEST.update_manifest_for_numerics(
            project_dir=inputs["project_dir"],
            analysis_id=scan_config["analysis_id"],
            scan_config=scan_config,
            constraints_by_id=inputs["constraints_by_id"],
            scan_config_path=inputs["paths"]["scan_config"],
            scan_csv_path=csv_path,
            scan_meta_path=meta_path,
            analysis_summary_path=summary_path,
            custom_observables_path=inputs["paths"]["custom_observables"],
            figure_paths=[],
            history_action=history_action,
        )
        print("scan completed successfully")
        print(f"  - scan.csv: {csv_path}")
        print(f"  - scan.meta.json: {meta_path}")
        print(f"  - analysis-summary: {summary_path}")
        print(f"  - manifest: {manifest_path}")
        print(f"  - history-action: {history_action}")
        print(
            "  - counts: "
            f"allowed={counts['allowed']} excluded={counts['excluded']} skipped={counts['skipped']}"
        )
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
