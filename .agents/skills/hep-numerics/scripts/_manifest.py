#!/usr/bin/env python3
"""Shared manifest update helpers for hep-numerics scripts."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


def load_json(path: Path) -> Any:
    """Load JSON from disk."""

    return json.loads(path.read_text(encoding="utf-8"))


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


def determine_numerics_status(
    constraints_by_id: dict[str, Any],
    scan_config: dict[str, Any],
) -> str:
    """Compute the numerics artifact status from the active constraint selection."""

    direct_or_interpolated_constraints = {
        constraint_id
        for constraint_id, constraint in constraints_by_id.items()
        if constraint.get("implementation_status") in {"direct", "interpolated"}
    }
    used_constraints = set(scan_config.get("constraints_used", []))
    return "done" if direct_or_interpolated_constraints.issubset(used_constraints) else "partial"


def build_numerics_files(
    *,
    project_dir: Path,
    scan_config_path: Path,
    scan_csv_path: Path,
    scan_meta_path: Path,
    analysis_summary_path: Path | None = None,
    custom_observables_path: Path | None = None,
    figure_paths: Iterable[Path] = (),
) -> list[str]:
    """Build the manifest file list for the current numerics analysis."""

    files = [
        relative_to_project(scan_config_path, project_dir),
        relative_to_project(scan_csv_path, project_dir),
        relative_to_project(scan_meta_path, project_dir),
    ]

    if analysis_summary_path is not None and analysis_summary_path.exists():
        files.append(relative_to_project(analysis_summary_path, project_dir))
    if custom_observables_path is not None and custom_observables_path.exists():
        files.append(relative_to_project(custom_observables_path, project_dir))

    files.extend(
        relative_to_project(path, project_dir)
        for path in sorted(set(path.resolve() for path in figure_paths))
        if path.exists()
    )
    return dedupe_preserve_order(files)


def update_manifest_for_numerics(
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
    history_action: str | None = None,
) -> Path:
    """Update manifest.json for one numerics analysis."""

    manifest_path = project_dir / "manifest.json"
    manifest = load_json(manifest_path)
    timestamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    existing_numerics = manifest.get("artifacts", {}).get("numerics", {})
    existing_analyses = existing_numerics.get("analyses", [])
    analyses = dedupe_preserve_order(
        [*(analysis for analysis in existing_analyses if isinstance(analysis, str)), analysis_id]
    )

    manifest["last_updated"] = timestamp[:10]
    manifest["artifacts"]["numerics"] = {
        "status": determine_numerics_status(constraints_by_id, scan_config),
        "files": build_numerics_files(
            project_dir=project_dir,
            scan_config_path=scan_config_path,
            scan_csv_path=scan_csv_path,
            scan_meta_path=scan_meta_path,
            analysis_summary_path=analysis_summary_path,
            custom_observables_path=custom_observables_path,
            figure_paths=figure_paths,
        ),
        "depends_on": {
            "model": {
                "version": scan_config["depends_on"]["model_version"],
                "checksum": scan_config["depends_on"]["model_checksum"],
            },
            "calculations": {
                "tasks": scan_config["depends_on"].get("task_ids", []),
                "model_version": scan_config["depends_on"]["model_version"],
            },
            "constraints": {
                "checksum": file_sha256(project_dir / "constraints" / "constraints-data.json"),
            },
        },
        "analyses": analyses,
        "produced_by": "hep-numerics",
        "timestamp": timestamp,
    }

    if history_action is not None:
        manifest.setdefault("history", []).append(
            {
                "action": history_action,
                "analysis_id": analysis_id,
                "timestamp": timestamp,
                "by": "hep-numerics",
                "note": f"analysis_id={analysis_id}",
            }
        )

    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest_path
