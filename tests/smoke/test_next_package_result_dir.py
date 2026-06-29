from __future__ import annotations

import subprocess
from pathlib import Path


def run_next_package_result_dir(repo_root: Path, base_dir: Path) -> Path:
    script_path = (
        repo_root
        / ".agents"
        / "skills"
        / "package-scribe"
        / "scripts"
        / "next-package-result-dir.sh"
    )
    result = subprocess.run(
        ["bash", "-c", 'bash "$0" "$1"', str(script_path), str(base_dir)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return Path(result.stdout.strip())


def test_next_package_result_dir_runs_under_bash(repo_root, tmp_path) -> None:
    empty_base = tmp_path / "empty"
    empty_base.mkdir()

    first = run_next_package_result_dir(repo_root, empty_base)

    assert first == empty_base / "workspace" / "package-scribe" / "package-result001"
    assert first.name == "package-result001"
    assert first.is_dir()
    assert not (empty_base / "package-result").exists()

    existing_base = tmp_path / "with-existing"
    existing_results = existing_base / "workspace" / "package-scribe"
    existing_results.mkdir(parents=True)
    (existing_results / "package-result001").touch()

    second = run_next_package_result_dir(repo_root, existing_base)

    assert second == existing_results / "package-result002"
    assert second.name == "package-result002"
    assert second.is_dir()
    assert not (existing_base / "package-result").exists()


def test_next_package_result_dir_uses_repo_workspace_from_project_root(
    repo_root,
    tmp_path,
) -> None:
    fake_repo = tmp_path / "fake-repo"
    project_root = fake_repo / "workspace" / "projects" / "smoke-e2e"
    project_root.mkdir(parents=True)

    next_dir = run_next_package_result_dir(repo_root, project_root)

    assert next_dir == fake_repo / "workspace" / "package-scribe" / "package-result001"
    assert not (fake_repo / "package-result").exists()
