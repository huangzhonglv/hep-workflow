from __future__ import annotations

import json

from tests.unit.compare_reference_fixtures import (
    default_target,
    hash_file,
    load_result,
    make_compare_project,
    run_compare,
    write_json,
)


def test_parametric_curve_runs_through_persisted_comparison_contract(
    repo_root,
    tmp_path,
) -> None:
    target = default_target("parametric-1", kind="parametric_curve")
    project_dir = make_compare_project(tmp_path, targets=[target])

    completed = run_compare(repo_root, project_dir, "run-001")

    assert completed.returncode == 0, completed.stderr
    result = load_result(project_dir, "run-001")
    target_result = result["results"][0]
    assert target_result["comparison"]["kind"] == "parametric_curve"
    assert target_result["comparison"]["geometry_method"] == (
        "normalized_continuous_polyline_hausdorff"
    )
    assert target_result["comparison"]["metrics"]["reference_domain_coverage"] == 1.0
    assert target_result["comparison"]["metrics"]["distance_decision_defined"] == 1
    assert target_result["verdict"] == "needs_human_review"
    assert target_result["verdict_ceiling"] == "needs_human_review"


def test_parametric_curve_missing_endpoint_persists_blocked_not_partial_metric(
    repo_root,
    tmp_path,
) -> None:
    target = default_target("parametric-gap", kind="parametric_curve")
    project_dir = make_compare_project(tmp_path, targets=[target])
    canonical = project_dir / target["data_file"]
    raw = project_dir / target["normalization"]["source_data_file"]
    incomplete = "M_Zp,delta_a_mu\n1.0,2.002\n2.0,4.004\n"
    canonical.write_text(incomplete, encoding="utf-8")
    raw.write_text(incomplete, encoding="utf-8")
    record_path = project_dir / target["normalization"]["record_file"]
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["source_checksum"] = hash_file(raw)
    record["canonical_checksum"] = hash_file(canonical)
    write_json(record_path, record)

    completed = run_compare(repo_root, project_dir, "run-001")

    assert completed.returncode == 0, completed.stderr
    result = load_result(project_dir, "run-001")
    target_result = result["results"][0]
    assert target_result["verdict"] == "blocked"
    assert target_result["comparison"]["metrics"] == {}
    assert any(
        "exactly cover parameter_domain endpoints" in warning
        for warning in target_result["warnings"]
    )


def test_implicit_projection_field_fails_schema_before_run_publication(
    repo_root,
    tmp_path,
) -> None:
    target = default_target("parametric-projection", kind="parametric_curve")
    target["projection"] = {"kind": "any"}
    project_dir = make_compare_project(tmp_path, targets=[target])

    completed = run_compare(repo_root, project_dir, "run-001")

    assert completed.returncode == 1
    assert "repro-targets.json failed schema validation" in completed.stderr
    assert not (project_dir / "reproduction" / "runs" / "run-001").exists()
