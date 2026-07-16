from __future__ import annotations

from pathlib import Path

import pytest

from scripts import compare_to_reference as comparator
from scripts._reproduction_result_validation import reproduction_result_semantic_errors
from tests.unit.compare_reference_fixtures import (
    default_target,
    load_result,
    make_compare_project,
    mark_scan_hint_blocked,
    run_compare,
)


def _graph_paths(payload: dict, scope: str) -> set[str]:
    return {
        str(entry["path"])
        for entry in payload["input_provenance"]["entries"]
        if entry["scope"] == scope
    }


def test_reproduction_snapshot_covers_consumed_project_and_schema_inputs(
    repo_root: Path,
    tmp_path: Path,
) -> None:
    project_dir = make_compare_project(tmp_path)

    completed = run_compare(repo_root, project_dir, "run-001")

    assert completed.returncode == 0, completed.stdout + completed.stderr
    payload = load_result(project_dir, "run-001")
    project_paths = _graph_paths(payload, "project")
    assert {
        "model/model-spec.json",
        "model/calc-tasks.json",
        "literature/paper-extract.json",
        "calculations/task-001/result-meta.json",
        "numerics/scan-configs/analysis-001.json",
        "numerics/scan-results/analysis-001/scan.meta.json",
        "numerics/scan-results/analysis-001/scan.csv",
    } <= project_paths
    repository_paths = _graph_paths(payload, "repository")
    assert {
        "schemas/scan-config.schema.json",
        "schemas/scan-meta.schema.json",
        "schemas/model-spec.schema.json",
        "schemas/calc-tasks.schema.json",
        "schemas/result-meta.schema.json",
        "schemas/paper-extract.schema.json",
    } <= repository_paths


def test_orchestrator_blocked_target_needs_no_scan_artifacts_or_graph_entries(
    repo_root: Path,
    tmp_path: Path,
) -> None:
    project_dir = make_compare_project(tmp_path)
    target_id = "fig-3a"
    mark_scan_hint_blocked(project_dir, target_id)
    for path in (
        project_dir / "numerics" / "scan-configs" / "analysis-001.json",
        project_dir / "numerics" / "scan-results" / "analysis-001" / "scan.csv",
        project_dir / "numerics" / "scan-results" / "analysis-001" / "scan.meta.json",
    ):
        path.unlink()

    completed = run_compare(repo_root, project_dir, "run-001")

    assert completed.returncode == 0, completed.stdout + completed.stderr
    payload = load_result(project_dir, "run-001")
    result = payload["results"][0]
    assert result["verdict"] == "blocked"
    project_paths = _graph_paths(payload, "project")
    assert not any(path.startswith("numerics/scan-") for path in project_paths)
    assert payload["depends_on"]["numerics"] == {
        "analysis_id": "analysis-001",
        "scan_meta_checksum": None,
        "scan_csv_checksum": None,
    }


def test_nonblocked_quantitative_target_cannot_bypass_missing_scan(
    repo_root: Path,
    tmp_path: Path,
) -> None:
    project_dir = make_compare_project(tmp_path)
    (project_dir / "numerics" / "scan-results" / "analysis-001" / "scan.csv").unlink()

    completed = run_compare(repo_root, project_dir, "run-001")

    assert completed.returncode == 1
    assert '"code": "artifact_missing"' in completed.stderr
    assert "numerics/scan-results/analysis-001/scan.csv" in completed.stderr
    assert not (project_dir / "reproduction" / "runs" / "run-001").exists()


