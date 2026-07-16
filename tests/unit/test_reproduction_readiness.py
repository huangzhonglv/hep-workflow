from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scripts._reproduction_readiness import (
    derive_reproduction_readiness,
    readiness_validation_errors,
)
from scripts.compare_to_reference import validate_target_normalization
from tests.unit.compare_reference_fixtures import (
    default_target,
    load_result,
    make_compare_project,
    mark_scan_hint_blocked,
    rebind_calculation_graph,
    rebind_scan_graph,
    run_compare,
    write_json,
)


TARGET_KINDS = (
    "formula",
    "benchmark_point",
    "keyed_benchmark_set",
    "scan_table",
    "figure_curve",
    "parametric_curve",
    "exclusion_region",
)


def validate_reference(
    project_dir: Path,
    target: dict[str, object],
    paper_id: str,
) -> None:
    validate_target_normalization(project_dir, target, paper_id=paper_id)


def derive(project_dir: Path, *, target_id: str | None = None) -> dict[str, object]:
    return derive_reproduction_readiness(
        project_dir,
        "analysis-001",
        target_id=target_id,
        reference_validator=validate_reference,
    )


def file_snapshot(project_dir: Path) -> dict[str, str]:
    return {
        path.relative_to(project_dir).as_posix(): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in sorted(project_dir.rglob("*"))
        if path.is_file()
    }


@pytest.mark.parametrize("kind", TARGET_KINDS)
def test_every_target_kind_has_exact_prerequisite_matrix(tmp_path, kind) -> None:
    target = default_target(f"target-{kind.replace('_', '-')}", kind=kind)
    project_dir = make_compare_project(tmp_path, targets=[target])

    report = derive(project_dir)

    assert report["workflow_state"] == "routable"
    readiness = report["targets"][0]
    assert readiness["kind"] == kind
    assert readiness["disposition"] == "ready"
    requirements = readiness["requirements"]
    assert requirements["literature"] == {
        "required": True,
        "status": "ready",
        "issues": [],
    }
    if kind == "formula":
        assert requirements["model"]["status"] == "not_applicable"
        assert requirements["calculations"] == {
            "required": False,
            "status": "not_applicable",
            "issues": [],
            "task_ids": [],
        }
        assert requirements["numerics"]["status"] == "not_applicable"
    else:
        assert requirements["model"]["status"] == "ready"
        assert requirements["calculations"]["status"] == "ready"
        assert requirements["calculations"]["task_ids"] == ["task-001"]
        assert requirements["numerics"]["status"] == "ready"


def test_missing_numeric_calculation_result_is_not_ready_and_comparator_fails_closed(
    repo_root,
    tmp_path,
) -> None:
    project_dir = make_compare_project(
        tmp_path,
        targets=[default_target("fig-3a", kind="figure_curve")],
        include_result_meta=False,
    )

    report = derive(project_dir)

    target = report["targets"][0]
    assert target["disposition"] == "not_ready"
    calculations = target["requirements"]["calculations"]
    assert calculations["status"] == "missing"
    assert calculations["task_ids"] == ["task-001"]
    assert [issue["code"] for issue in calculations["issues"]] == [
        "calculation_result_missing"
    ]

    completed = run_compare(repo_root, project_dir, "run-001")
    assert completed.returncode == 1
    assert "selected reproduction targets are not ready" in completed.stderr
    assert "calculation_result_missing" in completed.stderr
    assert not (project_dir / "reproduction" / "runs" / "run-001").exists()


def test_blocked_scan_hint_is_typed_and_needs_no_cli_authority(
    repo_root,
    tmp_path,
) -> None:
    project_dir = make_compare_project(tmp_path)
    mark_scan_hint_blocked(project_dir, "fig-3a")

    report = derive(project_dir)

    target = report["targets"][0]
    assert target["disposition"] == "blocked"
    numerics = target["requirements"]["numerics"]
    assert numerics["status"] == "blocked"
    assert numerics["issues"][0]["code"] == "scan_hint_incomplete"

    completed = run_compare(repo_root, project_dir, "run-001")
    assert completed.returncode == 0, completed.stdout + completed.stderr
    persisted = load_result(project_dir, "run-001")
    assert persisted["results"][0]["verdict"] == "blocked"


