from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_setup_only_skeleton_does_not_modify_existing_model(
    tmp_path,
    project_copy_factory,
    smoke_e2e_fixture_path: Path,
    repo_root: Path,
) -> None:
    project_dir = project_copy_factory(
        tmp_path,
        "smoke-e2e",
        source_project_path=smoke_e2e_fixture_path,
    )
    model_path = project_dir / "model" / "model-spec.json"
    literature_path = project_dir / "literature" / "paper-meta.json"
    before_model_hash = _sha256(model_path)
    before_literature_text = literature_path.read_text(encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(
                repo_root
                / ".claude"
                / "skills"
                / "hep-paper-formalize"
                / "scripts"
                / "init_paper_project_skeleton.py"
            ),
            "smoke-e2e",
            "--workspace-root",
            str(tmp_path / "workspace" / "projects"),
            "--exist-ok",
        ],
        capture_output=True,
        text=True,
        cwd=repo_root,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert Path(result.stdout.strip()) == project_dir

    assert _sha256(model_path) == before_model_hash
    assert literature_path.read_text(encoding="utf-8") == before_literature_text
    for relative in [
        "literature",
        "literature/digitized",
        "literature/style",
        "reproduction",
        "reproduction/runs",
        "reproduction/figures",
        "reproduction/reports",
    ]:
        assert (project_dir / relative).is_dir()
    assert not (project_dir / "paper").exists()
