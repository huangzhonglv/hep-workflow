from __future__ import annotations

from tests.unit.compare_reference_fixtures import (
    enrich_target,
    load_result,
    make_compare_project,
    mark_scan_hint_blocked,
    run_compare,
    write_normalized_reference,
)


def scan_table_target() -> dict:
    return enrich_target({
        "id": "table-1",
        "kind": "scan_table",
        "x_param": "M_Zp",
        "y_param": "g_prime",
        "match_columns": ["M_Zp", "g_prime"],
        "observables": ["delta_a_mu"],
        "fixed": {},
        "constraints_in_paper": [],
        "data_file": "literature/digitized/table-1.csv",
        "tolerance": {"kind": "relative", "value": 0.05},
    })


def test_malformed_scan_table_artifact_fails_before_reproduction_output(
    repo_root,
    tmp_path,
) -> None:
    project_dir = make_compare_project(tmp_path, targets=[scan_table_target()])
    write_normalized_reference(
        project_dir,
        scan_table_target(),
        "M_Zp,g_prime,delta_a_mu\n1.0,2.0,2.0\n2.0,4.0,4.0\n3.0,6.0,6.0\n",
    )
    scan_csv = (
        project_dir
        / "numerics"
        / "scan-results"
        / "analysis-001"
        / "scan.csv"
    )
    scan_csv.write_text(
        "M_Zp,g_prime,other\n1,2,2\n2,4,4\n3,6,6\n",
        encoding="utf-8",
    )

    completed = run_compare(repo_root, project_dir, "run-001")

    assert completed.returncode == 1
    assert "scan artifact pair is invalid" in completed.stderr
    assert "header/order does not match" in completed.stderr
    assert not (project_dir / "reproduction" / "runs" / "run-001").exists()
    assert not (project_dir / "reproduction" / "figures" / "run-001").exists()


def test_orchestrator_blocked_scan_table_still_has_completeness_record(
    repo_root,
    tmp_path,
) -> None:
    project_dir = make_compare_project(tmp_path, targets=[scan_table_target()])
    mark_scan_hint_blocked(project_dir, "table-1")

    completed = run_compare(repo_root, project_dir, "run-001")

    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = load_result(project_dir, "run-001")["results"][0]
    completeness = result["comparison"]["completeness"]
    assert result["verdict"] == "blocked"
    assert completeness["complete"] is False
    assert completeness["blocking_reasons"] == [
        "comparison_not_run:blocked_by_orchestrator"
    ]


def test_complete_scan_table_persists_full_coverage_with_human_review_ceiling(
    repo_root,
    tmp_path,
) -> None:
    project_dir = make_compare_project(tmp_path, targets=[scan_table_target()])
    write_normalized_reference(
        project_dir,
        scan_table_target(),
        "M_Zp,g_prime,delta_a_mu\n1.0,2.0,2.0\n2.0,4.0,4.0\n3.0,6.0,6.0\n",
    )

    completed = run_compare(repo_root, project_dir, "run-001")

    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = load_result(project_dir, "run-001")["results"][0]
    completeness = result["comparison"]["completeness"]
    assert result["derivation_independence"] == "unknown"
    assert result["verdict_ceiling"] == "needs_human_review"
    assert result["verdict"] == "needs_human_review"
    assert result["comparison"]["metrics"]["n_points_compared"] == 3
    assert result["comparison"]["metrics"]["max_relative_error"] < 0.05
    assert completeness["complete"] is True
    assert completeness["row_coverage"] == 1.0
    assert completeness["value_coverage"] == 1.0
