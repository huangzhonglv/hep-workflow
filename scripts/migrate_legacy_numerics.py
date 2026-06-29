#!/usr/bin/env python3
"""Migrate legacy hep-numerics metadata for one analysis.

This script updates metadata only. It does not touch scan.csv or figure files.
"""

from __future__ import annotations

import argparse
import difflib
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ANALYSIS_ID = "analysis-001"
HISTORY_ACTION = "numerics_analysis_complete"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def render_json(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def rel(path: Path, project_dir: Path) -> str:
    return path.resolve().relative_to(project_dir.resolve()).as_posix()


def collect_analysis_files(project_dir: Path, analysis_id: str) -> list[str]:
    numerics_dir = project_dir / "numerics"
    required_or_optional = [
        numerics_dir / "scan-configs" / f"{analysis_id}.json",
        numerics_dir / "scan-results" / analysis_id / "scan.csv",
        numerics_dir / "scan-results" / analysis_id / "scan.meta.json",
        numerics_dir / f"analysis-summary-{analysis_id}.md",
    ]
    files = [rel(path, project_dir) for path in required_or_optional if path.exists()]

    figure_dir = numerics_dir / "figures" / analysis_id
    if figure_dir.exists():
        figure_paths = sorted(figure_dir.glob("*.pdf")) + sorted(figure_dir.glob("*.png"))
        files.extend(rel(path, project_dir) for path in figure_paths if path.exists())

    return files


def build_analysis_record(manifest: dict[str, Any], project_dir: Path) -> dict[str, Any]:
    artifacts = manifest.get("artifacts", {})
    model_artifact = artifacts.get("model", {})
    constraints_artifact = artifacts.get("constraints", {})
    calculations_artifact = artifacts.get("calculations", {})

    return {
        "analysis_id": ANALYSIS_ID,
        "status": "done",
        "depends_on": {
            "model": {
                "version": manifest.get("active_model_version"),
                "checksum": model_artifact.get("checksum"),
            },
            "calculations": {
                "tasks": calculations_artifact.get("completed_tasks", []),
            },
            "constraints": {
                "checksum": constraints_artifact.get("checksum"),
            },
        },
        "files": collect_analysis_files(project_dir, ANALYSIS_ID),
        "last_action": HISTORY_ACTION,
    }


def migrate_manifest(manifest: dict[str, Any], project_dir: Path, today: str) -> dict[str, Any]:
    migrated = deepcopy(manifest)
    artifacts = migrated.setdefault("artifacts", {})
    numerics = artifacts.setdefault("numerics", {})
    analyses = numerics.get("analyses")

    if not analyses:
        numerics["analyses"] = [build_analysis_record(migrated, project_dir)]

    migrated["last_updated"] = today
    return migrated


def migrate_scan_meta(scan_meta: dict[str, Any], scan_config: dict[str, Any]) -> dict[str, Any]:
    migrated = deepcopy(scan_meta)
    if "history_action" not in migrated:
        migrated["history_action"] = HISTORY_ACTION
    if "scan_parameters" not in migrated:
        migrated["scan_parameters"] = scan_config.get("scan_parameters", [])
    return migrated


def print_diff(label: str, before: str, after: str) -> None:
    if before == after:
        print(f"{label}: no changes")
        return
    print(f"--- {label}")
    print(
        "".join(
            difflib.unified_diff(
                before.splitlines(keepends=True),
                after.splitlines(keepends=True),
                fromfile=f"{label}.before",
                tofile=f"{label}.after",
            )
        ),
        end="",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Migrate legacy numerics metadata for analysis-001."
    )
    parser.add_argument(
        "--project-dir",
        required=True,
        type=Path,
        help="Path to the workspace project root.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print manifest/meta diffs without writing files.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    project_dir = args.project_dir.resolve()

    manifest_path = project_dir / "manifest.json"
    scan_config_path = project_dir / "numerics" / "scan-configs" / f"{ANALYSIS_ID}.json"
    scan_meta_path = (
        project_dir / "numerics" / "scan-results" / ANALYSIS_ID / "scan.meta.json"
    )

    for path in [manifest_path, scan_config_path, scan_meta_path]:
        if not path.exists():
            raise FileNotFoundError(path)

    manifest = load_json(manifest_path)
    scan_config = load_json(scan_config_path)
    scan_meta = load_json(scan_meta_path)

    today = datetime.now(timezone.utc).date().isoformat()
    new_manifest = migrate_manifest(manifest, project_dir, today)
    new_scan_meta = migrate_scan_meta(scan_meta, scan_config)

    before_manifest = render_json(manifest)
    after_manifest = render_json(new_manifest)
    before_scan_meta = render_json(scan_meta)
    after_scan_meta = render_json(new_scan_meta)

    if args.dry_run:
        print_diff(str(manifest_path), before_manifest, after_manifest)
        print_diff(str(scan_meta_path), before_scan_meta, after_scan_meta)
        return 0

    if before_manifest != after_manifest:
        manifest_path.write_text(after_manifest, encoding="utf-8")
    if before_scan_meta != after_scan_meta:
        scan_meta_path.write_text(after_scan_meta, encoding="utf-8")

    print(f"migrated {project_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
