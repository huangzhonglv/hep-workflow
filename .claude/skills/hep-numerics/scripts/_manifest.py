#!/usr/bin/env python3
"""Shared manifest update helpers for hep-numerics scripts."""

from __future__ import annotations

import hashlib
import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from _strict_json import load_json as strict_load_json
from _identity import (
    numerics_history_analysis_id,
    validate_analysis_id,
    validate_figure_output_keys,
)
from _dependency_graph import verify_dependency_graph
from _scan_artifact_validation import validate_scan_artifact_pair
from _workflow_dependencies import scan_dependency_specs, scan_producer_from_graph


def load_json(path: Path) -> Any:
    """Load JSON from disk."""

    return strict_load_json(path)


def file_sha256(path: Path) -> str:
    """Compute a file checksum in the manifest-compatible format."""

    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return f"sha256:{digest}"


def relative_to_project(path: Path, project_dir: Path) -> str:
    """Render a path relative to the project root."""

    return path.resolve().relative_to(project_dir.resolve()).as_posix()


def dedupe_preserve_order(items: Iterable[str]) -> list[str]:
    """Deduplicate a sequence while preserving the first occurrence order."""

    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def canonical_strings(items: Iterable[str]) -> list[str]:
    """Return a deterministic sorted set of strings."""

    return sorted(set(items))


def aggregate_numerics_status(analyses: Iterable[dict[str, Any]]) -> str:
    """Reduce per-analysis states without hiding an incomplete or stale entry."""

    statuses = [str(analysis.get("status")) for analysis in analyses]
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


def scan_config_uses_custom_observables(scan_config: dict[str, Any]) -> bool:
    """Return whether the scan graph is expected to consume the custom module."""

    return any(
        isinstance(binding, dict)
        and isinstance(binding.get("source"), dict)
        and binding["source"].get("type") == "custom"
        for binding in scan_config.get("observables", [])
    )


def determine_numerics_status(
    constraints_by_id: dict[str, Any],
    scan_config: dict[str, Any],
    *,
    figure_paths: Iterable[Path] = (),
) -> str:
    """Compute the numerics artifact status from the active constraint selection."""

    direct_or_interpolated_constraints = {
        constraint_id
        for constraint_id, constraint in constraints_by_id.items()
        if constraint.get("implementation_status") in {"direct", "interpolated"}
    }
    used_constraints = set(scan_config.get("constraints_used", []))
    constraints_complete = bool(used_constraints) and used_constraints.issubset(
        direct_or_interpolated_constraints
    )
    expected_figure_names = {
        f"{key}.{suffix}"
        for key in validate_figure_output_keys(scan_config)
        for suffix in ("pdf", "png")
    }
    figure_paths = tuple(figure_paths)
    existing_figure_names = {
        path.name
        for path in figure_paths
        if path.exists() and path.is_file() and path.stat().st_size > 0
    }
    figures_complete = existing_figure_names == expected_figure_names
    return "done" if constraints_complete and figures_complete else "partial"


def build_numerics_files(
    *,
    project_dir: Path,
    scan_config_path: Path,
    scan_csv_path: Path,
    scan_meta_path: Path,
    analysis_summary_path: Path | None = None,
    custom_observables_path: Path | None = None,
    figure_paths: Iterable[Path] = (),
    figure_meta_path: Path | None = None,
    allow_unpublished_files: bool = False,
) -> list[str]:
    """Build the manifest file list for the current numerics analysis."""

    files = [
        relative_to_project(scan_config_path, project_dir),
        relative_to_project(scan_csv_path, project_dir),
        relative_to_project(scan_meta_path, project_dir),
    ]

    if analysis_summary_path is not None and (
        allow_unpublished_files or analysis_summary_path.exists()
    ):
        files.append(relative_to_project(analysis_summary_path, project_dir))
    if (
        custom_observables_path is not None
        and custom_observables_path.exists()
        and scan_config_uses_custom_observables(load_json(scan_config_path))
    ):
        files.append(relative_to_project(custom_observables_path, project_dir))

    files.extend(
        relative_to_project(path, project_dir)
        for path in sorted(set(path.resolve() for path in figure_paths))
        if allow_unpublished_files or path.exists()
    )
    if figure_meta_path is not None and (
        allow_unpublished_files or figure_meta_path.exists()
    ):
        files.append(relative_to_project(figure_meta_path, project_dir))
    return canonical_strings(files)


