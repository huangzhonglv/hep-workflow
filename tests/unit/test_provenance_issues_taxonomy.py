from __future__ import annotations

import pytest

from tests.unit.compare_reference_fixtures import (
    ENUM_REASONS,
    default_target,
    load_result,
    make_compare_project,
    run_compare,
)


@pytest.mark.parametrize(
    ("task_type", "loop_order", "provenance", "benchmark_used", "include_meta", "expected_reason"),
    [
        ("tree", 0, "manual_tree_algebra", False, True, "manual_tree_algebra_on_tree_task"),
        ("loop", 1, "literature_formula_imported", False, True, "literature_formula_imported"),
        ("loop", 1, "package_x_derived", True, True, "benchmark_used_as_input"),
        ("loop", 1, "manual_tree_algebra", False, True, "unsupported_manual_loop"),
        ("loop", 1, "package_x_derived", False, False, "formula_reference_only"),
        ("loop", 1, "blocked", False, True, "provenance_blocked"),
    ],
)
def test_provenance_issue_reasons_are_schema_enum_tokens(
    repo_root,
    tmp_path,
    task_type,
    loop_order,
    provenance,
    benchmark_used,
    include_meta,
    expected_reason,
) -> None:
    project_dir = make_compare_project(
        tmp_path,
        task_type=task_type,
        loop_order=loop_order,
        provenance=provenance,
        benchmark_used_as_input=benchmark_used,
        include_result_meta=include_meta,
        targets=None if include_meta else [default_target(kind="formula")],
    )

    result = run_compare(repo_root, project_dir, "run-001")

    assert result.returncode == 0, result.stdout + result.stderr
    issues = load_result(project_dir, "run-001")["results"][0]["provenance_issues"]
    assert issues
    assert issues[0]["reason"] == expected_reason
    assert issues[0]["reason"] in ENUM_REASONS
    assert " " not in issues[0]["reason"]
