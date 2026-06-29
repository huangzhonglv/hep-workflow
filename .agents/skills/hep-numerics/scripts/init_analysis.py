#!/usr/bin/env python3
"""Generate an initial hep-numerics scan-config draft for a project."""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any


ANALYSIS_ID_PATTERN = re.compile(r"^analysis-(\d{3})$")
DRAFT_DESCRIPTION_PREFIX = "Draft scan-config for "


def load_run_scan_module() -> Any:
    """Load the sibling run_scan implementation so helpers stay aligned."""

    script_path = Path(__file__).resolve()
    target = script_path.parent / "run_scan.py"
    spec = importlib.util.spec_from_file_location("hep_numerics_init_run_scan_helpers", target)
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
            "Generate a draft scan-config JSON and custom observable skeleton "
            "for a workspace project."
        )
    )
    parser.add_argument(
        "--project-dir",
        type=Path,
        required=True,
        help="Path to the workspace project directory.",
    )
    parser.add_argument(
        "--analysis-id",
        help="Optional explicit analysis identifier, for example analysis-001.",
    )
    return parser


def load_json(path: Path) -> Any:
    """Load JSON from disk."""

    return json.loads(path.read_text(encoding="utf-8"))


def sanitize_identifier(name: str) -> str:
    """Convert an observable name into a Python identifier."""

    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", name)
    if not cleaned:
        cleaned = "observable"
    if cleaned[0].isdigit():
        cleaned = f"observable_{cleaned}"
    return cleaned


def formula_fallback_entry_for_task(project_dir: Path, task_id: str) -> dict[str, Any] | None:
    """Return fallback provenance metadata for a task, if it uses formula fallback."""

    result_meta_path = project_dir / "calculations" / task_id / "result-meta.json"
    try:
        result_meta = load_json(result_meta_path)
    except Exception:
        return None
    if not isinstance(result_meta, dict):
        return None
    provenance = result_meta.get("calculation_provenance")
    if provenance not in RUN_SCAN.FORMULA_FALLBACK_PROVENANCES:
        return None
    return {
        "task_id": task_id,
        "observable": result_meta.get("observable"),
        "calculation_provenance": provenance,
        "benchmark_used_as_input": result_meta.get("benchmark_used_as_input"),
    }


def choose_scale(parameter: dict[str, Any]) -> str:
    """Infer a plotting/scan scale from the suggested range."""

    suggested_range = parameter.get("suggested_range")
    if not isinstance(suggested_range, list) or len(suggested_range) != 2:
        return "linear"
    start, stop = suggested_range
    if (
        isinstance(start, (int, float))
        and isinstance(stop, (int, float))
        and start > 0
        and stop > 0
        and stop / start >= 100
    ):
        return "log"
    return "linear"


def default_parameter_value(parameter: dict[str, Any]) -> float:
    """Choose a default numeric value for a non-scanned parameter."""

    if "value" in parameter and isinstance(parameter["value"], (int, float)):
        return float(parameter["value"])

    suggested_range = parameter.get("suggested_range")
    if not isinstance(suggested_range, list) or len(suggested_range) != 2:
        return 0.0

    start, stop = suggested_range
    if not isinstance(start, (int, float)) or not isinstance(stop, (int, float)):
        return 0.0
    if start <= 0 <= stop:
        return 0.0
    if start > 0 and stop > 0 and stop / start >= 100:
        return float((start * stop) ** 0.5)
    return float((start + stop) / 2.0)


def next_analysis_id(scan_configs_dir: Path) -> str:
    """Return the next free analysis-NNN identifier."""

    used_numbers: set[int] = set()
    if scan_configs_dir.exists():
        for path in scan_configs_dir.glob("analysis-*.json"):
            match = ANALYSIS_ID_PATTERN.fullmatch(path.stem)
            if match:
                used_numbers.add(int(match.group(1)))

    number = 1
    while number in used_numbers:
        number += 1
    return f"analysis-{number:03d}"


def iter_analysis_config_paths(scan_configs_dir: Path) -> list[Path]:
    """Return all analysis-NNN scan-config paths sorted by descending numeric suffix."""

    candidates: list[tuple[int, Path]] = []
    if not scan_configs_dir.exists():
        return []
    for path in scan_configs_dir.glob("analysis-*.json"):
        match = ANALYSIS_ID_PATTERN.fullmatch(path.stem)
        if match:
            candidates.append((int(match.group(1)), path))
    return [path for _, path in sorted(candidates, key=lambda item: item[0], reverse=True)]


