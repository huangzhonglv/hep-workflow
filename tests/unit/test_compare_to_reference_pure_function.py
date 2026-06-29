from __future__ import annotations

from tests.unit.compare_reference_fixtures import load_result, make_compare_project, run_compare


def comparable_fields(payload):
    return [
        {
            "target_id": result["target_id"],
            "metrics": result["comparison"]["metrics"],
            "verdict": result["verdict"],
            "verdict_ceiling": result["verdict_ceiling"],
            "derivation_independence": result["derivation_independence"],
            "provenance_issues": result["provenance_issues"],
        }
        for result in payload["results"]
    ]


def test_compare_to_reference_deterministic_fields(repo_root, tmp_path) -> None:
    project_dir = make_compare_project(tmp_path)

    first = run_compare(repo_root, project_dir, "run-001")
    second = run_compare(repo_root, project_dir, "run-002")

    assert first.returncode == 0, first.stdout + first.stderr
    assert second.returncode == 0, second.stdout + second.stderr
    assert comparable_fields(load_result(project_dir, "run-001")) == comparable_fields(
        load_result(project_dir, "run-002")
    )
