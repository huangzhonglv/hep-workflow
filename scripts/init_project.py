#!/usr/bin/env python3
"""Initialize a workspace project scaffold for the HEP workflow."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


PROJECT_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")

PROJECT_SUBDIRECTORIES = (
    "idea",
    "model",
    "calculations",
    "constraints",
    "numerics",
    "numerics/scan-results",
    "numerics/figures",
    "paper",
)


def today_utc() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def build_manifest(project_name: str, created: str | None = None) -> dict:
    created = created or today_utc()
    return {
        "project_name": project_name,
        "created": created,
        "last_updated": created,
        "active_model_version": None,
        "artifacts": {
            "idea": {
                "status": "not_started",
                "files": [],
                "produced_by": None,
                "timestamp": None,
            },
            "model": {
                "status": "not_started",
                "version": None,
                "files": [],
                "checksum": None,
                "produced_by": None,
                "timestamp": None,
            },
            "calculations": {
                "status": "not_started",
                "completed_tasks": [],
                "pending_tasks": [],
                "depends_on": {
                    "model": {"version": None, "checksum": None},
                },
                "produced_by": None,
                "timestamp": None,
            },
            "constraints": {
                "status": "not_started",
                "files": [],
                "depends_on": {
                    "model": {"version": None, "checksum": None},
                },
                "produced_by": None,
                "timestamp": None,
            },
            "numerics": {
                "status": "not_started",
                "files": [],
                "depends_on": {
                    "model": {"version": None, "checksum": None},
                    "calculations": {"tasks": [], "model_version": None},
                    "constraints": {"checksum": None},
                },
                "produced_by": None,
                "timestamp": None,
            },
        },
        "history": [],
    }


def init_project(project_name: str, base_dir: Path, exist_ok: bool = False) -> Path:
    if not PROJECT_NAME_PATTERN.fullmatch(project_name):
        raise ValueError(
            "project_name must match ^[a-z0-9][a-z0-9-]*$ "
            "(lowercase letters, digits, and hyphens only)"
        )

    project_dir = base_dir / project_name
    manifest_path = project_dir / "manifest.json"

    if project_dir.exists() and any(project_dir.iterdir()) and not exist_ok:
        raise FileExistsError(
            f"Project directory already exists and is not empty: {project_dir}"
        )

    project_dir.mkdir(parents=True, exist_ok=True)
    for relative_dir in PROJECT_SUBDIRECTORIES:
        (project_dir / relative_dir).mkdir(parents=True, exist_ok=True)

    if manifest_path.exists() and not exist_ok:
        raise FileExistsError(f"Manifest already exists: {manifest_path}")

    manifest = build_manifest(project_name)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return project_dir


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Initialize workspace/projects/{project_name}/ for the HEP workflow."
    )
    parser.add_argument("project_name", help="Project name in kebab-case.")
    parser.add_argument(
        "--workspace-root",
        default="workspace/projects",
        help="Workspace projects root relative to the repository root.",
    )
    parser.add_argument(
        "--exist-ok",
        action="store_true",
        help="Allow reusing an existing project directory and overwrite manifest.json.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parent.parent
    workspace_root = repo_root / args.workspace_root
    workspace_root.mkdir(parents=True, exist_ok=True)

    try:
        project_dir = init_project(
            project_name=args.project_name,
            base_dir=workspace_root,
            exist_ok=args.exist_ok,
        )
    except (ValueError, FileExistsError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(project_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