def build_numerics_dependencies(
    *,
    project_dir: Path,
    scan_config: dict[str, Any],
) -> dict[str, Any]:
    """Build the analysis-scoped dependency declaration from current inputs."""

    depends_on = scan_config["depends_on"]
    return {
        "model": {
            "version": depends_on["model_version"],
            "checksum": depends_on["model_checksum"],
        },
        "calculations": {
            "tasks": canonical_strings(depends_on.get("task_ids", [])),
            "model_version": depends_on["model_version"],
        },
        "constraints": {
            "checksum": file_sha256(project_dir / "constraints" / "constraints-data.json"),
        },
    }


def _analysis_by_id(numerics: dict[str, Any], analysis_id: str) -> dict[str, Any] | None:
    analyses = numerics.get("analyses", [])
    if not isinstance(analyses, list):
        raise ValueError("manifest artifacts.numerics.analyses must be an array")
    matches = [
        analysis
        for analysis in analyses
        if isinstance(analysis, dict) and analysis.get("analysis_id") == analysis_id
    ]
    if len(matches) > 1:
        raise ValueError(f"manifest contains duplicate numerics analysis_id {analysis_id!r}")
    return deepcopy(matches[0]) if matches else None


def _mark_stale_against_active_inputs(
    analyses: Iterable[dict[str, Any]],
    *,
    active_model: dict[str, Any],
    constraints_checksum: str,
) -> list[dict[str, Any]]:
    """Mark retained current-looking entries stale when their declared inputs drift."""

    updated: list[dict[str, Any]] = []
    for original in analyses:
        analysis = deepcopy(original)
        if analysis.get("status") in {"done", "partial"}:
            dependencies = analysis.get("depends_on", {})
            model = dependencies.get("model", {}) if isinstance(dependencies, dict) else {}
            constraints = (
                dependencies.get("constraints", {}) if isinstance(dependencies, dict) else {}
            )
            if (
                model.get("version") != active_model.get("version")
                or model.get("checksum") != active_model.get("checksum")
                or constraints.get("checksum") != constraints_checksum
            ):
                analysis["status"] = "stale"
        updated.append(analysis)
    return updated