@pytest.mark.parametrize("registry_state", ["unregistered", "partial"])
def test_quantitative_comparison_requires_done_manifest_analysis_owner(
    repo_root: Path,
    tmp_path: Path,
    registry_state: str,
) -> None:
    project_dir = make_compare_project(tmp_path)
    manifest_path = project_dir / "manifest.json"
    manifest = comparator.load_json(manifest_path)
    if registry_state == "unregistered":
        manifest["artifacts"]["numerics"] = {
            "status": "not_started",
            "files": [],
            "analyses": [],
            "produced_by": None,
            "timestamp": None,
        }
        manifest["history"] = [
            entry
            for entry in manifest["history"]
            if not str(entry.get("action", "")).startswith("numerics_")
        ]
    else:
        manifest["artifacts"]["numerics"]["analyses"][0]["status"] = "partial"
        manifest["artifacts"]["numerics"]["status"] = "partial"
    comparator.write_json(manifest_path, manifest)

    completed = run_compare(repo_root, project_dir, "run-001")

    assert completed.returncode == 1
    if registry_state == "unregistered":
        assert '"code": "manifest_analysis_missing"' in completed.stderr
    else:
        assert '"code": "manifest_analysis_not_done"' in completed.stderr
    assert not (project_dir / "reproduction" / "runs" / "run-001").exists()


def test_formula_only_comparison_does_not_invent_a_scan_owner_requirement(
    repo_root: Path,
    tmp_path: Path,
) -> None:
    project_dir = make_compare_project(
        tmp_path,
        targets=[default_target("eq-1", kind="formula")],
    )
    manifest_path = project_dir / "manifest.json"
    manifest = comparator.load_json(manifest_path)
    manifest["artifacts"]["numerics"] = {
        "status": "not_started",
        "files": [],
        "analyses": [],
        "produced_by": None,
        "timestamp": None,
    }
    manifest["history"] = [
        entry
        for entry in manifest["history"]
        if not str(entry.get("action", "")).startswith("numerics_")
    ]
    comparator.write_json(manifest_path, manifest)

    completed = run_compare(repo_root, project_dir, "run-001")

    assert completed.returncode == 0, completed.stdout + completed.stderr
    payload = load_result(project_dir, "run-001")
    assert payload["depends_on"]["numerics"]["scan_meta_checksum"] is None
    assert payload["depends_on"]["numerics"]["scan_csv_checksum"] is None
    published_manifest = comparator.load_json(manifest_path)
    assert published_manifest["artifacts"]["reproduction"]["depends_on"]["numerics"] == {
        "analyses": []
    }


def test_schema_invalid_scan_meta_cannot_reach_comparison(
    repo_root: Path,
    tmp_path: Path,
) -> None:
    project_dir = make_compare_project(tmp_path)
    scan_meta_path = (
        project_dir / "numerics" / "scan-results" / "analysis-001" / "scan.meta.json"
    )
    scan_meta = comparator.load_json(scan_meta_path)
    scan_meta["undeclared_field"] = "must fail"
    comparator.write_json(scan_meta_path, scan_meta)

    completed = run_compare(repo_root, project_dir, "run-001")

    assert completed.returncode == 1
    assert '"code": "schema_invalid"' in completed.stderr
    assert "scan-meta:" in completed.stderr
    assert not (project_dir / "reproduction" / "runs" / "run-001").exists()


