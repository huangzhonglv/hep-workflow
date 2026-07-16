from __future__ import annotations

import subprocess
import sys

from tests.unit.compare_reference_fixtures import load_result, make_compare_project, run_compare


def test_cli_rejects_missing_required_args(repo_root) -> None:
    result = subprocess.run(
        [sys.executable, str(repo_root / "scripts" / "compare_to_reference.py")],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "required" in result.stderr


def test_cli_rejects_nonexistent_project_dir(repo_root, tmp_path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(repo_root / "scripts" / "compare_to_reference.py"),
            "--project-dir",
            str(tmp_path / "missing-project"),
            "--analysis-id",
            "analysis-001",
            "--repro-id",
            "run-001",
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "project directory does not exist" in result.stderr


def test_cli_rejects_existing_repro_id(repo_root, tmp_path) -> None:
    project_dir = make_compare_project(tmp_path)
    (project_dir / "reproduction" / "runs" / "run-001").mkdir(parents=True)

    result = run_compare(repo_root, project_dir, "run-001")

    assert result.returncode == 1
    assert "already exists" in result.stderr


def test_full_script_writes_metrics_with_conservative_human_review_verdict(
    repo_root, tmp_path
) -> None:
    project_dir = make_compare_project(tmp_path)

    result = run_compare(repo_root, project_dir, "run-001")

    assert result.returncode == 0, result.stdout + result.stderr
    payload = load_result(project_dir, "run-001")
    target_result = payload["results"][0]
    assert target_result["derivation_independence"] == "unknown"
    assert target_result["verdict"] == "needs_human_review"
    assert target_result["verdict_ceiling"] == "needs_human_review"
    assert 0.0 < target_result["comparison"]["metrics"]["max_relative_error"] < 0.01
    assert target_result["notes"] == ""
