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
- Do not update `manifest.json` or use manifest content for workflow decisions.
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
import hashlib
import json
import sys
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
    SeriesComparison,
    benchmark_point_metrics,
    exclusion_region_metrics,
    figure_curve_metrics,
    load_csv,
    scan_table_metrics,
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
        help="Comma-separated target ids blocked by the orchestrator before numerics.",
    )
    return parser.parse_args(argv)


def parse_blocked_targets(raw: str) -> set[str]:
    return {item.strip() for item in raw.split(",") if item.strip()}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


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


def validate_inputs(args: argparse.Namespace) -> Inputs:
    project_dir = Path(args.project_dir)
    if not project_dir.exists() or not project_dir.is_dir():
        raise ValueError(f"project directory does not exist: {project_dir}")

    manifest_path = project_dir / "manifest.json"
    if not manifest_path.exists():
        raise ValueError(f"project directory is missing manifest.json: {project_dir}")

    run_dir = project_dir / "reproduction" / "runs" / args.repro_id
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

    calc_tasks_path = project_dir / "model" / "calc-tasks.json"
    if not calc_tasks_path.exists():
        raise ValueError(f"missing model/calc-tasks.json: {calc_tasks_path}")
    calc_tasks = load_json(calc_tasks_path)

    blocked_targets = parse_blocked_targets(args.blocked_targets)
    selected_targets = select_targets(repro_targets, args.target_id)
    non_blocked_targets = [
        target for target in selected_targets if str(target.get("id")) not in blocked_targets
    ]

    scan_csv = project_dir / "numerics" / "scan-results" / args.analysis_id / "scan.csv"
    if non_blocked_targets and not scan_csv.exists():
        raise ValueError(f"missing scan.csv for analysis {args.analysis_id}: {scan_csv}")

    return Inputs(
        project_dir=project_dir,
        analysis_id=args.analysis_id,
        repro_id=args.repro_id,
        run_dir=run_dir,
        repro_targets=repro_targets,
        calc_tasks=calc_tasks,
        scan_csv=scan_csv,
        repro_targets_path=repro_targets_path,
        blocked_targets=blocked_targets,
    )


def task_catalog(calc_tasks: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(task.get("task_id")): task
        for task in calc_tasks.get("tasks", [])
        if task.get("task_id")
    }


def result_meta_paths(project_dir: Path) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    for path in sorted((project_dir / "calculations").glob("task-*/result-meta.json")):
        paths[path.parent.name] = path
    return paths


def resolve_tasks_for_target(
    target: dict[str, Any],
    calc_task_by_id: dict[str, dict[str, Any]],
    meta_paths: dict[str, Path],
) -> list[str]:
    observables = {str(item) for item in target.get("observables", [])}
    task_ids: set[str] = set()

    for task_id, task in calc_task_by_id.items():
        if task_id in observables or str(task.get("target_quantity")) in observables:
            task_ids.add(task_id)

    for task_id, path in meta_paths.items():
        try:
            meta = load_json(path)
        except (OSError, json.JSONDecodeError):
            task_ids.add(task_id)
            continue
        if task_id in observables or str(meta.get("observable")) in observables:
            task_ids.add(task_id)

    return sorted(task_ids)