def test_input_changed_during_metric_computation_cannot_be_posthoc_stamped(
    repo_root: Path,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    project_dir = make_compare_project(tmp_path)
    paper_extract_path = project_dir / "literature" / "paper-extract.json"
    original_compute_metrics = comparator.compute_metrics
    mutated = False

    def mutate_after_snapshot(*args, **kwargs):
        nonlocal mutated
        if not mutated:
            paper_extract_path.write_text(
                paper_extract_path.read_text(encoding="utf-8") + "\n",
                encoding="utf-8",
            )
            mutated = True
        return original_compute_metrics(*args, **kwargs)

    monkeypatch.setattr(comparator, "compute_metrics", mutate_after_snapshot)

    return_code = comparator.run(
        [
            "--project-dir",
            str(project_dir),
            "--analysis-id",
            "analysis-001",
            "--repro-id",
            "run-001",
        ]
    )

    captured = capsys.readouterr()
    assert mutated is True
    assert return_code == 1
    assert "paper-extract.json" in captured.err
    assert "current exact bytes" in captured.err
    assert not (project_dir / "reproduction" / "runs" / "run-001").exists()
    assert not (project_dir / "reproduction" / "figures" / "run-001").exists()


def test_immediately_before_publish_gate_rejects_late_input_drift(
    repo_root: Path,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    project_dir = make_compare_project(tmp_path)
    paper_extract_path = project_dir / "literature" / "paper-extract.json"
    original_write_json = comparator.write_json

    def write_then_mutate(path: Path, payload: dict) -> None:
        original_write_json(path, payload)
        if path.name == "reproduction-result.json":
            paper_extract_path.write_text(
                paper_extract_path.read_text(encoding="utf-8") + "\n",
                encoding="utf-8",
            )

    monkeypatch.setattr(comparator, "write_json", write_then_mutate)

    return_code = comparator.run(
        [
            "--project-dir",
            str(project_dir),
            "--analysis-id",
            "analysis-001",
            "--repro-id",
            "run-001",
        ]
    )

    captured = capsys.readouterr()
    assert return_code == 1
    assert "immediately before publication" in captured.err
    assert "paper-extract.json" in captured.err
    assert not (project_dir / "reproduction" / "runs" / "run-001").exists()
    assert not (project_dir / "reproduction" / "figures" / "run-001").exists()


def test_manifest_bookkeeping_change_does_not_stale_scientific_provenance(
    repo_root: Path,
    tmp_path: Path,
) -> None:
    project_dir = make_compare_project(tmp_path)
    completed = run_compare(repo_root, project_dir, "run-001")
    assert completed.returncode == 0, completed.stdout + completed.stderr
    payload = load_result(project_dir, "run-001")

    manifest_path = project_dir / "manifest.json"
    manifest = comparator.load_json(manifest_path)
    manifest["artifacts"]["reproduction"] = {
        "status": "done",
        "latest_repro_id": "run-001",
    }
    manifest["history"] = [
        {
            "action": "reproduction_run_complete",
            "repro_id": "run-001",
        }
    ]
    original_write = comparator.write_json
    original_write(manifest_path, manifest)

    errors = reproduction_result_semantic_errors(
        payload,
        project_dir=project_dir,
        expected_run_dir=(project_dir / "reproduction" / "runs" / "run-001"),
    )

    assert not errors


def test_manifest_active_model_projection_drift_invalidates_current_result(
    repo_root: Path,
    tmp_path: Path,
) -> None:
    project_dir = make_compare_project(tmp_path)
    completed = run_compare(repo_root, project_dir, "run-001")
    assert completed.returncode == 0, completed.stdout + completed.stderr
    payload = load_result(project_dir, "run-001")

    manifest_path = project_dir / "manifest.json"
    manifest = comparator.load_json(manifest_path)
    manifest["active_model_version"] = "v2"
    comparator.write_json(manifest_path, manifest)

    errors = reproduction_result_semantic_errors(
        payload,
        project_dir=project_dir,
        expected_run_dir=(project_dir / "reproduction" / "runs" / "run-001"),
    )

    assert any("manifest.active_model_version" in error for error in errors)


def test_legacy_blocked_result_does_not_invent_a_scan_dependency(
    repo_root: Path,
    tmp_path: Path,
) -> None:
    project_dir = make_compare_project(tmp_path)
    target_id = "fig-3a"
    mark_scan_hint_blocked(project_dir, target_id)
    for path in (
        project_dir / "numerics" / "scan-configs" / "analysis-001.json",
        project_dir / "numerics" / "scan-results" / "analysis-001" / "scan.csv",
        project_dir / "numerics" / "scan-results" / "analysis-001" / "scan.meta.json",
    ):
        path.unlink()
    completed = run_compare(repo_root, project_dir, "run-001")
    assert completed.returncode == 0, completed.stdout + completed.stderr
    payload = load_result(project_dir, "run-001")
    payload["input_provenance"] = {
        "version": "sha256-bytes-v1",
        "verification_status": "legacy-unverified",
        "reason": "pre-Phase-1 blocked result retained for inspection",
    }

    errors = reproduction_result_semantic_errors(
        payload,
        project_dir=project_dir,
        expected_run_dir=(project_dir / "reproduction" / "runs" / "run-001"),
    )

    assert not errors