def has_execution_outputs(project_dir: Path, analysis_id: str) -> bool:
    """Return whether an analysis appears to have been executed already."""

    scan_results_dir = project_dir / "numerics" / "scan-results" / analysis_id
    if scan_results_dir.exists():
        return True
    figures_dir = project_dir / "numerics" / "figures" / analysis_id
    if figures_dir.exists():
        return True
    return False


def is_reusable_draft_scan_config(project_dir: Path, path: Path) -> bool:
    """Return whether a scan-config is an auto-generated draft that was never executed."""

    try:
        scan_config = load_json(path)
    except Exception:
        return False

    analysis_id = scan_config.get("analysis_id")
    description = scan_config.get("description")
    if not isinstance(analysis_id, str) or analysis_id != path.stem:
        return False
    if not isinstance(description, str) or not description.startswith(DRAFT_DESCRIPTION_PREFIX):
        return False
    return not has_execution_outputs(project_dir, analysis_id)


def find_reusable_draft_analysis_id(project_dir: Path, scan_configs_dir: Path) -> str | None:
    """Find the newest unexecuted auto-generated draft scan-config, if any."""

    for path in iter_analysis_config_paths(scan_configs_dir):
        if is_reusable_draft_scan_config(project_dir, path):
            return path.stem
    return None


def resolve_target_analysis(
    project_dir: Path,
    *,
    requested_analysis_id: str | None,
) -> tuple[str, Path, bool]:
    """Resolve the target analysis ID/path and whether an existing draft will be overwritten."""

    scan_configs_dir = project_dir / "numerics" / "scan-configs"
    if requested_analysis_id is not None:
        analysis_id = requested_analysis_id
        target_path = scan_configs_dir / f"{analysis_id}.json"
        if target_path.exists() and not is_reusable_draft_scan_config(project_dir, target_path):
            raise FileExistsError(
                f"scan-config already exists for {analysis_id}: {target_path}"
            )
        return analysis_id, target_path, target_path.exists()

    reusable_analysis_id = find_reusable_draft_analysis_id(project_dir, scan_configs_dir)
    if reusable_analysis_id is not None:
        target_path = scan_configs_dir / f"{reusable_analysis_id}.json"
        return reusable_analysis_id, target_path, True

    analysis_id = next_analysis_id(scan_configs_dir)
    return analysis_id, scan_configs_dir / f"{analysis_id}.json", False


def ensure_custom_observables_header(project_dir: Path) -> tuple[Path, bool]:
    """Create the project-level custom_observables.py header if missing."""

    path = project_dir / "numerics" / "custom_observables.py"
    if path.exists():
        return path, False

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        RUN_SCAN.render_custom_observables_template(project_dir.name),
        encoding="utf-8",
    )
    return path, True


def append_custom_stub(
    path: Path,
    *,
    function_name: str,
    parameter_names: list[str],
    constraint: dict[str, Any],
    needs_task_outputs: bool,
) -> bool:
    """Append one custom observable stub if it is not already present."""

    existing = path.read_text(encoding="utf-8")
    if f"def {function_name}(" in existing:
        return False

    computed_by = constraint.get("computed_by", {})
    lines = ["", "", f"def {function_name}(", "    *,"]
    if needs_task_outputs:
        lines.append("    task_outputs: dict[str, Callable[..., float]],")
    for name in parameter_names:
        lines.append(f"    {name}: float,")
    lines.extend(
        [
            ") -> float:",
            '    """',
            f"    Auto-generated observable stub for constraint {constraint['id']} ({constraint['name']}).",
            "",
        ]
    )

    if computed_by.get("type") == "derived":
        lines.extend(
            [
                "    Derivation note:",
                f"        {computed_by.get('derivation_note', '').strip()}",
            ]
        )
    else:
        lines.extend(
            [
                "    Original formula that could not be parsed safely:",
                f"        {computed_by.get('formula', '').strip()}",
            ]
        )

    lines.extend(
        [
            '    """',
            "    raise NotImplementedError(",
            f'        "{function_name} is not yet implemented; see constraint {constraint["id"]}"',
            "    )",
            "",
        ]
    )
    path.write_text(existing.rstrip() + "\n" + "\n".join(lines), encoding="utf-8")
    return True