def test_duplicate_compatibility_blockers_fail_before_publication(
    repo_root,
    tmp_path,
) -> None:
    project_dir = make_compare_project(tmp_path)
    mark_scan_hint_blocked(project_dir, "fig-3a")

    completed = run_compare(
        repo_root,
        project_dir,
        "run-001",
        "--blocked-targets",
        "fig-3a,fig-3a",
    )

    assert completed.returncode == 1
    assert "duplicate target ids" in completed.stderr
    assert not (project_dir / "reproduction" / "runs" / "run-001").exists()


@pytest.mark.parametrize(
    ("requested", "expected_error"),
    [
        ("unknown-target", "ids not declared"),
        ("fig-5b", "must exactly match the typed readiness report"),
    ],
)
def test_compatibility_blockers_cannot_invent_or_clear_typed_state(
    repo_root,
    tmp_path,
    requested,
    expected_error,
) -> None:
    project_dir = make_compare_project(
        tmp_path,
        targets=[default_target("fig-3a"), default_target("fig-5b")],
    )
    mark_scan_hint_blocked(project_dir, "fig-3a")

    completed = run_compare(
        repo_root,
        project_dir,
        "run-001",
        "--blocked-targets",
        requested,
    )

    assert completed.returncode == 1
    assert expected_error in completed.stderr
    assert not (project_dir / "reproduction" / "runs" / "run-001").exists()


def test_stale_calculation_dependency_routes_back_to_calculations(
    repo_root,
    tmp_path,
) -> None:
    project_dir = make_compare_project(tmp_path)
    python_path = project_dir / "calculations" / "task-001" / "result-python.py"
    python_path.write_text(
        python_path.read_text(encoding="utf-8") + "\n# stale mutation\n",
        encoding="utf-8",
    )

    report = derive(project_dir)

    calculations = report["targets"][0]["requirements"]["calculations"]
    assert calculations["status"] == "stale"
    assert calculations["issues"][0]["code"] == "calculation_dependency_stale"
    completed = run_compare(repo_root, project_dir, "run-001")
    assert completed.returncode == 1
    assert "calculation_dependency_stale" in completed.stderr


def test_blocked_numerics_cannot_hide_wrong_calculation_model_identity(
    repo_root,
    tmp_path,
) -> None:
    project_dir = make_compare_project(tmp_path)
    mark_scan_hint_blocked(project_dir, "fig-3a")
    meta_path = project_dir / "calculations" / "task-001" / "result-meta.json"
    metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    metadata["depends_on"]["model_version"] = "v999"
    write_json(meta_path, metadata)

    report = derive(project_dir)

    readiness = report["targets"][0]
    assert readiness["disposition"] == "not_ready"
    calculations = readiness["requirements"]["calculations"]
    assert calculations["status"] == "invalid"
    assert [issue["code"] for issue in calculations["issues"]] == [
        "calculation_result_invalid"
    ]
    assert readiness["requirements"]["numerics"]["status"] == "blocked"
    completed = run_compare(repo_root, project_dir, "run-001")
    assert completed.returncode == 1
    assert "model dependency does not match" in completed.stderr


def test_missing_scan_artifact_routes_back_to_numerics(
    repo_root,
    tmp_path,
) -> None:
    project_dir = make_compare_project(tmp_path)
    (project_dir / "numerics" / "scan-results" / "analysis-001" / "scan.csv").unlink()

    report = derive(project_dir)

    numerics = report["targets"][0]["requirements"]["numerics"]
    assert numerics["status"] == "missing"
    assert [issue["code"] for issue in numerics["issues"]] == ["artifact_missing"]
    completed = run_compare(repo_root, project_dir, "run-001")
    assert completed.returncode == 1
    assert "artifact_missing" in completed.stderr
    assert not (project_dir / "reproduction").exists()


