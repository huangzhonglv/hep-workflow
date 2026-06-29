from __future__ import annotations

from tests.unit.compare_reference_fixtures import load_result, make_compare_project, run_compare


def test_compare_script_does_not_read_manifest_for_behavior(repo_root, tmp_path) -> None:
    project_dir = make_compare_project(tmp_path, manifest_text="")

    result = run_compare(repo_root, project_dir, "run-001")

    assert result.returncode == 0, result.stdout + result.stderr
    payload = load_result(project_dir, "run-001")
    assert payload["results"][0]["verdict"] == "pass"
