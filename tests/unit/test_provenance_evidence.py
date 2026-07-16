from __future__ import annotations

import json

from tests.unit.compare_reference_fixtures import (
    default_target,
    hash_file,
    load_result,
    make_compare_project,
    mark_scan_hint_blocked,
    rebind_calculation_graph,
    rebind_scan_graph,
    run_compare,
    write_json,
)


def test_compare_blocks_when_result_meta_cannot_authorize_observable_unit(
    repo_root, tmp_path
) -> None:
    project_dir = make_compare_project(tmp_path)
    meta_path = project_dir / "calculations" / "task-001" / "result-meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta.pop("depends_on")
    write_json(meta_path, meta)

    completed = run_compare(repo_root, project_dir, "run-001")

    assert completed.returncode == 1
    assert '"code": "calculation_result_invalid"' in completed.stderr
    assert not (project_dir / "reproduction" / "runs" / "run-001").exists()


def test_compare_does_not_accept_package_x_name_in_comment(repo_root, tmp_path) -> None:
    project_dir = make_compare_project(tmp_path)
    source = project_dir / "calculations" / "task-001" / "result.wl"
    source.write_text(
        "(* LoopIntegrate[numerator, k, {k, mass}] *)\n"
        "result = importedFormula;\n",
        encoding="utf-8",
    )
    meta_path = project_dir / "calculations" / "task-001" / "result-meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["derivation_evidence"]["source_wl_sha256"] = hash_file(source)
    write_json(meta_path, meta)
    rebind_calculation_graph(project_dir)
    rebind_scan_graph(project_dir)

    completed = run_compare(repo_root, project_dir, "run-001")

    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = load_result(project_dir, "run-001")["results"][0]
    assert result["derivation_independence"] == "unknown"
    assert result["verdict"] != "pass"
    assert result["provenance_issues"][0]["reason"] == "derivation_artifacts_unverified"


def test_compare_routes_unmapped_target_observable_back_to_calculations(
    repo_root, tmp_path
) -> None:
    project_dir = make_compare_project(tmp_path)
    targets_path = project_dir / "literature" / "repro-targets.json"
    targets = json.loads(targets_path.read_text(encoding="utf-8"))
    targets["targets"][0]["observables"].append("unmapped_observable")
    write_json(targets_path, targets)

    completed = run_compare(repo_root, project_dir, "run-001")

    assert completed.returncode == 1
    assert '"code": "observable_task_unmatched"' in completed.stderr
    assert "unmapped_observable" in completed.stderr
    assert not (project_dir / "reproduction" / "runs" / "run-001").exists()


def test_formula_only_comparison_requires_structured_formula_reference(
    repo_root, tmp_path
) -> None:
    target = default_target(kind="formula")
    project_dir = make_compare_project(tmp_path, targets=[target])
    (project_dir / target["data_file"]).unlink()
    scan_dir = project_dir / "numerics" / "scan-results" / "analysis-001"
    for path in scan_dir.iterdir():
        path.unlink()
    scan_dir.rmdir()

    completed = run_compare(repo_root, project_dir, "run-001")

    assert completed.returncode == 1
    combined_output = completed.stdout + completed.stderr
    assert "formula reference is missing or empty" in combined_output
    assert not (project_dir / "reproduction" / "runs" / "run-001").exists()
    assert not (project_dir / "reproduction" / "figures" / "run-001").exists()


def test_orchestrator_block_preserves_conservative_provenance_ceiling(
    repo_root, tmp_path
) -> None:
    project_dir = make_compare_project(tmp_path)
    mark_scan_hint_blocked(project_dir, "fig-3a")

    completed = run_compare(repo_root, project_dir, "run-001")

    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = load_result(project_dir, "run-001")["results"][0]
    assert result["derivation_independence"] == "unknown"
    assert result["verdict_ceiling"] == "needs_human_review"
    assert result["verdict"] == "blocked"
    assert result["provenance_issues"][0]["reason"] == "derivation_evidence_not_runtime_verified"
    assert set(result["generated_files"]) == {"overlay"}
