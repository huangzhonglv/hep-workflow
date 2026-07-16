from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_hep_idea_skeleton_creates_all_numerics_directories(
    tmp_path: Path,
    repo_root: Path,
) -> None:
    workspace_root = tmp_path / "workspace" / "projects"
    script_path = (
        repo_root
        / ".claude"
        / "skills"
        / "hep-idea"
        / "scripts"
        / "init_project_skeleton.py"
    )

    result = subprocess.run(
        [
            sys.executable,
            str(script_path),
            "skeleton-test",
            "--workspace-root",
            str(workspace_root),
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    project_dir = workspace_root / "skeleton-test"
    assert Path(result.stdout.strip()) == project_dir
    for relative_path in (
        "numerics/scan-configs",
        "numerics/scan-results",
        "numerics/figures",
    ):
        assert (project_dir / relative_path).is_dir()
    assert not (project_dir / "paper").exists()
    assert not (project_dir / "manifest.json").exists()