def build_draft_config(project_dir: Path, analysis_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build the scan-config draft and any custom-observable side effects."""

    manifest = load_json(project_dir / "manifest.json")
    model_spec = load_json(project_dir / "model" / "model-spec.json")
    constraints_data = load_json(project_dir / "constraints" / "constraints-data.json")

    model_parameters = model_spec.get("parameters", [])
    scan_candidates = [parameter for parameter in model_parameters if parameter.get("role") == "scan"]
    if not scan_candidates:
        raise ValueError("model-spec.json does not define any role='scan' parameters")

    selected_scan_parameters = scan_candidates[:2]
    selected_scan_names = [parameter["name"] for parameter in selected_scan_parameters]

    scan_parameters = []
    for parameter in selected_scan_parameters:
        suggested_range = parameter.get("suggested_range")
        if not isinstance(suggested_range, list) or len(suggested_range) != 2:
            raise ValueError(
                f"scan parameter {parameter['name']!r} is missing a usable suggested_range"
            )
        scan_parameters.append(
            {
                "canonical_name": parameter["name"],
                "range": [float(suggested_range[0]), float(suggested_range[1])],
                "grid": 60,
                "scale": choose_scale(parameter),
            }
        )

    fixed_parameters = []
    for parameter in model_parameters:
        name = parameter["name"]
        if name in selected_scan_names:
            continue
        if parameter.get("role") == "derived":
            continue
        fixed_parameters.append(
            {
                "canonical_name": name,
                "value": default_parameter_value(parameter),
            }
        )

    selected_constraints = [
        constraint
        for constraint in constraints_data.get("constraints", [])
        if constraint.get("implementation_status") in {"direct", "interpolated"}
    ]

    observables: list[dict[str, Any]] = []
    depends_on_tasks: set[str] = set()
    custom_observable_specs: list[dict[str, Any]] = []
    formula_parse_failures: list[str] = []

    for constraint in selected_constraints:
        computed_by = constraint.get("computed_by", {})
        observable = constraint.get("observable")
        if computed_by.get("type") == "task":
            task_id = computed_by["task_id"]
            depends_on_tasks.add(task_id)
            if observable not in {entry["observable"] for entry in observables}:
                observables.append(
                    {
                        "observable": observable,
                        "source": {
                            "type": "task",
                            "task_id": task_id,
                        },
                    }
                )
        elif computed_by.get("type") == "derived":
            function_name = sanitize_identifier(observable)
            custom_observable_specs.append(
                {
                    "constraint": constraint,
                    "function_name": function_name,
                    "needs_task_outputs": True,
                }
            )
            depends_on_tasks.update(computed_by.get("depends_on_tasks", []))
            observables.append(
                {
                    "observable": observable,
                    "source": {
                        "type": "custom",
                        "function": function_name,
                        "note": computed_by.get("derivation_note", ""),
                    },
                }
            )
        elif computed_by.get("type") == "parameter_combination":
            try:
                RUN_SCAN.compile_parameter_combination(computed_by["formula"])
            except Exception:
                function_name = sanitize_identifier(observable)
                formula_parse_failures.append(constraint["id"])
                custom_observable_specs.append(
                    {
                        "constraint": constraint,
                        "function_name": function_name,
                        "needs_task_outputs": False,
                    }
                )
                observables.append(
                    {
                        "observable": observable,
                        "source": {
                            "type": "custom",
                            "function": function_name,
                            "note": "Generated fallback because the formula needs manual implementation.",
                        },
                    }
                )

    if not observables and selected_constraints:
        fallback_constraint = selected_constraints[0]
        function_name = sanitize_identifier(fallback_constraint["observable"])
        custom_observable_specs.append(
            {
                "constraint": fallback_constraint,
                "function_name": function_name,
                "needs_task_outputs": False,
            }
        )
        observables.append(
            {
                "observable": fallback_constraint["observable"],
                "source": {
                    "type": "custom",
                    "function": function_name,
                    "note": "Generated fallback because scan-config.observables requires at least one entry.",
                },
            }
        )

    figures: list[dict[str, Any]] = []
    if len(scan_parameters) >= 2 and selected_constraints:
        figures.append(
            {
                "kind": "exclusion_2d",
                "x": scan_parameters[0]["canonical_name"],
                "y": scan_parameters[1]["canonical_name"],
                "constraints": [constraint["id"] for constraint in selected_constraints],
                "show_allowed_region": True,
                "title": f"{model_spec['model_name']} exclusion overview",
            }
        )
    if scan_parameters:
        x_name = scan_parameters[0]["canonical_name"]
        for observable in observables:
            figures.append(
                {
                    "kind": "scan_1d",
                    "x": x_name,
                    "observables": [observable["observable"]],
                    "overlay_constraint_bands": True,
                    "title": f"{observable['observable']} vs {x_name}",
                }
            )

    formula_fallback_tasks = [
        entry
        for task_id in sorted(depends_on_tasks)
        for entry in [formula_fallback_entry_for_task(project_dir, task_id)]
        if entry is not None
    ]

    scan_config = {
        "analysis_id": analysis_id,
        "model_name": model_spec["model_name"],
        "description": f"Draft scan-config for {model_spec['model_name']}",
        "depends_on": {
            "model_version": manifest.get("active_model_version"),
            "model_checksum": manifest.get("artifacts", {}).get("model", {}).get("checksum"),
            "task_ids": sorted(depends_on_tasks),
        },
        "scan_parameters": scan_parameters,
        "fixed_parameters": fixed_parameters,
        "observables": observables,
        "constraints_used": [constraint["id"] for constraint in selected_constraints],
        "figures": figures,
        "allow_formula_fallback": bool(formula_fallback_tasks),
        "seed": 0,
        "parallelism": 1,
    }

    return scan_config, {
        "custom_observable_specs": custom_observable_specs,
        "formula_parse_failures": formula_parse_failures,
        "formula_fallback_tasks": formula_fallback_tasks,
        "model_parameter_names": [parameter["name"] for parameter in model_parameters],
    }


def main() -> int:
    """CLI entrypoint."""

    parser = build_parser()
    args = parser.parse_args()

    try:
        project_dir = args.project_dir.resolve()
        if not (project_dir / "manifest.json").exists():
            raise FileNotFoundError(f"manifest.json not found under {project_dir}")

        scan_configs_dir = project_dir / "numerics" / "scan-configs"
        scan_configs_dir.mkdir(parents=True, exist_ok=True)
        analysis_id, target_path, reusing_existing_draft = resolve_target_analysis(
            project_dir,
            requested_analysis_id=args.analysis_id,
        )

        scan_config, metadata = build_draft_config(project_dir, analysis_id)
        custom_path, created_custom_header = ensure_custom_observables_header(project_dir)
        appended_functions: list[str] = []
        for spec in metadata["custom_observable_specs"]:
            appended = append_custom_stub(
                custom_path,
                function_name=spec["function_name"],
                parameter_names=metadata["model_parameter_names"],
                constraint=spec["constraint"],
                needs_task_outputs=spec["needs_task_outputs"],
            )
            if appended:
                appended_functions.append(spec["function_name"])

        target_path.write_text(json.dumps(scan_config, indent=2) + "\n", encoding="utf-8")

        if reusing_existing_draft:
            print(f"Reused existing unexecuted draft scan-config: {target_path}")
        else:
            print(f"Wrote draft scan-config: {target_path}")
        if created_custom_header:
            print(f"Created custom observable skeleton: {custom_path}")
        if appended_functions:
            print(
                "Appended custom observable stubs: "
                + ", ".join(appended_functions)
            )
        if metadata["formula_parse_failures"]:
            print(
                "Parameter-combination constraints needing manual implementation: "
                + ", ".join(metadata["formula_parse_failures"])
            )
        if metadata["formula_fallback_tasks"]:
            tasks = ", ".join(
                f"{entry['task_id']} ({entry['calculation_provenance']})"
                for entry in metadata["formula_fallback_tasks"]
            )
            print(
                "Formula fallback backends detected; draft sets "
                f"allow_formula_fallback=true for review: {tasks}"
            )

        print("")
        print("Next steps:")
        print(f"1. Review and edit {target_path.name} to confirm scan ranges, fixed values, and figures.")
        print(
            f"2. Fill in {custom_path.name} for any generated custom observables before running numerics."
        )
        print(
            "3. Once the required calculation tasks have complete result-meta/result-python outputs, run:"
        )
        print(
            f"   python3 .agents/skills/hep-numerics/scripts/validate_scan_config.py "
            f"--project-dir {project_dir} --analysis-id {analysis_id}"
        )
        print(
            f"   python3 .agents/skills/hep-numerics/scripts/run_scan.py "
            f"--project-dir {project_dir} --analysis-id {analysis_id}"
        )
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