def test_stale_scan_dependency_routes_back_to_numerics(
    repo_root,
    tmp_path,
) -> None:
    project_dir = make_compare_project(tmp_path)
    constraints_path = project_dir / "constraints" / "constraints-data.json"
    constraints = json.loads(constraints_path.read_text(encoding="utf-8"))
    constraints["constraints"][0]["notes"] = "stale mutation"
    write_json(constraints_path, constraints)

    report = derive(project_dir)

    numerics = report["targets"][0]["requirements"]["numerics"]
    assert numerics["status"] == "stale"
    assert [issue["code"] for issue in numerics["issues"]] == [
        "scan_dependency_stale"
    ]
    completed = run_compare(repo_root, project_dir, "run-001")
    assert completed.returncode == 1
    assert "scan_dependency_stale" in completed.stderr


def test_invalid_reference_evidence_fails_closed_before_publication(
    repo_root,
    tmp_path,
) -> None:
    project_dir = make_compare_project(tmp_path)
    canonical = project_dir / "literature" / "digitized" / "fig-3a.csv"
    canonical.write_text(
        canonical.read_text(encoding="utf-8") + "4.0,8.008\n",
        encoding="utf-8",
    )

    report = derive(project_dir)

    literature = report["targets"][0]["requirements"]["literature"]
    assert literature["status"] == "invalid"
    assert [issue["code"] for issue in literature["issues"]] == [
        "reference_evidence_invalid"
    ]
    completed = run_compare(repo_root, project_dir, "run-001")
    assert completed.returncode == 1
    assert "reference_evidence_invalid" in completed.stderr
    assert not (project_dir / "reproduction").exists()


def test_invalid_model_identity_routes_back_to_model(
    repo_root,
    tmp_path,
) -> None:
    project_dir = make_compare_project(tmp_path)
    manifest_path = project_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts"]["model"]["checksum"] = "sha256:" + "f" * 64
    write_json(manifest_path, manifest)

    report = derive(project_dir)

    model = report["targets"][0]["requirements"]["model"]
    assert model["status"] == "invalid"
    assert [issue["code"] for issue in model["issues"]] == [
        "model_identity_mismatch"
    ]
    completed = run_compare(repo_root, project_dir, "run-001")
    assert completed.returncode == 1
    assert "model_identity_mismatch" in completed.stderr


def test_manifest_analysis_must_own_the_scan_it_exposes(
    repo_root,
    tmp_path,
) -> None:
    project_dir = make_compare_project(tmp_path)
    manifest_path = project_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts"]["numerics"]["analyses"][0]["files"].remove(
        "numerics/scan-results/analysis-001/scan.csv"
    )
    write_json(manifest_path, manifest)

    report = derive(project_dir)

    numerics = report["targets"][0]["requirements"]["numerics"]
    assert numerics["status"] == "invalid"
    assert {
        issue["code"] for issue in numerics["issues"]
    } == {"manifest_analysis_invalid"}
    assert any("does not own required files" in issue["detail"] for issue in numerics["issues"])
    completed = run_compare(repo_root, project_dir, "run-001")
    assert completed.returncode == 1
    assert "manifest_analysis_invalid" in completed.stderr


