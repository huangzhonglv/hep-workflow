#!/usr/bin/env python3
"""Validate workspace project JSON artifacts against repository schemas."""

from __future__ import annotations

import argparse
import ast
import csv
import importlib.util
import re
import stat
import sys
from pathlib import Path
from typing import Any

try:
    from _calculation_provenance import (
        derivation_artifact_errors,
        python_function_interface_errors,
    )
    from compare_to_reference import validate_target_normalization
    from _reproduction_result_validation import reproduction_result_semantic_errors
    from _strict_json import StrictJSONError, load_json
    from _identity import (
        numerics_history_analysis_id,
        resolve_contained,
        validate_analysis_id,
        validate_figure_output_keys,
        validate_repro_id,
    )
    from _dependency_graph import sha256_file, verify_dependency_graph
    from _scan_artifact_validation import (
        validate_figure_artifact_set,
        validate_scan_artifact_pair,
    )
    from _workflow_dependencies import (
        calculation_dependency_specs,
        figure_dependency_specs,
        figure_producer_from_graph,
        scan_dependency_specs,
        scan_producer_from_graph,
        verify_frozen_scan_dependency_graph,
    )
    from _publication_transaction import (
        assert_no_active_transactions,
        publication_lock,
    )
except ModuleNotFoundError:  # Imported as scripts.validate_workspace_projects in tests.
    from scripts._calculation_provenance import (
        derivation_artifact_errors,
        python_function_interface_errors,
    )
    from scripts.compare_to_reference import validate_target_normalization
    from scripts._reproduction_result_validation import reproduction_result_semantic_errors
    from scripts._strict_json import StrictJSONError, load_json
    from scripts._identity import (
        numerics_history_analysis_id,
        resolve_contained,
        validate_analysis_id,
        validate_figure_output_keys,
        validate_repro_id,
    )
    from scripts._dependency_graph import sha256_file, verify_dependency_graph
    from scripts._scan_artifact_validation import (
        validate_figure_artifact_set,
        validate_scan_artifact_pair,
    )
    from scripts._workflow_dependencies import (
        calculation_dependency_specs,
        figure_dependency_specs,
        figure_producer_from_graph,
        scan_dependency_specs,
        scan_producer_from_graph,
        verify_frozen_scan_dependency_graph,
    )
    from scripts._publication_transaction import (
        assert_no_active_transactions,
        publication_lock,
    )


ARTIFACT_SCHEMA_BY_RELATIVE_PATH = {
    "manifest.json": "manifest.schema.json",
    "model/model-spec.json": "model-spec.schema.json",
    "model/calc-tasks.json": "calc-tasks.schema.json",
    "model/benchmarks.json": "benchmarks.schema.json",
    "constraints/constraints-data.json": "constraints-data.schema.json",
    "literature/paper-meta.json": "paper-meta.schema.json",
    "literature/paper-extract.json": "paper-extract.schema.json",
    "literature/repro-targets.json": "repro-targets.schema.json",
}
RESULT_META_SCHEMA_NAME = "result-meta.schema.json"
SCAN_META_SCHEMA_NAME = "scan-meta.schema.json"
FIGURE_META_SCHEMA_NAME = "figure-meta.schema.json"
REPRODUCTION_RESULT_SCHEMA_NAME = "reproduction-result.schema.json"
TASK_DIR_PATTERN = re.compile(r"^task-[0-9]{3}$", re.ASCII)
RUN_DIR_PATTERN = re.compile(r"^run-[0-9]{3}$", re.ASCII)
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
DONE_SOURCE_OF_TRUTH_FILES = {
    "idea": ("idea/proposal.md",),
    "model": (
        "model/model-spec.json",
        "model/calc-tasks.json",
    ),
    "constraints": ("constraints/constraints-data.json",),
    "literature": (
        "literature/paper-meta.json",
        "literature/paper-extract.json",
        "literature/repro-targets.json",
    ),
}


class PythonStaticCheck:
    def __init__(self, tree: ast.Module | None, error: str | None) -> None:
        self.tree = tree
        self.error = error


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


def _duplicates(values: list[str]) -> list[str]:
    return sorted({value for value in values if values.count(value) > 1})


def _aggregate_numerics_status(analyses: list[dict[str, Any]]) -> str:
    statuses = [analysis.get("status") for analysis in analyses]
    if not statuses:
        return "not_started"
    if "failed" in statuses:
        return "failed"
    if "blocked" in statuses:
        return "blocked"
    if "stale" in statuses:
        return "stale"
    if all(status == "done" for status in statuses):
        return "done"
    return "partial"


def _manifest_numerics_statuses(loaded_artifacts: dict[str, Any]) -> dict[str, str]:
    manifest = loaded_artifacts.get("manifest.json")
    numerics = (
        manifest.get("artifacts", {}).get("numerics", {})
        if isinstance(manifest, dict)
        else {}
    )
    analyses = numerics.get("analyses", []) if isinstance(numerics, dict) else []
    return {
        str(analysis.get("analysis_id")): str(analysis.get("status"))
        for analysis in analyses
        if isinstance(analysis, dict) and isinstance(analysis.get("analysis_id"), str)
    }


def _manifest_history_identity_issues(
    manifest: dict[str, Any],
    analysis_ids: set[str],
) -> list[str]:
    """Validate semantic identities that JSON Schema cannot express."""

    history = manifest.get("history", [])
    if not isinstance(history, list):
        return []
    issues: list[str] = []
    event_ids = [
        entry["event_id"]
        for entry in history
        if isinstance(entry, dict) and isinstance(entry.get("event_id"), str)
    ]
    duplicate_event_ids = _duplicates(event_ids)
    if duplicate_event_ids:
        issues.append(
            "manifest history contains duplicate event_id values: "
            f"{duplicate_event_ids}"
        )

    for index, entry in enumerate(history):
        if not isinstance(entry, dict) or not str(entry.get("action", "")).startswith(
            "numerics_"
        ):
            continue
        try:
            linked_analysis = numerics_history_analysis_id(entry)
        except ValueError as exc:
            issues.append(f"manifest history[{index}] has invalid numerics identity: {exc}")
            continue
        if linked_analysis is None:
            issues.append(
                f"manifest history[{index}] numerics action requires an explicit "
                "analysis_id or one exact legacy analysis_id=<analysis-NNN> note token"
            )
        elif linked_analysis not in analysis_ids:
            issues.append(
                f"manifest history[{index}] references unknown numerics analysis "
                f"{linked_analysis!r}"
            )
    return issues


def _manifest_evidence_error(
    artifact_name: str,
    artifact: dict[str, Any],
    evidence_field: str,
) -> str | None:
    if artifact.get("status") != "done":
        return None
    evidence = artifact.get(evidence_field)
    if not isinstance(evidence, list) or not evidence:
        return f"artifacts.{artifact_name}.status='done' requires non-empty {evidence_field}"
    if not isinstance(artifact.get("produced_by"), str) or not artifact["produced_by"].strip():
        return f"artifacts.{artifact_name}.status='done' requires produced_by"
    if not isinstance(artifact.get("timestamp"), str) or not artifact["timestamp"].strip():
        return f"artifacts.{artifact_name}.status='done' requires timestamp"
    return None