def derive_numerics_artifact(
    analyses: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Derive the canonical aggregate projection from analysis-owned state."""

    ordered = sorted(
        (deepcopy(item) for item in analyses),
        key=lambda item: str(item["analysis_id"]),
    )
    ids = [str(item["analysis_id"]) for item in ordered]
    if len(ids) != len(set(ids)):
        raise ValueError("manifest contains duplicate numerics analysis_id values")
    if not ordered:
        return {
            "status": "not_started",
            "files": [],
            "analyses": [],
            "produced_by": None,
            "timestamp": None,
        }
    aggregate_files = canonical_strings(
        path
        for item in ordered
        for path in item.get("files", [])
        if isinstance(path, str)
    )
    latest = max(
        ordered,
        key=lambda item: (str(item.get("timestamp", "")), str(item.get("analysis_id", ""))),
    )
    return {
        "status": aggregate_numerics_status(ordered),
        "files": aggregate_files,
        "analyses": ordered,
        "produced_by": latest["produced_by"],
        "timestamp": latest["timestamp"],
    }


def merge_numerics_analysis(
    existing_numerics: dict[str, Any],
    analysis: dict[str, Any],
    *,
    active_model: dict[str, Any],
    constraints_checksum: str,
) -> dict[str, Any]:
    """Pure deterministic upsert of one analysis and all derived aggregate fields."""

    existing = existing_numerics.get("analyses", [])
    if not isinstance(existing, list) or not all(isinstance(item, dict) for item in existing):
        raise ValueError(
            "manifest v2 requires artifacts.numerics.analyses to contain objects; "
            "run scripts/migrate_manifest_v2.py first"
        )
    ids = [str(item.get("analysis_id")) for item in existing]
    if len(ids) != len(set(ids)):
        raise ValueError("manifest contains duplicate numerics analysis_id values")

    retained = [item for item in existing if item.get("analysis_id") != analysis["analysis_id"]]
    retained = _mark_stale_against_active_inputs(
        retained,
        active_model=active_model,
        constraints_checksum=constraints_checksum,
    )
    return derive_numerics_artifact([*retained, deepcopy(analysis)])


def refresh_numerics_staleness_for_inputs(
    manifest: dict[str, Any],
    *,
    active_model: dict[str, Any],
    constraints_checksum: str,
) -> dict[str, Any]:
    """Purely rederive stale/aggregate state for an explicit input generation."""

    if manifest.get("manifest_version") != 2:
        raise ValueError("manifest_version=2 is required before refreshing staleness")
    candidate = deepcopy(manifest)
    numerics = candidate.get("artifacts", {}).get("numerics", {})
    analyses = numerics.get("analyses", []) if isinstance(numerics, dict) else None
    if not isinstance(analyses, list) or not all(isinstance(item, dict) for item in analyses):
        raise ValueError("manifest v2 numerics analyses must be objects")
    refreshed = _mark_stale_against_active_inputs(
        analyses,
        active_model=active_model,
        constraints_checksum=constraints_checksum,
    )
    candidate["artifacts"]["numerics"] = derive_numerics_artifact(refreshed)
    return candidate


def refresh_numerics_staleness(
    manifest: dict[str, Any],
    *,
    project_dir: Path,
) -> dict[str, Any]:
    """Purely rederive stale/aggregate state after an upstream live-file change."""

    return refresh_numerics_staleness_for_inputs(
        manifest,
        active_model=manifest.get("artifacts", {}).get("model", {}),
        constraints_checksum=file_sha256(
            project_dir / "constraints" / "constraints-data.json"
        ),
    )


def build_manifest_for_numerics(
    manifest: dict[str, Any],
    *,
    project_dir: Path,
    analysis_id: str,
    scan_config: dict[str, Any],
    constraints_by_id: dict[str, Any],
    scan_config_path: Path,
    scan_csv_path: Path,
    scan_meta_path: Path,
    analysis_summary_path: Path | None = None,
    custom_observables_path: Path | None = None,
    figure_paths: Iterable[Path] = (),
    figure_evidence_paths: Iterable[Path] | None = None,
    figure_meta_path: Path | None = None,
    allow_unpublished_files: bool = False,
    history_action: str | None = None,
    history_event_id: str | None = None,
    timestamp: str,
) -> dict[str, Any]:
    """Build, but do not publish, a complete manifest v2 candidate."""

    if manifest.get("manifest_version") != 2:
        raise ValueError(
            "manifest_version=2 is required; run scripts/migrate_manifest_v2.py first"
        )
    analysis_id = validate_analysis_id(analysis_id)
    candidate = deepcopy(manifest)
    existing_numerics = candidate.get("artifacts", {}).get("numerics", {})
    if not isinstance(existing_numerics, dict):
        raise ValueError("manifest artifacts.numerics must be an object")
    existing_analysis = _analysis_by_id(existing_numerics, analysis_id)
    preserve_scan_dependencies = history_action in {None, "numerics_figures_regenerated"}
    if history_action == "numerics_figures_regenerated" and existing_analysis is None:
        raise ValueError(f"cannot replot unregistered analysis {analysis_id!r}")
    if (
        history_action == "numerics_figures_regenerated"
        and existing_analysis is not None
        and existing_analysis.get("status") == "stale"
    ):
        raise ValueError(f"cannot replot stale analysis {analysis_id!r}")

    figure_paths = tuple(figure_paths)
    files = build_numerics_files(
        project_dir=project_dir,
        scan_config_path=scan_config_path,
        scan_csv_path=scan_csv_path,
        scan_meta_path=scan_meta_path,
        analysis_summary_path=analysis_summary_path,
        custom_observables_path=custom_observables_path,
        figure_paths=figure_paths,
        figure_meta_path=figure_meta_path,
        allow_unpublished_files=allow_unpublished_files,
    )
    dependencies = (
        deepcopy(existing_analysis["depends_on"])
        if preserve_scan_dependencies and existing_analysis is not None
        else build_numerics_dependencies(project_dir=project_dir, scan_config=scan_config)
    )
    analysis = {
        "analysis_id": analysis_id,
        "status": determine_numerics_status(
            constraints_by_id,
            scan_config,
            figure_paths=(
                tuple(figure_evidence_paths)
                if figure_evidence_paths is not None
                else figure_paths
            ),
        ),
        "files": files,
        "depends_on": dependencies,
        "produced_by": "hep-numerics",
        "timestamp": timestamp,
    }
    active_model = candidate["artifacts"].get("model", {})
    constraints_checksum = file_sha256(
        project_dir / "constraints" / "constraints-data.json"
    )
    candidate["artifacts"]["numerics"] = merge_numerics_analysis(
        existing_numerics,
        analysis,
        active_model=active_model,
        constraints_checksum=constraints_checksum,
    )
    candidate["last_updated"] = timestamp[:10]

    if history_action is not None:
        if (
            not isinstance(history_event_id, str)
            or len(history_event_id) != 32
            or any(character not in "0123456789abcdef" for character in history_event_id)
        ):
            raise ValueError(
                "new numerics history events require a fresh 32-character "
                "lowercase-hex event_id"
            )
        existing_event_ids = {
            entry.get("event_id")
            for entry in candidate.get("history", [])
            if isinstance(entry, dict) and isinstance(entry.get("event_id"), str)
        }
        if history_event_id in existing_event_ids:
            raise ValueError(f"duplicate manifest history event_id {history_event_id!r}")
        history_entry = {
            "action": history_action,
            "analysis_id": analysis_id,
            "timestamp": timestamp,
            "by": "hep-numerics",
            "note": f"analysis_id={analysis_id}",
        }
        history_entry["event_id"] = history_event_id
        history = candidate.setdefault("history", [])
        if history_entry not in history:
            history.append(history_entry)
    return candidate


def _write_staged_manifest_candidate(
    manifest_path: Path,
    candidate: dict[str, Any],
) -> Path:
    """Write only beneath a private publication-transaction staging directory."""

    resolved = manifest_path.resolve(strict=False)
    parts = resolved.parts
    try:
        transaction_root_index = len(parts) - 1 - parts[::-1].index(
            ".hep-workflow-transactions"
        )
    except ValueError as exc:
        raise ValueError(
            "manifest candidates may be written only inside publication-transaction staging"
        ) from exc
    relative_parts = parts[transaction_root_index + 1 :]
    if len(relative_parts) < 3 or relative_parts[1] != "staging":
        raise ValueError(
            "manifest candidates may be written only inside publication-transaction staging"
        )

    manifest_path.write_text(json.dumps(candidate, indent=2) + "\n", encoding="utf-8")
    return manifest_path


def _history_entry_for_analysis(
    history: Any,
    analysis_id: str,
) -> dict[str, Any]:
    matches: list[dict[str, Any]] = []
    for entry in history if isinstance(history, list) else []:
        if not isinstance(entry, dict) or not str(entry.get("action", "")).startswith(
            "numerics_"
        ):
            continue
        linked_analysis = numerics_history_analysis_id(entry)
        if linked_analysis == analysis_id:
            matches.append(entry)
    if not matches:
        raise ValueError(
            f"cannot migrate {analysis_id!r}: no analysis-scoped numerics history entry"
        )
    return matches[-1]


def _recorded_dependency_checksum(
    scan_meta: dict[str, Any],
    *,
    role: str,
    path: str,
) -> str:
    graph = scan_meta.get("input_provenance")
    entries = graph.get("entries") if isinstance(graph, dict) else None
    matches = [
        entry.get("sha256")
        for entry in (entries if isinstance(entries, list) else [])
        if isinstance(entry, dict)
        and entry.get("scope") == "project"
        and entry.get("role") == role
        and entry.get("path") == path
    ]
    if len(matches) != 1 or not isinstance(matches[0], str):
        raise ValueError(
            f"cannot derive unique recorded dependency {role!r} at {path!r}"
        )
    return matches[0]


def _validate_schema_instance(
    payload: Any,
    *,
    repo_root: Path,
    schema_name: str,
    label: str,
) -> None:
    """Fail closed when migration evidence violates an authoritative schema."""

    from jsonschema import Draft202012Validator

    schema = load_json(repo_root / "schemas" / schema_name)
    errors = sorted(
        Draft202012Validator(schema).iter_errors(payload),
        key=lambda error: list(error.absolute_path),
    )
    if not errors:
        return
    rendered = "; ".join(
        f"{'.'.join(map(str, error.absolute_path)) or '<root>'}: {error.message}"
        for error in errors
    )
    raise ValueError(f"{label} failed {schema_name}: {rendered}")


def migrate_manifest_v1(
    manifest: dict[str, Any],
    *,
    project_dir: Path,
) -> dict[str, Any]:
    """Pure, deterministic and fail-closed manifest v1 -> v2 migration."""

    if manifest.get("manifest_version") == 2:
        numerics = manifest.get("artifacts", {}).get("numerics", {})
        analyses = numerics.get("analyses", []) if isinstance(numerics, dict) else None
        if not isinstance(analyses, list) or not all(
            isinstance(item, dict) for item in analyses
        ):
            raise ValueError("manifest_version=2 has a non-v2 numerics analysis registry")
        return deepcopy(manifest)
    if manifest.get("manifest_version") not in {None, 1}:
        raise ValueError(f"unsupported manifest_version {manifest.get('manifest_version')!r}")

    candidate = deepcopy(manifest)
    repo_root = Path(__file__).resolve().parents[4]
    numerics = candidate.get("artifacts", {}).get("numerics")
    if not isinstance(numerics, dict):
        raise ValueError("manifest artifacts.numerics must be an object")
    analysis_ids = numerics.get("analyses")
    if not isinstance(analysis_ids, list) or not all(
        isinstance(item, str) for item in analysis_ids
    ):
        raise ValueError("manifest v1 numerics.analyses must be a string array")
    if len(analysis_ids) != len(set(analysis_ids)):
        raise ValueError("manifest v1 contains duplicate numerics analysis IDs")

    if not analysis_ids:
        expected_empty = {
            "status": "not_started",
            "files": [],
            "depends_on": {
                "model": {"version": None, "checksum": None},
                "calculations": {"tasks": [], "model_version": None},
                "constraints": {"checksum": None},
            },
            "analyses": [],
            "produced_by": None,
            "timestamp": None,
        }
        if numerics != expected_empty:
            raise ValueError(
                "cannot migrate an empty analysis registry unless the legacy "
                "numerics artifact is the exact not_started empty skeleton"
            )
        candidate["manifest_version"] = 2
        candidate["artifacts"]["numerics"] = {
            "status": "not_started",
            "files": [],
            "analyses": [],
            "produced_by": None,
            "timestamp": None,
        }
        _validate_schema_instance(
            candidate,
            repo_root=repo_root,
            schema_name="manifest.schema.json",
            label="migrated manifest candidate",
        )
        return candidate

    if numerics.get("status") not in {"done", "partial"}:
        raise ValueError(
            "cannot unambiguously migrate a nonempty legacy numerics registry "
            f"with aggregate status {numerics.get('status')!r}"
        )

    expected_legacy_fields = {
        "status",
        "files",
        "depends_on",
        "analyses",
        "produced_by",
        "timestamp",
    }
    if set(numerics) != expected_legacy_fields:
        raise ValueError(
            "cannot migrate legacy numerics with unexpected/missing aggregate fields: "
            f"{sorted(set(numerics) ^ expected_legacy_fields)}"
        )
    legacy_files = numerics.get("files")
    if (
        not isinstance(legacy_files, list)
        or not legacy_files
        or not all(isinstance(item, str) for item in legacy_files)
        or len(legacy_files) != len(set(legacy_files))
    ):
        raise ValueError(
            "cannot migrate nonempty legacy numerics without unique nonempty string files"
        )
    legacy_dependencies = numerics.get("depends_on")
    if not isinstance(legacy_dependencies, dict):
        raise ValueError("cannot migrate legacy numerics without aggregate depends_on")
    if not isinstance(numerics.get("produced_by"), str) or not numerics[
        "produced_by"
    ].strip():
        raise ValueError("cannot migrate legacy numerics without aggregate produced_by")
    if not isinstance(numerics.get("timestamp"), str):
        raise ValueError("cannot migrate legacy numerics without aggregate timestamp")

    constraints_path = project_dir / "constraints" / "constraints-data.json"
    constraints_payload = load_json(constraints_path)
    constraints_by_id = {
        str(item.get("id")): item
        for item in constraints_payload.get("constraints", [])
        if isinstance(item, dict)
    }
    current_constraints_checksum = file_sha256(constraints_path)
    active_model = candidate.get("artifacts", {}).get("model", {})
    migrated: list[dict[str, Any]] = []
    intrinsic_statuses: dict[str, str] = {}

    for raw_analysis_id in sorted(analysis_ids):
        analysis_id = validate_analysis_id(raw_analysis_id)
        config_path = project_dir / "numerics" / "scan-configs" / f"{analysis_id}.json"
        result_dir = project_dir / "numerics" / "scan-results" / analysis_id
        csv_path = result_dir / "scan.csv"
        meta_path = result_dir / "scan.meta.json"
        summary_path = project_dir / "numerics" / f"analysis-summary-{analysis_id}.md"
        required = (config_path, csv_path, meta_path, summary_path)
        missing = [
            path.relative_to(project_dir).as_posix()
            for path in required
            if not path.is_file() or path.stat().st_size == 0
        ]
        if missing:
            raise ValueError(
                f"cannot migrate {analysis_id!r}: missing/non-empty evidence {missing}"
            )

        scan_config = load_json(config_path)
        _validate_schema_instance(
            scan_config,
            repo_root=repo_root,
            schema_name="scan-config.schema.json",
            label=f"cannot migrate {analysis_id!r}: scan config",
        )
        scan_meta = load_json(meta_path)
        _validate_schema_instance(
            scan_meta,
            repo_root=repo_root,
            schema_name="scan-meta.schema.json",
            label=f"cannot migrate {analysis_id!r}: scan metadata",
        )
        if not isinstance(scan_meta, dict) or scan_meta.get("analysis_id") != analysis_id:
            raise ValueError(f"cannot migrate {analysis_id!r}: invalid scan metadata identity")
        snapshot = scan_meta.get("scan_config_snapshot")
        if not isinstance(snapshot, dict) or snapshot.get("analysis_id") != analysis_id:
            raise ValueError(f"cannot migrate {analysis_id!r}: invalid config snapshot identity")
        snapshot_dependencies = snapshot.get("depends_on")
        if not isinstance(snapshot_dependencies, dict):
            raise ValueError(f"cannot migrate {analysis_id!r}: missing snapshot dependencies")
        if scan_meta.get("scan_csv_sha256") != file_sha256(csv_path):
            raise ValueError(f"cannot migrate {analysis_id!r}: scan CSV checksum mismatch")
        pair_issues = validate_scan_artifact_pair(
            project_dir,
            analysis_id,
            None,
            Path(__file__).resolve().parents[4],
            historical_scan_config_snapshot=snapshot,
        )
        if pair_issues:
            raise ValueError(
                f"cannot migrate {analysis_id!r}: intrinsic scan evidence is invalid: "
                + "; ".join(pair_issues)
            )
        try:
            producer_script = scan_producer_from_graph(
                scan_meta.get("input_provenance", {}),
                repo_root,
            )
            expected_dependencies = scan_dependency_specs(
                project_dir,
                repo_root,
                config_path,
                snapshot,
                producer_script=producer_script,
            )
        except (OSError, ValueError) as exc:
            raise ValueError(
                f"cannot migrate {analysis_id!r}: cannot derive expected scan provenance: "
                f"{exc}"
            ) from exc
        graph_issues = verify_dependency_graph(
            scan_meta.get("input_provenance"),
            project_dir,
            repo_root,
            expected_specs=expected_dependencies,
            check_current_bytes=False,
        )
        if graph_issues:
            raise ValueError(
                f"cannot migrate {analysis_id!r}: recorded scan provenance is "
                "structurally incomplete: "
                + "; ".join(graph_issues)
            )

        files = [relative_to_project(path, project_dir) for path in required]
        figure_paths = [
            project_dir / "numerics" / "figures" / analysis_id / f"{key}.{suffix}"
            for key in validate_figure_output_keys(snapshot)
            for suffix in ("pdf", "png")
        ]
        files.extend(
            relative_to_project(path, project_dir)
            for path in figure_paths
            if path.is_file() and path.stat().st_size > 0
        )
        graph = scan_meta.get("input_provenance")
        graph_entries = graph.get("entries") if isinstance(graph, dict) else None
        custom_recorded = any(
            isinstance(entry, dict)
            and entry.get("scope") == "project"
            and entry.get("role") == "custom-observables"
            and entry.get("path") == "numerics/custom_observables.py"
            for entry in (graph_entries if isinstance(graph_entries, list) else [])
        )
        if custom_recorded:
            custom_path = project_dir / "numerics" / "custom_observables.py"
            if not custom_path.is_file():
                raise ValueError(
                    f"cannot migrate {analysis_id!r}: recorded custom module is missing"
                )
            files.append(relative_to_project(custom_path, project_dir))

        constraints_checksum = _recorded_dependency_checksum(
            scan_meta,
            role="constraints-data",
            path="constraints/constraints-data.json",
        )
        dependencies = {
            "model": {
                "version": snapshot_dependencies.get("model_version"),
                "checksum": snapshot_dependencies.get("model_checksum"),
            },
            "calculations": {
                "tasks": canonical_strings(snapshot_dependencies.get("task_ids", [])),
                "model_version": snapshot_dependencies.get("model_version"),
            },
            "constraints": {"checksum": constraints_checksum},
        }
        history_entry = _history_entry_for_analysis(candidate.get("history"), analysis_id)
        if not isinstance(history_entry.get("by"), str) or not history_entry["by"].strip():
            raise ValueError(f"cannot migrate {analysis_id!r}: history producer is missing")
        if not isinstance(history_entry.get("timestamp"), str):
            raise ValueError(f"cannot migrate {analysis_id!r}: history timestamp is missing")
        current_graph_issues = verify_dependency_graph(
            scan_meta.get("input_provenance"),
            project_dir,
            repo_root,
            expected_specs=expected_dependencies,
        )
        stale = bool(current_graph_issues) or (
            dependencies["model"]["version"] != active_model.get("version")
            or dependencies["model"]["checksum"] != active_model.get("checksum")
            or constraints_checksum != current_constraints_checksum
        )
        intrinsic_status = determine_numerics_status(
            constraints_by_id,
            snapshot,
            figure_paths=figure_paths,
        )
        intrinsic_statuses[analysis_id] = intrinsic_status
        status = "stale" if stale else intrinsic_status
        migrated.append(
            {
                "analysis_id": analysis_id,
                "status": status,
                "files": canonical_strings(files),
                "depends_on": dependencies,
                "produced_by": history_entry["by"],
                "timestamp": history_entry["timestamp"],
            }
        )

    derived_numerics = derive_numerics_artifact(migrated)
    latest = max(
        migrated,
        key=lambda item: (str(item["timestamp"]), str(item["analysis_id"])),
    )
    latest_analysis_id = str(latest["analysis_id"])
    if numerics.get("produced_by") != latest.get("produced_by"):
        raise ValueError(
            "cannot migrate legacy numerics: aggregate produced_by does not match "
            f"the deterministically latest analysis {latest_analysis_id!r}"
        )
    if numerics.get("timestamp") != latest.get("timestamp"):
        raise ValueError(
            "cannot migrate legacy numerics: aggregate timestamp does not match "
            f"the deterministically latest analysis {latest_analysis_id!r}"
        )
    if legacy_dependencies != latest.get("depends_on"):
        raise ValueError(
            "cannot migrate legacy numerics: aggregate depends_on does not match "
            f"the deterministically latest analysis {latest_analysis_id!r}"
        )
    if numerics.get("status") != intrinsic_statuses[latest_analysis_id]:
        raise ValueError(
            "cannot migrate legacy numerics: aggregate status does not match the "
            f"latest analysis intrinsic status {intrinsic_statuses[latest_analysis_id]!r}"
        )

    legacy_file_set = set(legacy_files)
    reconstructed_file_set = set(derived_numerics["files"])
    latest_file_set = set(latest["files"])
    unowned_legacy_files = sorted(legacy_file_set - reconstructed_file_set)
    missing_latest_files = sorted(latest_file_set - legacy_file_set)
    if unowned_legacy_files:
        raise ValueError(
            "cannot migrate legacy numerics without discarding unowned legacy files: "
            f"{unowned_legacy_files}"
        )
    if missing_latest_files:
        raise ValueError(
            "cannot migrate legacy numerics: aggregate files do not contain the "
            f"latest analysis evidence {missing_latest_files}"
        )

    candidate["manifest_version"] = 2
    candidate["artifacts"]["numerics"] = derived_numerics
    _validate_schema_instance(
        candidate,
        repo_root=repo_root,
        schema_name="manifest.schema.json",
        label="migrated manifest candidate",
    )
    return candidate
