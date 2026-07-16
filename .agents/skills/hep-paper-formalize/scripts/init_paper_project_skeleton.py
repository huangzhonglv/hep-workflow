#!/usr/bin/env python3
"""Initialize a paper-reproduction project skeleton without writing files."""

from __future__ import annotations

import argparse
import re
import sys
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
    "literature",
    "literature/digitized",
    "literature/style",
    "reproduction",
    "reproduction/runs",
    "reproduction/figures",
    "reproduction/reports",
)


def init_project_skeleton(
    project_name: str,
    base_dir: Path,
    exist_ok: bool = False,
) -> Path:
    if not PROJECT_NAME_PATTERN.fullmatch(project_name):
        raise ValueError(
            "project_name must match ^[a-z0-9][a-z0-9-]*$ "
            "(lowercase letters, digits, and hyphens only)"
        )

    project_dir = base_dir / project_name
    if project_dir.exists() and any(project_dir.iterdir()) and not exist_ok:
        raise FileExistsError(
            f"Project directory already exists and is not empty: {project_dir}"
        )

    project_dir.mkdir(parents=True, exist_ok=True)
    for relative_dir in PROJECT_SUBDIRECTORIES:
        (project_dir / relative_dir).mkdir(parents=True, exist_ok=True)

    return project_dir


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Initialize workspace/projects/{project_name}/ for hep-paper-formalize "
            "without creating manifest.json or placeholder output files."
        )
    )
    parser.add_argument("project_name", help="Project name in kebab-case.")
    parser.add_argument(
        "--workspace-root",
        default="workspace/projects",
        help=(
            "Workspace projects root. Absolute paths are used as-is; "
            "relative paths are resolved from the inferred repository root."
        ),
    )
    parser.add_argument(
        "--exist-ok",
        action="store_true",
        help="Allow reusing an existing project directory.",
    )
    return parser.parse_args()


def resolve_repo_root() -> Path:
    script_dir = Path(__file__).resolve().parent
    skill_dir = script_dir.parent
    skills_dir = skill_dir.parent
    platform_dir = skills_dir.parent

    if (
        script_dir.name == "scripts"
        and skill_dir.name == "hep-paper-formalize"
        and skills_dir.name == "skills"
        and platform_dir.name in {".agents", ".claude"}
    ):
        return platform_dir.parent

    raise RuntimeError(
        "Cannot infer repository root from the current skill layout. "
        "Expected the script under "
        "<repo>/.agents/skills/hep-paper-formalize/scripts/ or "
        "<repo>/.claude/skills/hep-paper-formalize/scripts/. "
        "If this skill was copied elsewhere, rerun with an absolute "
        "--workspace-root."
    )


def main() -> int:
    args = parse_args()
    workspace_root = Path(args.workspace_root)
    if not workspace_root.is_absolute():
        repo_root = resolve_repo_root()
        workspace_root = repo_root / workspace_root
    workspace_root.mkdir(parents=True, exist_ok=True)

    try:
        project_dir = init_project_skeleton(
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
