from __future__ import annotations

from tests.unit.compare_reference_fixtures import hash_file, make_compare_project, run_compare


def test_reproduction_run_directory_is_immutable(repo_root, tmp_path) -> None:
    project_dir = make_compare_project(tmp_path)

    first = run_compare(repo_root, project_dir, "run-001")
    result_path = project_dir / "reproduction" / "runs" / "run-001" / "reproduction-result.json"
    first_hash = hash_file(result_path)
    second = run_compare(repo_root, project_dir, "run-001")
    second_hash = hash_file(result_path)
    third = run_compare(repo_root, project_dir, "run-002")

    assert first.returncode == 0, first.stdout + first.stderr
    assert second.returncode != 0
    assert "already exists" in second.stderr
    assert first_hash == second_hash
    assert third.returncode == 0, third.stdout + third.stderr