def test_formula_only_comparison_does_not_consume_model_calculation_or_scan(
    repo_root,
    tmp_path,
) -> None:
    project_dir = make_compare_project(
        tmp_path,
        targets=[default_target("eq-1", kind="formula")],
    )
    for directory in ("model", "calculations", "constraints", "numerics"):
        shutil.rmtree(project_dir / directory)
    manifest_path = project_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["active_model_version"] = None
    manifest["artifacts"]["model"] = {
        "status": "not_started",
        "version": None,
        "files": [],
        "checksum": None,
        "produced_by": None,
        "timestamp": None,
    }
    for name in ("calculations", "constraints"):
        manifest["artifacts"][name]["status"] = "not_started"
        manifest["artifacts"][name]["produced_by"] = None
        manifest["artifacts"][name]["timestamp"] = None
        manifest["artifacts"][name]["depends_on"]["model"] = {
            "version": None,
            "checksum": None,
        }
    manifest["artifacts"]["calculations"]["completed_tasks"] = []
    manifest["artifacts"]["calculations"]["pending_tasks"] = []
    manifest["artifacts"]["constraints"]["files"] = []
    manifest["artifacts"]["numerics"] = {
        "status": "not_started",
        "files": [],
        "analyses": [],
        "produced_by": None,
        "timestamp": None,
    }
    manifest["history"] = []
    write_json(manifest_path, manifest)

    report = derive(project_dir)
    readiness = report["targets"][0]
    assert readiness["disposition"] == "ready"
    assert readiness["requirements"]["model"]["status"] == "not_applicable"

    completed = run_compare(
        repo_root,
        project_dir,
        "run-001",
        "--target-id",
        "eq-1",
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    persisted = load_result(project_dir, "run-001")
    assert persisted["depends_on"]["model"] == {
        "version": None,
        "checksum": None,
    }
    assert persisted["depends_on"]["calculations"] == {
        "tasks": [],
        "model_version": None,
    }
    result = persisted["results"][0]
    assert result["tasks_used"] == []
    assert result["provenance_issues"] == [
        {"state": "unknown", "reason": "formula_reference_only"}
    ]
    assert result["verdict"] == "needs_human_review"

    workspace_validation = subprocess.run(
        [
            sys.executable,
            str(repo_root / "scripts" / "validate_workspace_projects.py"),
            "--workspace-root",
            str(project_dir.parent),
            project_dir.name,
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    assert workspace_validation.returncode == 0, (
        workspace_validation.stdout + workspace_validation.stderr
    )


def test_mixed_formula_and_numeric_targets_use_only_kind_required_evidence(
    repo_root,
    tmp_path,
) -> None:
    project_dir = make_compare_project(
        tmp_path,
        targets=[
            default_target("eq-1", kind="formula"),
            default_target("fig-3a", kind="figure_curve"),
        ],
    )

    report = derive(project_dir)
    by_id = {item["target_id"]: item for item in report["targets"]}
    assert by_id["eq-1"]["requirements"]["model"]["status"] == "not_applicable"
    assert by_id["fig-3a"]["requirements"]["model"]["status"] == "ready"

    completed = run_compare(repo_root, project_dir, "run-001")
    assert completed.returncode == 0, completed.stdout + completed.stderr
    persisted = load_result(project_dir, "run-001")
    results = {item["target_id"]: item for item in persisted["results"]}
    assert results["eq-1"]["tasks_used"] == []
    assert results["eq-1"]["provenance_issues"] == [
        {"state": "unknown", "reason": "formula_reference_only"}
    ]
    assert results["fig-3a"]["tasks_used"] == ["task-001"]
    assert persisted["depends_on"]["model"]["version"] == "v1"
    assert persisted["depends_on"]["calculations"] == {
        "tasks": ["task-001"],
        "model_version": "v1",
    }
    assert persisted["depends_on"]["numerics"]["scan_csv_checksum"].startswith(
        "sha256:"
    )


def test_mixed_run_does_not_bind_formula_only_calculation_task(
    repo_root,
    tmp_path,
) -> None:
    formula = default_target("eq-1", kind="formula")
    formula["observables"] = ["formula_aux"]
    project_dir = make_compare_project(
        tmp_path,
        targets=[formula, default_target("fig-3a", kind="figure_curve")],
    )
    calc_tasks_path = project_dir / "model" / "calc-tasks.json"
    calc_tasks = json.loads(calc_tasks_path.read_text(encoding="utf-8"))
    second_task = json.loads(json.dumps(calc_tasks["tasks"][0]))
    second_task["task_id"] = "task-002"
    second_task["title"] = "Formula-only auxiliary task"
    second_task["target_quantity"] = "formula_aux"
    calc_tasks["tasks"].append(second_task)
    write_json(calc_tasks_path, calc_tasks)

    task_001 = project_dir / "calculations" / "task-001"
    task_002 = project_dir / "calculations" / "task-002"
    shutil.copytree(task_001, task_002)
    meta_path = task_002 / "result-meta.json"
    metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    metadata["task_id"] = "task-002"
    metadata["observable"] = "formula_aux"
    metadata["return_value"]["name"] = "formula_aux"
    metadata["derivation_evidence"]["observable"] = "formula_aux"
    write_json(meta_path, metadata)
    rebind_calculation_graph(project_dir, "task-001")
    rebind_calculation_graph(project_dir, "task-002")
    rebind_scan_graph(project_dir)

    completed = run_compare(repo_root, project_dir, "run-001")

    assert completed.returncode == 0, completed.stdout + completed.stderr
    persisted = load_result(project_dir, "run-001")
    project_graph_paths = {
        entry["path"]
        for entry in persisted["input_provenance"]["entries"]
        if entry["scope"] == "project"
    }
    assert not any(
        path.startswith("calculations/task-002/") for path in project_graph_paths
    )
    assert persisted["depends_on"]["calculations"]["tasks"] == ["task-001"]


def test_readiness_cli_is_deterministic_and_does_not_write_project_files(
    repo_root,
    tmp_path,
) -> None:
    project_dir = make_compare_project(tmp_path)
    before = file_snapshot(project_dir)
    command = [
        sys.executable,
        str(repo_root / "scripts" / "check_reproduction_readiness.py"),
        "--project-dir",
        str(project_dir),
        "--analysis-id",
        "analysis-001",
    ]

    first = subprocess.run(command, cwd=repo_root, capture_output=True, text=True)
    second = subprocess.run(command, cwd=repo_root, capture_output=True, text=True)

    assert first.returncode == second.returncode == 0
    assert first.stdout == second.stdout
    assert first.stderr == second.stderr == ""
    assert file_snapshot(project_dir) == before
    payload = json.loads(first.stdout)
    assert readiness_validation_errors(payload) == []


def test_readiness_cli_returns_typed_not_ready_as_routing_data(
    repo_root,
    tmp_path,
) -> None:
    project_dir = make_compare_project(tmp_path, include_result_meta=False)
    before = file_snapshot(project_dir)

    completed = subprocess.run(
        [
            sys.executable,
            str(repo_root / "scripts" / "check_reproduction_readiness.py"),
            "--project-dir",
            str(project_dir),
            "--analysis-id",
            "analysis-001",
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["workflow_state"] == "not_ready"
    assert payload["summary"]["not_ready"] == 1
    assert completed.stderr == ""
    assert file_snapshot(project_dir) == before


def test_readiness_semantics_reject_summary_and_formula_requirement_drift(
    tmp_path,
) -> None:
    project_dir = make_compare_project(
        tmp_path,
        targets=[default_target("eq-1", kind="formula")],
    )
    report = derive(project_dir)
    report["summary"]["ready"] = 0
    report["targets"][0]["requirements"]["model"] = {
        "required": True,
        "status": "ready",
        "issues": [],
    }

    errors = readiness_validation_errors(report)

    assert "summary does not exactly match target dispositions" in errors
    assert any("must be not_applicable for formula targets" in error for error in errors)


def test_readiness_semantics_reject_not_applicable_numeric_requirement(
    tmp_path,
) -> None:
    project_dir = make_compare_project(tmp_path)
    report = derive(project_dir)
    report["targets"][0]["requirements"]["model"] = {
        "required": True,
        "status": "not_applicable",
        "issues": [],
    }
    report["targets"][0]["requirements"]["literature"] = {
        "required": True,
        "status": "not_applicable",
        "issues": [],
    }

    errors = readiness_validation_errors(report)

    assert any(
        "model cannot be not_applicable for numeric targets" in error
        for error in errors
    )
    assert any("literature cannot be not_applicable" in error for error in errors)