def validate_manifest_and_global_identities(
    project_dir: Path,
    loaded_artifacts: dict[str, Any],
    *,
    scope: str = "complete",
) -> tuple[int, bool]:
    """Validate cross-artifact completion, paths, checksums, and global IDs."""

    if scope not in {"complete", "foundation"}:
        raise ValueError(f"unknown manifest semantic validation scope: {scope!r}")
    foundation_only = scope == "foundation"

    manifest = loaded_artifacts.get("manifest.json")
    if not isinstance(manifest, dict):
        print("SKIP manifest semantic state validation")
        return 0, False

    issues: list[str] = []
    artifacts = manifest.get("artifacts", {})
    if not isinstance(artifacts, dict):
        return 0, True

    history_numerics = artifacts.get("numerics")
    history_analyses = (
        history_numerics.get("analyses", [])
        if isinstance(history_numerics, dict)
        else []
    )
    history_analysis_ids = {
        str(item.get("analysis_id"))
        for item in history_analyses
        if isinstance(item, dict) and isinstance(item.get("analysis_id"), str)
    }
    issues.extend(_manifest_history_identity_issues(manifest, history_analysis_ids))

    evidence_fields = (
        ("idea", "files"),
        ("model", "files"),
        ("constraints", "files"),
        ("numerics", "files"),
        ("literature", "files"),
        ("reproduction", "runs"),
    )
    for artifact_name, evidence_field in evidence_fields:
        if foundation_only and artifact_name not in {
            "idea",
            "model",
            "constraints",
            "literature",
        }:
            continue
        artifact = artifacts.get(artifact_name)
        if not isinstance(artifact, dict):
            continue
        error = _manifest_evidence_error(artifact_name, artifact, evidence_field)
        if error:
            issues.append(error)

    for artifact_name, required_relpaths in DONE_SOURCE_OF_TRUTH_FILES.items():
        artifact = artifacts.get(artifact_name)
        if not isinstance(artifact, dict) or artifact.get("status") != "done":
            continue
        files = artifact.get("files")
        listed_files = (
            {item for item in files if isinstance(item, str)}
            if isinstance(files, list)
            else set()
        )
        for relpath in required_relpaths:
            if relpath not in listed_files:
                issues.append(
                    f"artifacts.{artifact_name}.status='done' requires {relpath!r} "
                    f"in artifacts.{artifact_name}.files"
                )
            try:
                required_path = resolve_contained(
                    project_dir,
                    relpath,
                    f"artifacts.{artifact_name} required source-of-truth path",
                )
            except ValueError as exc:
                issues.append(str(exc))
                continue
            try:
                is_nonempty_regular_file = (
                    required_path.is_file() and required_path.stat().st_size > 0
                )
            except OSError:
                is_nonempty_regular_file = False
            if not is_nonempty_regular_file:
                issues.append(
                    f"artifacts.{artifact_name}.status='done' requires non-empty "
                    f"regular file {relpath!r}"
                )

    model = artifacts.get("model")
    calculations = artifacts.get("calculations")
    if (
        not foundation_only
        and isinstance(calculations, dict)
        and calculations.get("status") == "done"
    ):
        if not calculations.get("completed_tasks"):
            issues.append(
                "artifacts.calculations.status='done' requires completed_tasks"
            )
        if not isinstance(calculations.get("produced_by"), str) or not calculations[
            "produced_by"
        ].strip():
            issues.append(
                "artifacts.calculations.status='done' requires produced_by"
            )
        if not isinstance(calculations.get("timestamp"), str) or not calculations[
            "timestamp"
        ].strip():
            issues.append(
                "artifacts.calculations.status='done' requires timestamp"
            )
        calculation_model_dependency = calculations.get("depends_on", {}).get(
            "model"
        )
        if not isinstance(model, dict) or not isinstance(
            calculation_model_dependency, dict
        ) or (
            calculation_model_dependency.get("version") != model.get("version")
            or calculation_model_dependency.get("checksum")
            != model.get("checksum")
        ):
            issues.append(
                "artifacts.calculations.status='done' requires depends_on.model "
                "to exactly match artifacts.model version and checksum"
            )

    list_fields = (
        ("idea", "files"),
        ("model", "files"),
        ("constraints", "files"),
        ("numerics", "files"),
        ("literature", "files"),
        ("calculations", "completed_tasks"),
        ("calculations", "pending_tasks"),
        ("numerics", "analyses"),
        ("reproduction", "runs"),
    )
    for artifact_name, list_field in list_fields:
        if foundation_only and artifact_name not in {
            "idea",
            "model",
            "constraints",
            "literature",
        }:
            continue
        artifact = artifacts.get(artifact_name)
        values = artifact.get(list_field) if isinstance(artifact, dict) else None
        if isinstance(values, list):
            strings = [value for value in values if isinstance(value, str)]
            duplicates = _duplicates(strings)
            if duplicates:
                issues.append(
                    f"artifacts.{artifact_name}.{list_field} contains duplicates: {duplicates}"
                )

    artifact_file_scopes = (
        ("idea", "model", "constraints", "literature")
        if foundation_only
        else ("idea", "model", "constraints", "numerics", "literature")
    )
    for artifact_name in artifact_file_scopes:
        artifact = artifacts.get(artifact_name)
        files = artifact.get("files", []) if isinstance(artifact, dict) else []
        for relpath in files if isinstance(files, list) else []:
            if not isinstance(relpath, str):
                continue
            try:
                path = resolve_contained(
                    project_dir,
                    relpath,
                    f"manifest artifacts.{artifact_name}.files path",
                )
            except ValueError as exc:
                issues.append(str(exc))
                continue
            if not path.exists() or not path.is_file():
                issues.append(
                    f"artifacts.{artifact_name}.files references missing regular file {relpath!r}"
                )

    model_spec_path = project_dir / "model" / "model-spec.json"
    if isinstance(model, dict) and model_spec_path.exists():
        try:
            actual_model_checksum = sha256_file(model_spec_path)
        except ValueError as exc:
            issues.append(f"cannot hash model/model-spec.json: {exc}")
        else:
            if model.get("checksum") != actual_model_checksum:
                issues.append(
                    "artifacts.model.checksum does not match exact model/model-spec.json bytes"
                )
            if manifest.get("active_model_version") != model.get("version"):
                issues.append(
                    "active_model_version does not match artifacts.model.version"
                )
            model_spec_payload = loaded_artifacts.get("model/model-spec.json")
            if (
                isinstance(model_spec_payload, dict)
                and model_spec_payload.get("version") != model.get("version")
            ):
                issues.append(
                    "artifacts.model.version does not match model/model-spec.json version"
                )

    constraints_artifact = artifacts.get("constraints")
    constraints_path = project_dir / "constraints" / "constraints-data.json"
    if isinstance(constraints_artifact, dict) and constraints_path.exists():
        dependency = constraints_artifact.get("depends_on", {}).get("model", {})
        if isinstance(model, dict) and (
            dependency.get("version") != model.get("version")
            or dependency.get("checksum") != model.get("checksum")
        ):
            issues.append("constraints model dependency does not match active model")
        constraints_payload = loaded_artifacts.get(
            "constraints/constraints-data.json"
        )
        declared_model_version = (
            constraints_payload.get("model_version")
            if isinstance(constraints_payload, dict)
            else None
        )
        if (
            declared_model_version is not None
            and isinstance(model, dict)
            and declared_model_version != model.get("version")
        ):
            issues.append(
                "constraints/constraints-data.json model_version does not match "
                "the active model version"
            )

    numerics_analysis_statuses: dict[str, str] = {}
    numerics = artifacts.get("numerics")
    if not foundation_only and isinstance(numerics, dict):
        analyses = numerics.get("analyses", [])
        analysis_objects = (
            [item for item in analyses if isinstance(item, dict)]
            if isinstance(analyses, list)
            else []
        )
        analysis_ids = [
            item.get("analysis_id")
            for item in analysis_objects
            if isinstance(item.get("analysis_id"), str)
        ]
        duplicate_analysis_ids = _duplicates(analysis_ids)
        if duplicate_analysis_ids:
            issues.append(
                "artifacts.numerics.analyses contains duplicate analysis_id values: "
                f"{duplicate_analysis_ids}"
            )
        if analysis_ids != sorted(analysis_ids):
            issues.append("artifacts.numerics.analyses must be sorted by analysis_id")
        numerics_analysis_statuses = {
            str(item["analysis_id"]): str(item.get("status"))
            for item in analysis_objects
            if isinstance(item.get("analysis_id"), str)
        }

        # A published scan result directory is authoritative evidence, not an
        # initialization draft. Transaction staging and ID reservations live
        # outside scan-results, so every real canonical child here must have
        # exactly one evidence-bearing manifest owner and vice versa. Scan
        # configs are deliberately not used for the reverse check because
        # init_analysis may create a config before a scan is published.
        scan_results_root = project_dir / "numerics" / "scan-results"
        physical_analysis_ids: set[str] = set()
        if scan_results_root.exists() or scan_results_root.is_symlink():
            if scan_results_root.is_symlink() or not scan_results_root.is_dir():
                issues.append(
                    "numerics/scan-results must be a real directory when present"
                )
            else:
                for child in sorted(scan_results_root.iterdir(), key=lambda path: path.name):
                    try:
                        metadata = child.lstat()
                    except OSError as exc:
                        issues.append(
                            f"cannot inspect numerics/scan-results/{child.name}: {exc}"
                        )
                        continue
                    if stat.S_ISLNK(metadata.st_mode):
                        issues.append(
                            f"numerics/scan-results/{child.name}: analysis directory "
                            "must not be a symlink"
                        )
                        continue
                    if not stat.S_ISDIR(metadata.st_mode):
                        continue
                    try:
                        validate_analysis_id(child.name)
                    except ValueError as exc:
                        issues.append(
                            f"numerics/scan-results/{child.name}: {exc}"
                        )
                        continue
                    physical_analysis_ids.add(child.name)

        evidence_analysis_ids = {
            analysis_id
            for analysis_id, status in numerics_analysis_statuses.items()
            if status in {"done", "partial", "stale"}
        }
        unowned_physical = sorted(physical_analysis_ids - evidence_analysis_ids)
        if unowned_physical:
            issues.append(
                "published scan-result directories lack evidence-bearing manifest "
                f"owners: {unowned_physical}"
            )
        missing_physical = sorted(evidence_analysis_ids - physical_analysis_ids)
        if missing_physical:
            issues.append(
                "evidence-bearing manifest analyses lack canonical scan-result "
                f"directories: {missing_physical}"
            )

        aggregate_files = numerics.get("files", [])
        expected_aggregate_files = sorted(
            {
                relpath
                for analysis in analysis_objects
                for relpath in analysis.get("files", [])
                if isinstance(relpath, str)
            }
        )
        if aggregate_files != expected_aggregate_files:
            issues.append(
                "artifacts.numerics.files must exactly equal the sorted union of "
                "artifacts.numerics.analyses[].files"
            )
        expected_status = _aggregate_numerics_status(analysis_objects)
        if numerics.get("status") != expected_status:
            issues.append(
                f"artifacts.numerics.status must be {expected_status!r} for its "
                "per-analysis states"
            )
        if analysis_objects:
            latest = max(
                analysis_objects,
                key=lambda item: (
                    str(item.get("timestamp", "")),
                    str(item.get("analysis_id", "")),
                ),
            )
            if numerics.get("produced_by") != latest.get("produced_by"):
                issues.append(
                    "artifacts.numerics.produced_by must equal the deterministic "
                    "latest analysis producer"
                )
            if numerics.get("timestamp") != latest.get("timestamp"):
                issues.append(
                    "artifacts.numerics.timestamp must equal the deterministic "
                    "latest analysis timestamp"
                )
        elif (
            numerics.get("produced_by") is not None
            or numerics.get("timestamp") is not None
        ):
            issues.append(
                "empty artifacts.numerics.analyses requires null produced_by and timestamp"
            )

        try:
            actual_constraints_checksum = sha256_file(constraints_path)
        except ValueError as exc:
            actual_constraints_checksum = None
            if analysis_objects:
                issues.append(f"cannot hash constraints-data for numerics: {exc}")

        for analysis in analysis_objects:
            analysis_id = analysis.get("analysis_id")
            if not isinstance(analysis_id, str):
                continue
            try:
                validate_analysis_id(analysis_id)
            except ValueError as exc:
                issues.append(str(exc))
                continue

            entry_files = analysis.get("files", [])
            if isinstance(entry_files, list) and entry_files != sorted(entry_files):
                issues.append(
                    f"numerics analysis {analysis_id!r} files must be sorted"
                )
            entry_file_set = {
                item
                for item in (entry_files if isinstance(entry_files, list) else [])
                if isinstance(item, str)
            }
            canonical_config = f"numerics/scan-configs/{analysis_id}.json"
            canonical_csv = f"numerics/scan-results/{analysis_id}/scan.csv"
            canonical_meta = f"numerics/scan-results/{analysis_id}/scan.meta.json"
            canonical_summary = f"numerics/analysis-summary-{analysis_id}.md"
            required_relpaths = {
                canonical_config,
                canonical_csv,
                canonical_meta,
                canonical_summary,
            }
            allowed_prefix = f"numerics/figures/{analysis_id}/"
            for relpath in entry_file_set:
                if relpath not in required_relpaths | {"numerics/custom_observables.py"} and not relpath.startswith(
                    allowed_prefix
                ):
                    issues.append(
                        f"numerics analysis {analysis_id!r} cannot own path {relpath!r}"
                    )
                try:
                    owned_path = resolve_contained(
                        project_dir,
                        relpath,
                        f"numerics analysis {analysis_id!r} file",
                    )
                except ValueError as exc:
                    issues.append(str(exc))
                    continue
                if not owned_path.is_file():
                    issues.append(
                        f"numerics analysis {analysis_id!r} references missing file {relpath!r}"
                    )

            if analysis.get("status") in {"done", "partial", "stale"}:
                missing_owned = sorted(required_relpaths - entry_file_set)
                if missing_owned:
                    issues.append(
                        f"numerics analysis {analysis_id!r} status={analysis.get('status')!r} "
                        f"requires owned files {missing_owned}"
                    )

            config_path = project_dir / canonical_config
            meta_path = project_dir / canonical_meta
            if not config_path.is_file() or not meta_path.is_file():
                continue
            try:
                scan_config = load_json(config_path)
                scan_meta = load_json(meta_path)
            except (OSError, StrictJSONError) as exc:
                issues.append(f"cannot load analysis evidence for {analysis_id!r}: {exc}")
                continue
            if not isinstance(scan_config, dict) or not isinstance(scan_meta, dict):
                issues.append(f"analysis evidence for {analysis_id!r} must be JSON objects")
                continue
            snapshot = scan_meta.get("scan_config_snapshot")
            snapshot = snapshot if isinstance(snapshot, dict) else scan_config
            snapshot_dependencies = snapshot.get("depends_on", {})
            if not isinstance(snapshot_dependencies, dict):
                snapshot_dependencies = {}
            declared_dependencies = analysis.get("depends_on", {})
            declared_model = (
                declared_dependencies.get("model", {})
                if isinstance(declared_dependencies, dict)
                else {}
            )
            declared_calculations = (
                declared_dependencies.get("calculations", {})
                if isinstance(declared_dependencies, dict)
                else {}
            )
            declared_constraints = (
                declared_dependencies.get("constraints", {})
                if isinstance(declared_dependencies, dict)
                else {}
            )
            if (
                declared_model.get("version") != snapshot_dependencies.get("model_version")
                or declared_model.get("checksum")
                != snapshot_dependencies.get("model_checksum")
            ):
                issues.append(
                    f"numerics analysis {analysis_id!r} model dependency does not "
                    "match its scan snapshot"
                )
            snapshot_tasks = snapshot_dependencies.get("task_ids", [])
            canonical_snapshot_tasks = (
                sorted(set(snapshot_tasks))
                if isinstance(snapshot_tasks, list)
                and all(isinstance(item, str) for item in snapshot_tasks)
                else None
            )
            if canonical_snapshot_tasks is None or declared_calculations.get(
                "tasks"
            ) != canonical_snapshot_tasks or (
                declared_calculations.get("model_version")
                != snapshot_dependencies.get("model_version")
            ):
                issues.append(
                    f"numerics analysis {analysis_id!r} calculation dependency does "
                    "not match its scan snapshot"
                )

            graph = scan_meta.get("input_provenance")
            graph_entries = graph.get("entries", []) if isinstance(graph, dict) else []
            recorded_constraints = [
                entry.get("sha256")
                for entry in (graph_entries if isinstance(graph_entries, list) else [])
                if isinstance(entry, dict)
                and entry.get("scope") == "project"
                and entry.get("role") == "constraints-data"
                and entry.get("path") == "constraints/constraints-data.json"
            ]
            if len(recorded_constraints) != 1 or declared_constraints.get(
                "checksum"
            ) != recorded_constraints[0]:
                issues.append(
                    f"numerics analysis {analysis_id!r} constraints dependency does "
                    "not match its recorded scan graph"
                )

            is_stale = analysis.get("status") == "stale"
            current_dependency_matches = (
                isinstance(model, dict)
                and declared_model.get("version") == model.get("version")
                and declared_model.get("checksum") == model.get("checksum")
                and actual_constraints_checksum is not None
                and declared_constraints.get("checksum") == actual_constraints_checksum
            )
            if (
                analysis.get("status") in {"done", "partial"}
                and not current_dependency_matches
            ):
                issues.append(
                    f"numerics analysis {analysis_id!r} must be marked stale because "
                    "its manifest dependencies do not match current inputs"
                )

            if analysis.get("status") == "done":
                try:
                    figure_keys = validate_figure_output_keys(scan_config)
                except ValueError as exc:
                    issues.append(
                        f"cannot verify configured figures for {analysis_id!r}: {exc}"
                    )
                    continue
                if figure_keys:
                    figure_meta_relpath = (
                        f"numerics/figures/{analysis_id}/figures.meta.json"
                    )
                    figure_meta_path = project_dir / figure_meta_relpath
                    if (
                        figure_meta_relpath not in entry_file_set
                        or not figure_meta_path.is_file()
                        or figure_meta_path.stat().st_size == 0
                    ):
                        issues.append(
                            f"done numerics analysis {analysis_id!r} requires "
                            f"owned renderer provenance {figure_meta_relpath!r}"
                        )
                for figure_key in figure_keys:
                    for suffix in ("pdf", "png"):
                        relpath = f"numerics/figures/{analysis_id}/{figure_key}.{suffix}"
                        figure_path = project_dir / relpath
                        if (
                            relpath not in entry_file_set
                            or not figure_path.is_file()
                            or figure_path.stat().st_size == 0
                        ):
                            issues.append(
                                f"done numerics analysis {analysis_id!r} requires "
                                f"configured non-empty figure {relpath!r}"
                            )

    reproduction = artifacts.get("reproduction")
    if not foundation_only and isinstance(reproduction, dict):
        reproduction_dependencies = reproduction.get("depends_on", {})
        reproduction_numerics = (
            reproduction_dependencies.get("numerics", {})
            if isinstance(reproduction_dependencies, dict)
            else {}
        )
        dependency_analysis_ids = (
            reproduction_numerics.get("analyses", [])
            if isinstance(reproduction_numerics, dict)
            else []
        )
        if isinstance(dependency_analysis_ids, list):
            for analysis_id in dependency_analysis_ids:
                if not isinstance(analysis_id, str):
                    continue
                status = numerics_analysis_statuses.get(analysis_id)
                if status is None:
                    issues.append(
                        "artifacts.reproduction.depends_on.numerics references "
                        f"unknown analysis {analysis_id!r}"
                    )
                elif status != "done":
                    issues.append(
                        "artifacts.reproduction.depends_on.numerics analysis "
                        f"{analysis_id!r} is not consumable: status={status!r}, "
                        "expected 'done'"
                    )
        runs = reproduction.get("runs", [])
        for repro_id in runs if isinstance(runs, list) else []:
            if not isinstance(repro_id, str):
                continue
            try:
                validate_repro_id(repro_id)
                run_dir = resolve_contained(
                    project_dir,
                    f"reproduction/runs/{repro_id}",
                    "manifest reproduction run",
                )
            except ValueError as exc:
                issues.append(str(exc))
                continue
            if not (run_dir / "reproduction-result.json").is_file():
                issues.append(
                    f"manifest reproduction run {repro_id!r} lacks reproduction-result.json"
                )

    model_spec = loaded_artifacts.get("model/model-spec.json")
    if isinstance(model_spec, dict):
        names = [
            str(item.get("name"))
            for collection in ("fields", "parameters")
            for item in model_spec.get(collection, [])
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        ]
        duplicates = _duplicates(names)
        if duplicates:
            issues.append(f"model field/parameter canonical namespace has duplicates: {duplicates}")

    for relpath, collection, key in (
        ("model/calc-tasks.json", "tasks", "task_id"),
        ("model/benchmarks.json", "benchmarks", "task_id"),
        ("constraints/constraints-data.json", "constraints", "id"),
        ("literature/repro-targets.json", "targets", "id"),
    ):
        payload = loaded_artifacts.get(relpath)
        if not isinstance(payload, dict):
            continue
        values = [
            str(item.get(key))
            for item in payload.get(collection, [])
            if isinstance(item, dict) and isinstance(item.get(key), str)
        ]
        duplicates = _duplicates(values)
        if duplicates:
            issues.append(f"{relpath} contains duplicate {key} values: {duplicates}")

    if issues:
        print("FAIL manifest/global semantic validation")
        for issue in issues:
            print(f"  - {issue}")
        return 1, True
    print("OK   manifest/global semantic validation")
    return 0, True


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

    artifact_issues = (
        derivation_artifact_errors(
            task_dir,
            task_dir.name,
            task,
            result_meta,
        )
        if provenance in {"package_x_derived", "manual_tree_algebra"}
        else []
    )
    issues.extend(
        f"declared derivation artifacts are unverifiable: {issue}"
        for issue in artifact_issues
    )

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
    model_parameters_by_name = (
        {
            parameter["name"]: parameter
            for parameter in model_spec.get("parameters", [])
            if isinstance(parameter, dict) and isinstance(parameter.get("name"), str)
        }
        if model_spec
        else None
    )
    allowed_parameter_names = (
        set(model_parameters_by_name) if model_parameters_by_name is not None else None
    )
    task_by_id = task_definitions_by_id(loaded_artifacts)
    manifest = loaded_artifacts.get("manifest.json")
    calculations = (
        manifest.get("artifacts", {}).get("calculations", {})
        if isinstance(manifest, dict)
        else {}
    )
    calculations_status = (
        calculations.get("status") if isinstance(calculations, dict) else None
    )
    completed_tasks = (
        {
            task_id
            for task_id in calculations.get("completed_tasks", [])
            if isinstance(task_id, str)
        }
        if isinstance(calculations, dict)
        else set()
    )

    for task_dir in task_dirs:
        task_label = f"calculations/{task_dir.name}"
        historical = (
            calculations_status == "stale" or task_dir.name not in completed_tasks
        )
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
        except StrictJSONError as exc:
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

        result_parameter_names = [
            parameter["canonical_name"]
            for parameter in result_meta.get("parameters", [])
            if isinstance(parameter, dict)
            and isinstance(parameter.get("canonical_name"), str)
        ]
        duplicate_result_parameters = _duplicates(result_parameter_names)
        if duplicate_result_parameters:
            failures += 1
            print(
                f"FAIL {task_label}/result-meta.json: duplicate canonical parameter "
                f"names {duplicate_result_parameters}"
            )
        if model_parameters_by_name is not None and not historical:
            for parameter in result_meta.get("parameters", []):
                if not isinstance(parameter, dict):
                    continue
                parameter_name = parameter.get("canonical_name")
                model_parameter = model_parameters_by_name.get(parameter_name)
                if model_parameter is None:
                    continue
                for field in ("role", "unit"):
                    if parameter.get(field) != model_parameter.get(field):
                        failures += 1
                        print(
                            f"FAIL {task_label}/result-meta.json: parameter "
                            f"{parameter_name!r} {field} {parameter.get(field)!r} "
                            "does not match model-spec "
                            f"{model_parameter.get(field)!r}"
                        )

        result_observable = result_meta.get("observable")
        return_name = result_meta.get("return_value", {}).get("name")
        if return_name != result_observable:
            failures += 1
            print(
                f"FAIL {task_label}/result-meta.json: return_value.name "
                f"{return_name!r} does not match observable {result_observable!r}"
            )
        task_definition = task_by_id.get(task_dir.name)
        if (
            not historical
            and isinstance(task_definition, dict)
            and task_definition.get("target_quantity") != result_observable
        ):
            failures += 1
            print(
                f"FAIL {task_label}/result-meta.json: observable "
                f"{result_observable!r} does not match calc-tasks target_quantity "
                f"{task_definition.get('target_quantity')!r}"
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
                else:
                    interface_issues = python_function_interface_errors(
                        static_check.tree,
                        python_function,
                        result_parameter_names,
                    )
                    if interface_issues:
                        failures += 1
                        for issue in interface_issues:
                            print(
                                f"FAIL {task_label}/{referenced_python.name}: "
                                f"{issue}"
                            )
                    else:
                        print(
                            f"OK   {task_label}/{referenced_python.name}: "
                            "python_function matches result-meta and canonical parameters"
                        )

        referenced_wl = task_dir / result_meta.get("source_wl", "")
        if result_meta.get("source_wl") and not referenced_wl.exists():
            failures += 1
            print(
                f"FAIL {task_label}/result-meta.json: referenced source_wl "
                f"{result_meta.get('source_wl')!r} does not exist"
            )

        if allowed_parameter_names is not None and not historical:
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

        try:
            dependency_specs = calculation_dependency_specs(
                project_dir,
                Path(__file__).resolve().parent.parent,
                task_dir.name,
                result_meta,
            )
        except (OSError, ValueError) as exc:
            failures += 1
            print(
                f"FAIL {task_label}/result-meta.json: cannot derive input provenance "
                f"coverage ({exc})"
            )
        else:
            dependency_issues = verify_dependency_graph(
                result_meta.get("input_provenance"),
                project_dir,
                Path(__file__).resolve().parent.parent,
                expected_specs=dependency_specs,
                check_current_bytes=not historical,
            )
            if dependency_issues:
                failures += 1
                print(f"FAIL {task_label}/result-meta.json: input provenance")
                for issue in dependency_issues:
                    print(f"  - {issue}")
            else:
                suffix = (
                    " (historical graph; current-byte equality intentionally skipped)"
                    if historical
                    else ""
                )
                print(
                    f"OK   {task_label}/result-meta.json input provenance{suffix}"
                )

        provenance_issues = validate_result_provenance(
            task_dir,
            task_label,
            None if historical else task_by_id.get(task_dir.name),
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
    calculations_status = calculations.get("status")
    historical = calculations_status == "stale"

    overlap = sorted(set(pending_tasks) & set(completed_tasks))
    if overlap:
        failures += 1
        print(
            "FAIL manifest.json: calculations.pending_tasks overlaps "
            f"calculations.completed_tasks: {overlap}"
        )

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
    if calc_tasks and not historical:
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

    active_model_version = manifest.get("active_model_version")
    active_model_artifact = manifest.get("artifacts", {}).get("model", {})
    active_model_checksum = (
        active_model_artifact.get("checksum")
        if isinstance(active_model_artifact, dict)
        else None
    )
    calculation_model_dependency = calculations.get("depends_on", {}).get("model", {})
    expected_model_version = (
        calculation_model_dependency.get("version")
        if historical and isinstance(calculation_model_dependency, dict)
        else active_model_version
    )
    expected_model_checksum = (
        calculation_model_dependency.get("checksum")
        if historical and isinstance(calculation_model_dependency, dict)
        else active_model_checksum
    )
    if expected_model_version is not None:
        for task_id in completed_tasks:
            result_meta_path = project_dir / "calculations" / task_id / "result-meta.json"
            if not result_meta_path.exists() or result_meta_path.stat().st_size == 0:
                continue
            try:
                result_meta = load_json(result_meta_path)
            except StrictJSONError:
                continue
            result_model_version = result_meta.get("depends_on", {}).get("model_version")
            if result_model_version != expected_model_version:
                failures += 1
                relpath = result_meta_path.relative_to(project_dir).as_posix()
                expected_label = (
                    "preserved stale calculations dependency"
                    if historical
                    else "manifest active_model_version"
                )
                print(
                    f"FAIL {relpath}: depends_on.model_version "
                    f"{result_model_version!r} does not match {expected_label} "
                    f"{expected_model_version!r} "
                    "(stale calculation)"
                )
            result_model_checksum = result_meta.get("depends_on", {}).get(
                "model_checksum"
            )
            if result_model_checksum != expected_model_checksum:
                failures += 1
                relpath = result_meta_path.relative_to(project_dir).as_posix()
                expected_label = (
                    "preserved stale calculations dependency"
                    if historical
                    else "manifest model checksum"
                )
                print(
                    f"FAIL {relpath}: depends_on.model_checksum "
                    f"{result_model_checksum!r} does not match {expected_label} "
                    f"{expected_model_checksum!r} (stale calculation)"
                )

    if failures == 0:
        suffix = " (stale historical evidence)" if historical else ""
        print(f"OK   manifest.json calculations artifact{suffix}")

    return failures, True


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

    physical_run_ids = [run_dir.name for run_dir in run_dirs]
    for repro_id in physical_run_ids:
        listed_count = manifest_runs.count(repro_id)
        if listed_count != 1:
            failures += 1
            print(
                f"FAIL reproduction/runs/{repro_id}: immutable run directory must "
                "appear exactly once in artifacts.reproduction.runs; "
                f"found {listed_count} entries"
            )

    completion_events = [
        entry
        for entry in (manifest.get("history", []) if isinstance(manifest, dict) else [])
        if isinstance(entry, dict)
        and entry.get("action") == "reproduction_run_complete"
    ]
    completion_event_ids = [
        entry["event_id"]
        for entry in completion_events
        if isinstance(entry.get("event_id"), str)
    ]
    duplicate_completion_event_ids = _duplicates(completion_event_ids)
    if duplicate_completion_event_ids:
        failures += 1
        print(
            "FAIL manifest.json: reproduction completion events contain duplicate "
            f"event_id values {duplicate_completion_event_ids}"
        )
    for repro_id in manifest_runs:
        matches = [entry for entry in completion_events if entry.get("repro_id") == repro_id]
        if len(matches) != 1:
            failures += 1
            print(
                f"FAIL manifest.json: listed reproduction run {repro_id!r} requires "
                "exactly one matching reproduction_run_complete event; "
                f"found {len(matches)}"
            )
        elif not isinstance(matches[0].get("event_id"), str):
            failures += 1
            print(
                f"FAIL manifest.json: reproduction completion event for {repro_id!r} "
                "requires event_id"
            )
    extra_completion_runs = sorted(
        {
            str(entry.get("repro_id"))
            for entry in completion_events
            if entry.get("repro_id") not in manifest_runs
        }
    )
    if extra_completion_runs:
        failures += 1
        print(
            "FAIL manifest.json: reproduction completion events reference unlisted "
            f"runs {extra_completion_runs}"
        )

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
        if run_dir.is_symlink():
            failures += 1
            print(f"FAIL {run_label}: reproduction run directory must not be a symlink")
            continue
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
        except StrictJSONError as exc:
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

        semantic_errors = reproduction_result_semantic_errors(
            result,
            project_dir=project_dir,
            expected_run_dir=run_dir,
        )
        if semantic_errors:
            failures += 1
            print(f"FAIL {relpath}: semantic reproduction-result validation")
            for error in semantic_errors:
                print(f"  - {error}")
        else:
            print(f"OK   {relpath}: semantic reproduction-result validation")

    return failures, validated_any


def validate_reproduction_reference_evidence(
    project_dir: Path,
    loaded_artifacts: dict[str, Any],
) -> tuple[int, bool]:
    payload = loaded_artifacts.get("literature/repro-targets.json")
    if not isinstance(payload, dict):
        print("SKIP literature/digitized normalization evidence")
        return 0, False
    failures = 0
    for target in payload.get("targets", []):
        if not isinstance(target, dict):
            continue
        try:
            validate_target_normalization(
                project_dir,
                target,
                paper_id=str(payload.get("paper_id", "")),
            )
        except ValueError as exc:
            failures += 1
            print(
                f"FAIL literature reference evidence {target.get('id')!r}: {exc}"
            )
        else:
            print(f"OK   literature reference evidence {target.get('id')!r}")
    return failures, True


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
        if analysis_dir.is_symlink():
            failures += 1
            print(
                f"FAIL numerics/scan-results/{analysis_id}: analysis directory must not be a symlink"
            )
            continue
        try:
            validate_analysis_id(analysis_id)
        except ValueError as exc:
            failures += 1
            print(f"FAIL numerics/scan-results/{analysis_id}: {exc}")
            continue
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
    analysis_statuses: dict[str, str],
    *,
    manifest_override: dict[str, Any] | None = None,
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
        is_stale = analysis_statuses.get(scan_config_path.stem) == "stale"
        try:
            result = validate_scan_config_module.validate_scan_config(
                scan_config_path=scan_config_path,
                project_dir=None if is_stale else project_dir,
                manifest_override=None if is_stale else manifest_override,
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
                suffix = " (stale snapshot; current-input checks skipped)" if is_stale else ""
                print(f"OK   {relpath} <- validate_scan_config.py{suffix}")

    return failures, True


def count_csv_data_rows(scan_csv_path: Path) -> int:
    with scan_csv_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        try:
            next(reader)
        except StopIteration as exc:
            raise ValueError("scan.csv is empty") from exc
        return sum(1 for _ in reader)


def scan_calculation_dependencies_are_current(
    project_dir: Path,
    scan_config: dict[str, Any],
) -> bool:
    depends_on = scan_config.get("depends_on")
    task_ids = depends_on.get("task_ids", []) if isinstance(depends_on, dict) else []
    if not isinstance(task_ids, list):
        return False
    repo_root = Path(__file__).resolve().parent.parent
    for task_id in task_ids:
        if not isinstance(task_id, str):
            return False
        metadata_path = (
            project_dir / "calculations" / task_id / "result-meta.json"
        )
        try:
            metadata = load_json(metadata_path)
            expected_specs = calculation_dependency_specs(
                project_dir,
                repo_root,
                task_id,
                metadata,
            )
        except (OSError, StrictJSONError, ValueError):
            return False
        if verify_dependency_graph(
            metadata.get("input_provenance") if isinstance(metadata, dict) else None,
            project_dir,
            repo_root,
            expected_specs=expected_specs,
        ):
            return False
    return True


def validate_scan_meta_outputs(
    project_dir: Path,
    validators: dict[str, Any],
    analysis_statuses: dict[str, str],
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
        is_stale = analysis_statuses.get(analysis_id) == "stale"
        if analysis_dir.is_symlink():
            failures += 1
            print(
                f"FAIL numerics/scan-results/{analysis_id}: analysis directory must not be a symlink"
            )
            continue
        try:
            validate_analysis_id(analysis_id)
        except ValueError as exc:
            failures += 1
            print(f"FAIL numerics/scan-results/{analysis_id}: {exc}")
            continue
        meta_path = analysis_dir / "scan.meta.json"
        relpath = meta_path.relative_to(project_dir).as_posix()

        if not meta_path.exists():
            failures += 1
            print(f"FAIL {relpath}: missing scan.meta.json for {analysis_id}")
            continue

        try:
            scan_meta = load_json(meta_path)
        except StrictJSONError as exc:
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
        pair_issues = validate_scan_artifact_pair(
            project_dir,
            analysis_id,
            (
                None
                if is_stale
                else project_dir
                / "numerics"
                / "scan-configs"
                / f"{analysis_id}.json"
            ),
            Path(__file__).resolve().parent.parent,
            historical_scan_config_snapshot=(
                scan_config_snapshot if is_stale else None
            ),
        )
        if pair_issues:
            failures += 1
            if "history_action" not in scan_meta:
                print(
                    f"FAIL {relpath}: derived analysis metadata cannot serve as a "
                    "completed run-scan artifact"
                )
            else:
                print(f"FAIL numerics scan artifact pair {analysis_id}")
            for issue in pair_issues:
                print(f"  - {issue}")
            continue
        if is_stale:
            print(
                f"OK   {relpath}: stale evidence passed intrinsic pair validation; "
                "only current-byte dependency matching is skipped"
            )

        if not isinstance(scan_config_snapshot, dict):
            failures += 1
            print(
                f"FAIL {relpath}: scan_config_snapshot must be an object after "
                "strict artifact-pair validation"
            )
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

        if is_stale:
            recorded_csv_checksum = scan_meta.get("scan_csv_sha256")
            try:
                actual_csv_checksum = sha256_file(scan_csv_path)
            except ValueError as exc:
                failures += 1
                print(f"FAIL {scan_csv_relpath}: cannot hash stale scan table ({exc})")
            else:
                if recorded_csv_checksum != actual_csv_checksum:
                    failures += 1
                    print(
                        f"FAIL {scan_csv_relpath}: stale scan table checksum does not "
                        "match scan.meta.json"
                    )
                else:
                    print(f"OK   {scan_csv_relpath}: stale table checksum matches metadata")

        try:
            scan_config_path = (
                project_dir / "numerics" / "scan-configs" / f"{analysis_id}.json"
            )
            live_scan_config = load_json(scan_config_path)
            dependency_config = scan_config_snapshot
            producer_script = scan_producer_from_graph(
                scan_meta.get("input_provenance", {}),
                Path(__file__).resolve().parent.parent,
            )
            expected_dependencies = scan_dependency_specs(
                project_dir,
                Path(__file__).resolve().parent.parent,
                scan_config_path,
                dependency_config,
                producer_script=producer_script,
            )
        except (OSError, StrictJSONError, ValueError) as exc:
            failures += 1
            print(f"FAIL {relpath}: cannot derive scan input provenance ({exc})")
            continue
        if is_stale:
            provenance_issues = verify_dependency_graph(
                scan_meta.get("input_provenance"),
                project_dir,
                Path(__file__).resolve().parent.parent,
                expected_specs=expected_dependencies,
                check_current_bytes=False,
            )
        else:
            provenance_issues = verify_frozen_scan_dependency_graph(
                scan_meta.get("input_provenance"),
                project_dir,
                Path(__file__).resolve().parent.parent,
                expected_dependencies,
                scan_config_source=scan_meta.get("scan_config_source", ""),
            )
        if provenance_issues:
            failures += 1
            print(f"FAIL {relpath}: scan input provenance")
            for issue in provenance_issues:
                print(f"  - {issue}")
        else:
            suffix = (
                " (historical graph structure/coverage verified; current-byte "
                "matching skipped)"
                if is_stale
                else ""
            )
            print(f"OK   {relpath}: strict artifact pair and input provenance{suffix}")
            if is_stale:
                current_provenance_issues = verify_dependency_graph(
                    scan_meta.get("input_provenance"),
                    project_dir,
                    Path(__file__).resolve().parent.parent,
                    expected_specs=expected_dependencies,
                )
                if (
                    not current_provenance_issues
                    and scan_calculation_dependencies_are_current(
                        project_dir,
                        dependency_config,
                    )
                ):
                    failures += 1
                    print(
                        f"FAIL {relpath}: analysis is marked stale but its complete "
                        "input provenance, including transitive calculations, still "
                        "matches current exact bytes"
                    )

        figure_dir = project_dir / "numerics" / "figures" / analysis_id
        figure_meta_path = figure_dir / "figures.meta.json"
        figure_specs = live_scan_config.get("figures", [])
        figure_meta_required = (
            analysis_statuses.get(analysis_id) == "done" and bool(figure_specs)
        )
        if figure_meta_required and not figure_meta_path.is_file():
            failures += 1
            print(
                f"FAIL numerics/figures/{analysis_id}/figures.meta.json: "
                "done analysis with configured figures requires renderer provenance"
            )
        if figure_meta_path.is_file():
            try:
                figure_meta = load_json(figure_meta_path)
            except (OSError, StrictJSONError) as exc:
                failures += 1
                print(f"FAIL {figure_meta_path}: invalid JSON ({exc})")
                continue
            figure_schema_errors = validate_json_data(
                figure_meta,
                validators[FIGURE_META_SCHEMA_NAME],
            )
            if figure_schema_errors:
                failures += 1
                print(f"FAIL {figure_meta_path} <- {FIGURE_META_SCHEMA_NAME}")
                for issue in figure_schema_errors:
                    print(f"  - {issue}")
                continue
            figure_issues = validate_figure_artifact_set(
                project_dir,
                analysis_id,
                live_scan_config,
                scan_meta,
                figure_meta,
                require_live_render_match=not is_stale,
            )
            if figure_issues:
                failures += 1
                print(f"FAIL figure artifact generation {analysis_id}")
                for issue in figure_issues:
                    print(f"  - {issue}")
                continue
            try:
                renderer = figure_producer_from_graph(
                    figure_meta.get("input_provenance", {}),
                    Path(__file__).resolve().parent.parent,
                )
                expected_figure_dependencies = figure_dependency_specs(
                    project_dir,
                    Path(__file__).resolve().parent.parent,
                    scan_config_path=scan_config_path,
                    scan_csv_path=scan_csv_path,
                    scan_meta_path=meta_path,
                    renderer_script=renderer,
                )
            except (OSError, ValueError) as exc:
                failures += 1
                print(f"FAIL {figure_meta_path}: cannot derive figure provenance ({exc})")
                continue
            figure_provenance_issues = verify_dependency_graph(
                figure_meta.get("input_provenance"),
                project_dir,
                Path(__file__).resolve().parent.parent,
                expected_specs=expected_figure_dependencies,
                check_current_bytes=not is_stale,
            )
            if figure_provenance_issues:
                failures += 1
                print(f"FAIL {figure_meta_path}: figure input provenance")
                for issue in figure_provenance_issues:
                    print(f"  - {issue}")
            else:
                print(f"OK   {figure_meta_path}: renderer provenance and outputs")

    return failures, True


def load_schema_validators(repo_root: Path) -> dict[str, Any]:
    """Load every schema validator used by one workspace snapshot."""

    from jsonschema import Draft202012Validator

    validators: dict[str, Any] = {}
    schemas_dir = repo_root / "schemas"
    for schema_name in set(ARTIFACT_SCHEMA_BY_RELATIVE_PATH.values()) | {
        RESULT_META_SCHEMA_NAME,
        SCAN_META_SCHEMA_NAME,
        FIGURE_META_SCHEMA_NAME,
        REPRODUCTION_RESULT_SCHEMA_NAME,
    }:
        schema = load_json(schemas_dir / schema_name)
        validators[schema_name] = Draft202012Validator(schema)
    return validators


def validate_project_snapshot(
    project_dir: Path,
    validators: dict[str, Any],
    validate_scan_config_module: Any,
    *,
    manifest_override: dict[str, Any] | None = None,
) -> int:
    """Validate one coherent project snapshot, optionally with a staged manifest.

    The override lets transactional migration validate the complete candidate
    state before replacing the live manifest. Callers own snapshot locking.
    """

    failures = 0
    print(f"[{project_dir.name}]")
    validated_any = False
    loaded_artifacts: dict[str, Any] = {}
    for relpath, schema_name in ARTIFACT_SCHEMA_BY_RELATIVE_PATH.items():
        artifact_path = project_dir / relpath
        if relpath == "manifest.json" and manifest_override is not None:
            data = manifest_override
        elif not artifact_path.exists():
            print(f"SKIP {relpath}")
            continue
        else:
            try:
                data = load_json(artifact_path)
            except StrictJSONError as exc:
                failures += 1
                print(f"FAIL {relpath}: invalid JSON ({exc})")
                continue

        validated_any = True
        loaded_artifacts[relpath] = data
        errors = validate_json_data(data, validators[schema_name])
        if errors:
            failures += 1
            print(f"FAIL {relpath} <- {schema_name}")
            for error in errors:
                print(f"  - {error}")
        else:
            print(f"OK   {relpath} <- {schema_name}")

    semantic_failures, semantic_validated = validate_manifest_and_global_identities(
        project_dir,
        loaded_artifacts,
    )
    failures += semantic_failures
    validated_any = validated_any or semantic_validated

    artifact_failures, artifact_validated = validate_calculations_artifact(
        project_dir, loaded_artifacts
    )
    failures += artifact_failures
    validated_any = validated_any or artifact_validated

    literature_failures, literature_validated = validate_literature_manifest_files(
        project_dir, loaded_artifacts
    )
    failures += literature_failures
    validated_any = validated_any or literature_validated

    reference_failures, reference_validated = validate_reproduction_reference_evidence(
        project_dir, loaded_artifacts
    )
    failures += reference_failures
    validated_any = validated_any or reference_validated

    calculation_failures, calculations_validated = validate_calculation_outputs(
        project_dir, validators, loaded_artifacts
    )
    failures += calculation_failures
    validated_any = validated_any or calculations_validated

    analysis_statuses = _manifest_numerics_statuses(loaded_artifacts)
    scan_config_failures, scan_configs_validated = validate_scan_configs(
        project_dir,
        validate_scan_config_module,
        analysis_statuses,
        manifest_override=manifest_override,
    )
    failures += scan_config_failures
    validated_any = validated_any or scan_configs_validated

    scan_meta_failures, scan_meta_validated = validate_scan_meta_outputs(
        project_dir, validators, analysis_statuses
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
    return failures


def main() -> int:
    try:
        import jsonschema  # noqa: F401
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
    validate_scan_config_module = load_validate_scan_config_module(repo_root)

    if not workspace_root.exists():
        print(f"error: workspace root not found: {workspace_root}", file=sys.stderr)
        return 1

    try:
        validators = load_schema_validators(repo_root)
    except (OSError, StrictJSONError) as exc:
        print(f"error: cannot load workspace schemas: {exc}", file=sys.stderr)
        return 1

    project_dirs = iter_project_dirs(workspace_root, args.projects)
    if not project_dirs:
        print(f"error: no project directories found under {workspace_root}", file=sys.stderr)
        return 1

    failures = 0
    held_publication_locks = []
    blocked_projects: set[Path] = set()
    for project_dir in project_dirs:
        if not project_dir.exists():
            continue
        lock = publication_lock(
            project_dir,
            "workspace-validation",
        )
        try:
            lock.acquire()
            assert_no_active_transactions(project_dir)
        except Exception as exc:
            lock.release()
            failures += 1
            blocked_projects.add(project_dir)
            print(
                f"FAIL {project_dir.name}: cannot acquire a coherent project "
                f"snapshot ({exc})"
            )
        else:
            held_publication_locks.append(lock)

    for project_dir in project_dirs:
        if not project_dir.exists():
            failures += 1
            print(f"FAIL {project_dir.name}: project directory not found")
            continue
        if project_dir in blocked_projects:
            continue

        failures += validate_project_snapshot(
            project_dir,
            validators,
            validate_scan_config_module,
        )

    for lock in reversed(held_publication_locks):
        lock.release()
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
