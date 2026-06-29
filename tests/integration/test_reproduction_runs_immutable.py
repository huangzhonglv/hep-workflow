from __future__ import annotations

import hashlib
from pathlib import Path

from tests.integration.test_compare_to_reference_minimal import (
    _run_compare,
    _smoke_project,
    _write_synthetic_scan,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_reproduction_runs_are_immutable(
    tmp_path,
    project_copy_factory,
    smoke_e2e_fixture_path: Path,
    repo_root: Path,
) -> None:
    project_dir = _smoke_project(tmp_path, project_copy_factory, smoke_e2e_fixture_path)
    _write_synthetic_scan(project_dir)

    first = _run_compare(repo_root, project_dir, "run-001")
    assert first.returncode == 0, first.stdout + first.stderr
    result_path = project_dir / "reproduction" / "runs" / "run-001" / "reproduction-result.json"
    before_hash = _sha256(result_path)

    repeat = _run_compare(repo_root, project_dir, "run-001")
    assert repeat.returncode != 0
    assert "already exists" in (repeat.stdout + repeat.stderr)
    assert _sha256(result_path) == before_hash

    second = _run_compare(repo_root, project_dir, "run-002")
    assert second.returncode == 0, second.stdout + second.stderr
    assert (
        project_dir
        / "reproduction"
        / "runs"
        / "run-002"
        / "reproduction-result.json"
    ).exists()