def score_task(
    task_id: str,
    task: dict[str, Any] | None,
    meta_path: Path | None,
) -> tuple[str, dict[str, str] | None, dict[str, Any] | None]:
    if meta_path is None or not meta_path.exists():
        return "unknown", {
            "task_id": task_id,
            "state": "unknown",
            "reason": "result_meta_missing",
        }, None

    try:
        meta = load_json(meta_path)
    except (OSError, json.JSONDecodeError):
        return "unknown", {
            "task_id": task_id,
            "state": "unknown",
            "reason": "result_meta_missing",
        }, None

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
    if provenance == "package_x_derived" and benchmark_used is False:
        return "package_x_independent", None, meta
    if provenance == "manual_tree_algebra" and benchmark_used is False:
        if task_type == "tree" and loop_order == 0:
            return "manual", {
                "task_id": task_id,
                "state": "manual",
                "reason": "manual_tree_algebra_on_tree_task",
            }, meta
        if task_type == "loop" or (isinstance(loop_order, int) and loop_order >= 1):
            return "unknown", {
                "task_id": task_id,
                "state": "unknown",
                "reason": "unsupported_manual_loop",
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
                return float(metrics[key])
    if tolerance_kind == "absolute":
        for key in ("max_absolute_error", "absolute_error", "max_hausdorff_distance"):
            if key in metrics:
                return float(metrics[key])
    return None


def compute_verdict(
    *,
    blocked: bool,
    target_kind: str,
    tolerance: dict[str, Any],
    metrics: dict[str, Any],
    ceiling: str,
) -> str:
    if blocked:
        return "blocked"
    if target_kind == "formula":
        return "needs_human_review"
    if tolerance.get("kind") == "qualitative":
        return "needs_human_review"

    metric_value = metric_value_for_tolerance(metrics, str(tolerance.get("kind")))
    if metric_value is None or tolerance.get("value") is None:
        return "blocked"
    if metric_value > float(tolerance["value"]):
        return "fail"
    if ceiling == "pass":
        return "pass"
    return "needs_human_review"


def read_digitized(project_dir: Path, target: dict[str, Any]) -> pd.DataFrame | None:
    data_file = str(target.get("data_file", ""))
    if not data_file:
        return None
    path = project_dir / data_file
    if not path.exists() or not path.is_file():
        return None
    return load_csv(path)


def compute_metrics(
    scan_df: pd.DataFrame,
    digitized_df: pd.DataFrame | None,
    target: dict[str, Any],
) -> tuple[dict[str, Any], SeriesComparison | None, list[str], bool]:
    if target["kind"] == "formula":
        return {}, None, ["formula_target_requires_human_review"], False
    if digitized_df is None:
        return {}, None, [f"missing_digitized_data_file: {target.get('data_file')}"], True

    try:
        if target["kind"] == "benchmark_point":
            metrics, comparison = benchmark_point_metrics(scan_df, digitized_df, target)
        elif target["kind"] == "figure_curve":
            metrics, comparison = figure_curve_metrics(scan_df, digitized_df, target)
        elif target["kind"] == "scan_table":
            metrics, comparison = scan_table_metrics(scan_df, digitized_df, target)
        elif target["kind"] == "exclusion_region":
            metrics, comparison = exclusion_region_metrics(scan_df, digitized_df, target)
        else:
            return {}, None, [f"unsupported_target_kind: {target['kind']}"], True
    except (ValueError, KeyError, TypeError) as exc:
        return {}, None, [f"metric_computation_blocked: {exc}"], True

    return dict(sorted(metrics.items())), comparison, [], False


def build_target_result(
    *,
    inputs: Inputs,
    target: dict[str, Any],
    scan_df: pd.DataFrame | None,
    calc_task_by_id: dict[str, dict[str, Any]],
    meta_paths: dict[str, Path],
) -> dict[str, Any]:
    target_id = str(target["id"])
    task_ids = resolve_tasks_for_target(target, calc_task_by_id, meta_paths)
    task_states: list[str] = []
    issues: list[dict[str, str]] = []
    scored_meta: list[dict[str, Any]] = []
    for task_id in task_ids:
        state, issue, meta = score_task(task_id, calc_task_by_id.get(task_id), meta_paths.get(task_id))
        task_states.append(state)
        if issue is not None:
            issues.append(issue)
        if meta is not None:
            scored_meta.append(meta)

    independence = aggregate_target_independence(task_states)
    ceiling = verdict_ceiling(independence)
    generated = relative_generated_files(inputs.repro_id, target_id)
    warnings: list[str] = []
    metrics: dict[str, Any] = {}
    comparison_data: SeriesComparison | None = None
    blocked = False
    digitized_df = read_digitized(inputs.project_dir, target)

    if target_id in inputs.blocked_targets:
        blocked = True
        ceiling = "needs_human_review"
        warnings.append("blocked_by_orchestrator: missing scan_config_hints, no scan attempted")
        render_blocked_overlay(
            project_dir=inputs.project_dir,
            generated_files=generated,
            target=target,
            digitized_df=digitized_df,
        )
    else:
        if scan_df is None:
            blocked = True
            warnings.append("metric_computation_blocked: missing scan.csv")
        else:
            metrics, comparison_data, metric_warnings, blocked = compute_metrics(scan_df, digitized_df, target)
            warnings.extend(metric_warnings)
        if not blocked:
            render_all_figures(
                project_dir=inputs.project_dir,
                generated_files=generated,
                target=target,
                comparison=comparison_data,
            )
        else:
            render_blocked_overlay(
                project_dir=inputs.project_dir,
                generated_files=generated,
                target=target,
                digitized_df=digitized_df,
            )

    if not task_ids:
        warnings.append("no_tasks_matched_target_observables")

    verdict = compute_verdict(
        blocked=blocked,
        target_kind=str(target["kind"]),
        tolerance=target["tolerance"],
        metrics=metrics,
        ceiling=ceiling,
    )
    return {
        "target_id": target_id,
        "tasks_used": task_ids,
        "derivation_independence": independence,
        "provenance_issues": issues,
        "comparison": {
            "kind": target["kind"],
            "interpolation_method": "linear",
            "metrics": metrics,
        },
        "tolerance": target["tolerance"],
        "verdict": verdict,
        "verdict_ceiling": ceiling,
        "generated_files": generated,
        "warnings": sorted(set(warnings)),
        "notes": "",
    }


def digitized_checksums(project_dir: Path, targets: list[dict[str, Any]]) -> dict[str, str]:
    checksums: dict[str, str] = {}
    for target in targets:
        data_file = str(target.get("data_file", ""))
        if not data_file:
            continue
        path = project_dir / data_file
        if path.exists() and path.is_file():
            checksums[data_file] = sha256_file(path)
    return dict(sorted(checksums.items()))


def model_dependency(calc_tasks: dict[str, Any], meta_paths: dict[str, Path]) -> dict[str, Any]:
    checksum: str | None = None
    version = calc_tasks.get("model_version")
    for path in (meta_paths[key] for key in sorted(meta_paths)):
        try:
            depends_on = load_json(path).get("depends_on", {})
        except (OSError, json.JSONDecodeError):
            continue
        if checksum is None:
            checksum = depends_on.get("model_checksum")
        if version is None:
            version = depends_on.get("model_version")
    return {"version": version, "checksum": checksum}


def build_depends_on(
    inputs: Inputs,
    targets: list[dict[str, Any]],
    task_ids: list[str],
    meta_paths: dict[str, Path],
) -> dict[str, Any]:
    model = model_dependency(inputs.calc_tasks, meta_paths)
    scan_meta = inputs.scan_csv.parent / "scan.meta.json"
    return {
        "model": model,
        "calculations": {
            "tasks": sorted(set(task_ids)),
            "model_version": model["version"],
        },
        "numerics": {
            "analysis_id": inputs.analysis_id,
            "scan_meta_checksum": sha256_file(scan_meta) if scan_meta.exists() else None,
        },
        "literature": {
            "repro_targets_checksum": sha256_file(inputs.repro_targets_path),
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


def build_run_result(inputs: Inputs, targets: list[dict[str, Any]]) -> dict[str, Any]:
    apply_style(inputs.project_dir)
    calc_task_by_id = task_catalog(inputs.calc_tasks)
    meta_paths = result_meta_paths(inputs.project_dir)
    scan_df = load_csv(inputs.scan_csv) if inputs.scan_csv.exists() else None

    results: list[dict[str, Any]] = []
    all_task_ids: list[str] = []
    for target in targets:
        result = build_target_result(
            inputs=inputs,
            target=target,
            scan_df=scan_df,
            calc_task_by_id=calc_task_by_id,
            meta_paths=meta_paths,
        )
        all_task_ids.extend(result["tasks_used"])
        results.append(result)

    payload: dict[str, Any] = {
        "repro_id": inputs.repro_id,
        "paper_id": inputs.repro_targets["paper_id"],
        "started_at": utc_now(),
        "finished_at": utc_now(),
        "depends_on": build_depends_on(inputs, targets, all_task_ids, meta_paths),
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
    diagnostic_file = write_diagnostic(inputs.run_dir, results)
    if diagnostic_file is not None:
        payload["diagnostic_file"] = diagnostic_file
    return payload


def validate_output(payload: dict[str, Any]) -> None:
    errors = schema_errors("reproduction-result.schema.json", payload)
    if errors:
        details = "\n  - ".join(errors)
        raise ValueError(f"generated reproduction-result.json failed schema validation:\n  - {details}")


def run(argv: list[str] | None = None) -> int:
    np.random.seed(0)
    try:
        args = parse_args(argv)
        inputs = validate_inputs(args)
        targets = select_targets(inputs.repro_targets, args.target_id)
        inputs.run_dir.mkdir(parents=True)
        (inputs.project_dir / "reproduction" / "figures" / inputs.repro_id).mkdir(parents=True, exist_ok=True)
        payload = build_run_result(inputs, targets)
        validate_output(payload)
        write_json(inputs.run_dir / "reproduction-result.json", payload)
    except ValueError as exc:
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
