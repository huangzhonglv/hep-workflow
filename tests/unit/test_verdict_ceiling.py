from __future__ import annotations

import pytest

from tests.unit.compare_reference_fixtures import load_result, make_compare_project, run_compare


@pytest.mark.parametrize(
    (
        "task_type",
        "loop_order",
        "provenance",
        "include_result_meta",
        "expected_ceiling",
        "expected_verdict",
        "expected_independence",
        "expected_issue_state",
    ),
    [
        ("loop", 1, "package_x_derived", True, "pass", "pass", "independent", None),
        (
            "tree",
            0,
            "manual_tree_algebra",
            True,
            "needs_human_review",
            "needs_human_review",
            "independent_manual",
            "manual",
        ),
        (
            "loop",
            1,
            "manual_tree_algebra",
            True,
            "needs_human_review",
            "needs_human_review",
            "unknown",
            "unknown",
        ),
        (
            "loop",
            1,
            "literature_formula_imported",
            True,
            "needs_human_review",
            "needs_human_review",
            "tainted",
            "tainted",
        ),
        (
            "loop",
            1,
            "package_x_derived",
            False,
            "needs_human_review",
            "needs_human_review",
            "unknown",
            "unknown",
        ),
    ],
)
def test_verdict_ceiling_cases(
    repo_root,
    tmp_path,
    task_type,
    loop_order,
    provenance,
    include_result_meta,
    expected_ceiling,
    expected_verdict,
    expected_independence,
    expected_issue_state,
) -> None:
    project_dir = make_compare_project(
        tmp_path,
        task_type=task_type,
        loop_order=loop_order,
        provenance=provenance,
        include_result_meta=include_result_meta,
    )

    result = run_compare(repo_root, project_dir, "run-001")

    assert result.returncode == 0, result.stdout + result.stderr
    target_result = load_result(project_dir, "run-001")["results"][0]
    assert target_result["verdict_ceiling"] == expected_ceiling
    assert target_result["verdict"] == expected_verdict
    assert target_result["derivation_independence"] == expected_independence
    if expected_issue_state is None:
        assert target_result["provenance_issues"] == []
    else:
        assert target_result["provenance_issues"][0]["state"] == expected_issue_state
