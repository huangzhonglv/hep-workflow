from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scripts._publication_transaction import capture_identity


def _run_initializer(
    repo_root: Path,
    project_dir: Path,
    *,
    owner: str,
    mode: str,
) -> dict[str, str]:
    result = subprocess.run(
        [
            sys.executable,
            str(repo_root / "scripts" / "init_foundation_attempt.py"),
            "--project-dir",
            str(project_dir),
            "--owner",
            owner,
            "--mode",
            mode,
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def _run_finalizer(
    repo_root: Path,
    project_dir: Path,
    allocation: dict[str, str],
    *,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    process_env = os.environ.copy()
    process_env.update(env or {})
    return subprocess.run(
        [
            sys.executable,
            str(repo_root / "scripts" / "finalize_foundation_attempt.py"),
            "--project-dir",
            str(project_dir),
            "--attempt-dir",
            allocation["attempt_dir"],
            "--attempt-id",
            allocation["attempt_id"],
            "--owner",
            allocation["owner"],
            "--mode",
            allocation["mode"],
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
        env=process_env,
    )


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _copy_project(repo_root: Path, tmp_path: Path, name: str) -> Path:
    source = (
        repo_root
        / "tests"
        / "fixtures"
        / "workspace-projects"
        / "numerics-contract"
    )
    destination = tmp_path / "workspace" / "projects" / name
    shutil.copytree(source, destination)
    manifest_path = destination / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["project_name"] = name
    _write_json(manifest_path, manifest)
    return destination


def _prepare_constraints_revision(
    repo_root: Path,
    project_dir: Path,
) -> dict[str, str]:
    allocation = _run_initializer(
        repo_root,
        project_dir,
        owner="hep-idea",
        mode="revise",
    )
    candidate_dir = Path(allocation["candidate_dir"])
    constraints_path = candidate_dir / "constraints" / "constraints-data.json"
    constraints = json.loads(constraints_path.read_text(encoding="utf-8"))
    constraints["constraints"][0]["notes"] += " Foundation revision."
    _write_json(constraints_path, constraints)

    manifest_path = candidate_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    timestamp = "2026-07-15T00:00:00Z"
    manifest["last_updated"] = "2026-07-15"
    manifest["artifacts"]["constraints"]["produced_by"] = "hep-idea"
    manifest["artifacts"]["constraints"]["timestamp"] = timestamp
    manifest["history"].append(
        {
            "action": "constraints_updated",
            "timestamp": timestamp,
            "by": "hep-idea",
            "note": "Test-only foundation revision.",
        }
    )
    _write_json(manifest_path, manifest)
    return allocation


def _prepare_model_revision(
    repo_root: Path,
    project_dir: Path,
) -> dict[str, str]:
    allocation = _run_initializer(
        repo_root,
        project_dir,
        owner="hep-idea",
        mode="revise",
    )
    candidate_dir = Path(allocation["candidate_dir"])
    model_spec_path = candidate_dir / "model" / "model-spec.json"
    model_spec = json.loads(model_spec_path.read_text(encoding="utf-8"))
    model_spec["version"] = "v2"
    model_spec["tags"].append("foundation-revision")
    _write_json(model_spec_path, model_spec)

    calc_tasks_path = candidate_dir / "model" / "calc-tasks.json"
    calc_tasks = json.loads(calc_tasks_path.read_text(encoding="utf-8"))
    calc_tasks["model_version"] = "v2"
    _write_json(calc_tasks_path, calc_tasks)

    constraints_path = candidate_dir / "constraints" / "constraints-data.json"
    constraints = json.loads(constraints_path.read_text(encoding="utf-8"))
    constraints["model_version"] = "v2"
    _write_json(constraints_path, constraints)

    checksum = "sha256:" + hashlib.sha256(model_spec_path.read_bytes()).hexdigest()
    manifest_path = candidate_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    timestamp = "2026-07-15T00:00:00Z"
    manifest["last_updated"] = "2026-07-15"
    manifest["active_model_version"] = "v2"
    manifest["artifacts"]["model"].update(
        {
            "version": "v2",
            "checksum": checksum,
            "produced_by": "hep-idea",
            "timestamp": timestamp,
        }
    )
    manifest["artifacts"]["constraints"].update(
        {
            "depends_on": {
                "model": {"version": "v2", "checksum": checksum}
            },
            "produced_by": "hep-idea",
            "timestamp": timestamp,
        }
    )
    manifest["history"].extend(
        [
            {
                "action": "model_complete_v2",
                "timestamp": timestamp,
                "by": "hep-idea",
                "note": "Test-only model revision.",
            },
            {
                "action": "constraints_updated",
                "timestamp": timestamp,
                "by": "hep-idea",
                "note": "Rebound constraints to test model revision.",
            },
        ]
    )
    _write_json(manifest_path, manifest)
    return allocation


def _prepare_calc_tasks_revision(
    repo_root: Path,
    project_dir: Path,
) -> dict[str, str]:
    allocation = _run_initializer(
        repo_root,
        project_dir,
        owner="hep-idea",
        mode="revise",
    )
    candidate_dir = Path(allocation["candidate_dir"])
    calc_tasks_path = candidate_dir / "model" / "calc-tasks.json"
    calc_tasks = json.loads(calc_tasks_path.read_text(encoding="utf-8"))
    calc_tasks["tasks"][0]["description"] += " Foundation task revision."
    _write_json(calc_tasks_path, calc_tasks)

    manifest_path = candidate_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    timestamp = "2026-07-15T00:00:00Z"
    manifest["last_updated"] = "2026-07-15"
    manifest["artifacts"]["model"]["produced_by"] = "hep-idea"
    manifest["artifacts"]["model"]["timestamp"] = timestamp
    manifest["history"].append(
        {
            "action": "model_updated",
            "timestamp": timestamp,
            "by": "hep-idea",
            "note": "Test-only calculation-task revision.",
        }
    )
    _write_json(manifest_path, manifest)
    return allocation


def _prepare_benchmarks_revision(
    repo_root: Path,
    project_dir: Path,
) -> dict[str, str]:
    allocation = _run_initializer(
        repo_root,
        project_dir,
        owner="hep-idea",
        mode="revise",
    )
    candidate_dir = Path(allocation["candidate_dir"])
    benchmarks_path = candidate_dir / "model" / "benchmarks.json"
    benchmarks = json.loads(benchmarks_path.read_text(encoding="utf-8"))
    benchmarks["benchmarks"][0]["notes"] += " Foundation benchmark revision."
    _write_json(benchmarks_path, benchmarks)

    manifest_path = candidate_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    timestamp = "2026-07-15T00:00:00Z"
    manifest["last_updated"] = "2026-07-15"
    manifest["artifacts"]["model"]["produced_by"] = "hep-idea"
    manifest["artifacts"]["model"]["timestamp"] = timestamp
    manifest["history"].append(
        {
            "action": "benchmarks_updated",
            "timestamp": timestamp,
            "by": "hep-idea",
            "note": "Test-only benchmark revision.",
        }
    )
    _write_json(manifest_path, manifest)
    return allocation


def _validate_project(repo_root: Path, project_dir: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(repo_root / "scripts" / "validate_workspace_projects.py"),
            project_dir.name,
            "--workspace-root",
            str(project_dir.parent),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _prepare_fresh_hep_idea_candidate(
    repo_root: Path,
    project_dir: Path,
) -> dict[str, str]:
    allocation = _run_initializer(
        repo_root,
        project_dir,
        owner="hep-idea",
        mode="initialize",
    )
    candidate_dir = Path(allocation["candidate_dir"])
    fixture = (
        repo_root
        / "tests"
        / "fixtures"
        / "workspace-projects"
        / "numerics-contract"
    )
    shutil.copy2(fixture / "idea" / "proposal.md", candidate_dir / "idea")
    for filename in ("model-spec.json", "calc-tasks.json", "benchmarks.json"):
        shutil.copy2(fixture / "model" / filename, candidate_dir / "model")
    shutil.copy2(
        fixture / "constraints" / "constraints-data.json",
        candidate_dir / "constraints",
    )
    (candidate_dir / "constraints" / "constraints-summary.md").write_text(
        "# Constraints\n\nSynthetic publication fixture.\n",
        encoding="utf-8",
    )

    manifest = json.loads(
        (
            repo_root
            / ".claude"
            / "skills"
            / "hep-idea"
            / "templates"
            / "manifest.example.json"
        ).read_text(encoding="utf-8")
    )
    timestamp = "2026-07-15T00:00:00Z"
    manifest["project_name"] = project_dir.name
    manifest["created"] = "2026-07-15"
    manifest["last_updated"] = "2026-07-15"
    checksum = "sha256:" + hashlib.sha256(
        (candidate_dir / "model" / "model-spec.json").read_bytes()
    ).hexdigest()
    manifest["artifacts"]["model"]["checksum"] = checksum
    manifest["artifacts"]["constraints"]["depends_on"]["model"][
        "checksum"
    ] = checksum
    for name in ("idea", "model", "constraints"):
        manifest["artifacts"][name]["timestamp"] = timestamp
    for event in manifest["history"]:
        event["timestamp"] = timestamp
    _write_json(candidate_dir / "manifest.json", manifest)
    return allocation


def test_fresh_hep_idea_foundation_is_published_as_one_valid_generation(
    repo_root: Path,
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace" / "projects"
    initialized = subprocess.run(
        [
            sys.executable,
            str(
                repo_root
                / ".agents"
                / "skills"
                / "hep-idea"
                / "scripts"
                / "init_project_skeleton.py"
            ),
            "foundation-fresh",
            "--workspace-root",
            str(workspace_root),
        ],
        capture_output=True,
        text=True,
    )
    assert initialized.returncode == 0, initialized.stdout + initialized.stderr
    project_dir = workspace_root / "foundation-fresh"
    allocation = _prepare_fresh_hep_idea_candidate(repo_root, project_dir)

    published = _run_finalizer(repo_root, project_dir, allocation)

    assert published.returncode == 0, published.stdout + published.stderr
    assert json.loads(published.stdout)["status"] == "published"
    _validate_project(repo_root, project_dir)
    manifest_before_retry = (project_dir / "manifest.json").read_bytes()

    retried = _run_finalizer(repo_root, project_dir, allocation)

    assert retried.returncode == 0, retried.stdout + retried.stderr
    assert json.loads(retried.stdout)["status"] == "already_published"
    assert (project_dir / "manifest.json").read_bytes() == manifest_before_retry


def test_constraints_revision_atomically_marks_owned_numerics_stale(
    repo_root: Path,
    tmp_path: Path,
) -> None:
    project_dir = _copy_project(repo_root, tmp_path, "foundation-revision")
    allocation = _prepare_constraints_revision(repo_root, project_dir)
    history_before = json.loads(
        (project_dir / "manifest.json").read_text(encoding="utf-8")
    )["history"]

    published = _run_finalizer(repo_root, project_dir, allocation)

    assert published.returncode == 0, published.stdout + published.stderr
    manifest = json.loads(
        (project_dir / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["artifacts"]["numerics"]["status"] == "stale"
    assert manifest["artifacts"]["numerics"]["analyses"][0]["status"] == "stale"
    assert manifest["artifacts"]["calculations"]["status"] == "done"
    assert manifest["history"][: len(history_before)] == history_before
    assert manifest["history"][-1]["action"] == "constraints_updated"
    _validate_project(repo_root, project_dir)


def test_model_revision_atomically_marks_calculations_and_numerics_stale(
    repo_root: Path,
    tmp_path: Path,
) -> None:
    project_dir = _copy_project(repo_root, tmp_path, "foundation-model-revision")
    calculation_before = json.loads(
        (project_dir / "manifest.json").read_text(encoding="utf-8")
    )["artifacts"]["calculations"]
    result_meta_before = (
        project_dir / "calculations" / "task-001" / "result-meta.json"
    ).read_bytes()
    allocation = _prepare_model_revision(repo_root, project_dir)

    published = _run_finalizer(repo_root, project_dir, allocation)

    assert published.returncode == 0, published.stdout + published.stderr
    manifest = json.loads(
        (project_dir / "manifest.json").read_text(encoding="utf-8")
    )
    calculations = manifest["artifacts"]["calculations"]
    assert calculations["status"] == "stale"
    assert calculations["completed_tasks"] == calculation_before["completed_tasks"]
    assert calculations["pending_tasks"] == calculation_before["pending_tasks"]
    assert calculations["depends_on"] == calculation_before["depends_on"]
    assert manifest["artifacts"]["numerics"]["status"] == "stale"
    assert manifest["artifacts"]["numerics"]["analyses"][0]["status"] == "stale"
    assert [entry["action"] for entry in manifest["history"][-2:]] == [
        "model_complete_v2",
        "constraints_updated",
    ]
    assert (
        project_dir / "calculations" / "task-001" / "result-meta.json"
    ).read_bytes() == result_meta_before
    _validate_project(repo_root, project_dir)


def test_calc_tasks_revision_stales_calculations_and_consuming_numerics(
    repo_root: Path,
    tmp_path: Path,
) -> None:
    project_dir = _copy_project(repo_root, tmp_path, "foundation-task-revision")
    calculation_before = json.loads(
        (project_dir / "manifest.json").read_text(encoding="utf-8")
    )["artifacts"]["calculations"]
    allocation = _prepare_calc_tasks_revision(repo_root, project_dir)

    published = _run_finalizer(repo_root, project_dir, allocation)

    assert published.returncode == 0, published.stdout + published.stderr
    manifest = json.loads(
        (project_dir / "manifest.json").read_text(encoding="utf-8")
    )
    calculations = manifest["artifacts"]["calculations"]
    assert calculations == {**calculation_before, "status": "stale"}
    assert manifest["artifacts"]["numerics"]["status"] == "stale"
    assert manifest["artifacts"]["numerics"]["analyses"][0]["status"] == "stale"
    assert manifest["history"][-1]["action"] == "model_updated"
    _validate_project(repo_root, project_dir)


def test_benchmark_revision_stales_calculations_and_task_consuming_numerics(
    repo_root: Path,
    tmp_path: Path,
) -> None:
    project_dir = _copy_project(repo_root, tmp_path, "foundation-benchmark-revision")
    calculation_before = json.loads(
        (project_dir / "manifest.json").read_text(encoding="utf-8")
    )["artifacts"]["calculations"]
    allocation = _prepare_benchmarks_revision(repo_root, project_dir)

    published = _run_finalizer(repo_root, project_dir, allocation)

    assert published.returncode == 0, published.stdout + published.stderr
    manifest = json.loads(
        (project_dir / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["artifacts"]["calculations"] == {
        **calculation_before,
        "status": "stale",
    }
    assert manifest["artifacts"]["numerics"]["status"] == "stale"
    assert manifest["artifacts"]["numerics"]["analyses"][0]["status"] == "stale"
    assert manifest["history"][-1]["action"] == "benchmarks_updated"
    _validate_project(repo_root, project_dir)


def test_model_revision_rejects_corrupt_historical_calculation_evidence(
    repo_root: Path,
    tmp_path: Path,
) -> None:
    project_dir = _copy_project(repo_root, tmp_path, "foundation-corrupt-calculation")
    result_meta_path = project_dir / "calculations" / "task-001" / "result-meta.json"
    result_meta = json.loads(result_meta_path.read_text(encoding="utf-8"))
    result_meta["input_provenance"]["root_sha256"] = f"sha256:{'0' * 64}"
    _write_json(result_meta_path, result_meta)
    allocation = _prepare_model_revision(repo_root, project_dir)
    manifest_before = (project_dir / "manifest.json").read_bytes()
    model_before = capture_identity(project_dir / "model")

    failed = _run_finalizer(repo_root, project_dir, allocation)

    assert failed.returncode != 0
    assert "stale calculation historical evidence is intrinsically invalid" in failed.stderr
    assert (project_dir / "manifest.json").read_bytes() == manifest_before
    assert capture_identity(project_dir / "model") == model_before


@pytest.mark.parametrize("boundary", [1, 2, 3])
def test_foundation_failure_rolls_back_every_publication_boundary(
    repo_root: Path,
    tmp_path: Path,
    boundary: int,
) -> None:
    project_dir = _copy_project(
        repo_root,
        tmp_path,
        f"foundation-rollback-{boundary}",
    )
    allocation = _prepare_constraints_revision(repo_root, project_dir)
    manifest_before = capture_identity(project_dir / "manifest.json")
    constraints_before = capture_identity(project_dir / "constraints")
    reservation_path = Path(allocation["attempt_dir"]) / ".reservation.json"
    reservation_before = reservation_path.read_bytes()

    failed = _run_finalizer(
        repo_root,
        project_dir,
        allocation,
        env={"HEP_WORKFLOW_TEST_FAIL_FOUNDATION_AFTER": str(boundary)},
    )

    assert failed.returncode != 0
    assert "injected foundation finalization failure" in failed.stderr
    assert capture_identity(project_dir / "manifest.json") == manifest_before
    assert capture_identity(project_dir / "constraints") == constraints_before
    assert reservation_path.read_bytes() == reservation_before


def test_cross_owner_manifest_change_is_rejected_without_live_writes(
    repo_root: Path,
    tmp_path: Path,
) -> None:
    project_dir = _copy_project(repo_root, tmp_path, "foundation-owner-guard")
    allocation = _prepare_constraints_revision(repo_root, project_dir)
    candidate_manifest_path = Path(allocation["candidate_dir"]) / "manifest.json"
    candidate = json.loads(candidate_manifest_path.read_text(encoding="utf-8"))
    candidate["artifacts"]["calculations"]["status"] = "failed"
    _write_json(candidate_manifest_path, candidate)
    manifest_before = (project_dir / "manifest.json").read_bytes()
    constraints_before = capture_identity(project_dir / "constraints")

    failed = _run_finalizer(repo_root, project_dir, allocation)

    assert failed.returncode != 0
    assert "changed unowned artifacts.calculations" in failed.stderr
    assert (project_dir / "manifest.json").read_bytes() == manifest_before
    assert capture_identity(project_dir / "constraints") == constraints_before


def test_history_action_must_match_the_actual_changed_file_scope(
    repo_root: Path,
    tmp_path: Path,
) -> None:
    project_dir = _copy_project(repo_root, tmp_path, "foundation-history-scope")
    allocation = _prepare_constraints_revision(repo_root, project_dir)
    candidate_manifest_path = Path(allocation["candidate_dir"]) / "manifest.json"
    candidate = json.loads(candidate_manifest_path.read_text(encoding="utf-8"))
    candidate["history"][-1]["action"] = "benchmarks_updated"
    _write_json(candidate_manifest_path, candidate)
    manifest_before = (project_dir / "manifest.json").read_bytes()
    constraints_before = capture_identity(project_dir / "constraints")

    failed = _run_finalizer(repo_root, project_dir, allocation)

    assert failed.returncode != 0
    assert "history actions do not match the actual changed file scope" in failed.stderr
    assert (project_dir / "manifest.json").read_bytes() == manifest_before
    assert capture_identity(project_dir / "constraints") == constraints_before


def test_constraints_payload_model_version_mismatch_fails_before_publication(
    repo_root: Path,
    tmp_path: Path,
) -> None:
    project_dir = _copy_project(repo_root, tmp_path, "foundation-constraint-version")
    allocation = _prepare_constraints_revision(repo_root, project_dir)
    constraints_path = (
        Path(allocation["candidate_dir"])
        / "constraints"
        / "constraints-data.json"
    )
    constraints = json.loads(constraints_path.read_text(encoding="utf-8"))
    constraints["model_version"] = "v2"
    _write_json(constraints_path, constraints)
    manifest_before = (project_dir / "manifest.json").read_bytes()
    constraints_before = capture_identity(project_dir / "constraints")

    failed = _run_finalizer(repo_root, project_dir, allocation)

    assert failed.returncode != 0
    assert "model_version does not match the active model version" in failed.stderr
    assert (project_dir / "manifest.json").read_bytes() == manifest_before
    assert capture_identity(project_dir / "constraints") == constraints_before


def test_concurrent_foundation_baseline_drift_is_not_overwritten(
    repo_root: Path,
    tmp_path: Path,
) -> None:
    project_dir = _copy_project(repo_root, tmp_path, "foundation-baseline-drift")
    allocation = _prepare_constraints_revision(repo_root, project_dir)
    live_constraints_path = project_dir / "constraints" / "constraints-data.json"
    live_constraints = json.loads(live_constraints_path.read_text(encoding="utf-8"))
    live_constraints["constraints"][0]["notes"] += " Concurrent live edit."
    _write_json(live_constraints_path, live_constraints)
    manifest_before = (project_dir / "manifest.json").read_bytes()
    live_before = live_constraints_path.read_bytes()

    failed = _run_finalizer(repo_root, project_dir, allocation)

    assert failed.returncode != 0
    assert "changed after foundation attempt allocation" in failed.stderr
    assert (project_dir / "manifest.json").read_bytes() == manifest_before
    assert live_constraints_path.read_bytes() == live_before


def test_foundation_candidate_cannot_implicitly_delete_owned_live_files(
    repo_root: Path,
    tmp_path: Path,
) -> None:
    source = repo_root / "workspace" / "projects" / "smoke-e2e"
    project_dir = tmp_path / "workspace" / "projects" / "paper-delete-guard"
    shutil.copytree(source, project_dir)
    manifest_path = project_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["project_name"] = project_dir.name
    _write_json(manifest_path, manifest)
    allocation = _run_initializer(
        repo_root,
        project_dir,
        owner="hep-paper-formalize",
        mode="setup",
    )
    candidate_dir = Path(allocation["candidate_dir"])
    style_relative = "literature/style/paper-style.mplstyle"
    (candidate_dir / style_relative).unlink()
    candidate_manifest_path = candidate_dir / "manifest.json"
    candidate = json.loads(candidate_manifest_path.read_text(encoding="utf-8"))
    candidate["last_updated"] = "2026-07-15"
    candidate["artifacts"]["literature"]["files"].remove(style_relative)
    candidate["artifacts"]["literature"]["timestamp"] = "2026-07-15T00:00:00Z"
    candidate["history"].append(
        {
            "action": "literature_updated",
            "timestamp": "2026-07-15T00:00:00Z",
            "by": "hep-paper-formalize",
        }
    )
    _write_json(candidate_manifest_path, candidate)
    live_style = project_dir / style_relative
    live_before = live_style.read_bytes()
    manifest_before = manifest_path.read_bytes()

    failed = _run_finalizer(repo_root, project_dir, allocation)

    assert failed.returncode != 0
    assert "does not support implicit deletion" in failed.stderr
    assert live_style.read_bytes() == live_before
    assert manifest_path.read_bytes() == manifest_before


def test_paper_setup_preserves_unowned_hidden_files(
    repo_root: Path,
    tmp_path: Path,
) -> None:
    source = repo_root / "workspace" / "projects" / "smoke-e2e"
    project_dir = tmp_path / "workspace" / "projects" / "paper-setup"
    shutil.copytree(source, project_dir)
    manifest_path = project_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["project_name"] = project_dir.name
    _write_json(manifest_path, manifest)
    hidden_path = project_dir / "literature" / ".local-review-note"
    hidden_path.write_text("preserve me\n", encoding="utf-8")
    allocation = _run_initializer(
        repo_root,
        project_dir,
        owner="hep-paper-formalize",
        mode="setup",
    )
    candidate_dir = Path(allocation["candidate_dir"])
    summary_path = candidate_dir / "literature" / "repro-summary.md"
    summary_path.write_text(
        summary_path.read_text(encoding="utf-8") + "\nSetup refreshed.\n",
        encoding="utf-8",
    )
    candidate_manifest_path = candidate_dir / "manifest.json"
    candidate = json.loads(candidate_manifest_path.read_text(encoding="utf-8"))
    timestamp = "2026-07-15T00:00:00Z"
    candidate["last_updated"] = "2026-07-15"
    candidate["artifacts"]["literature"]["timestamp"] = timestamp
    candidate["history"].append(
        {
            "action": "literature_updated",
            "timestamp": timestamp,
            "by": "hep-paper-formalize",
        }
    )
    _write_json(candidate_manifest_path, candidate)

    published = _run_finalizer(repo_root, project_dir, allocation)

    assert published.returncode == 0, published.stdout + published.stderr
    assert hidden_path.read_text(encoding="utf-8") == "preserve me\n"
    _validate_project(repo_root, project_dir)


def test_fresh_paper_skeleton_can_seed_a_setup_attempt(
    repo_root: Path,
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace" / "projects"
    initialized = subprocess.run(
        [
            sys.executable,
            str(
                repo_root
                / ".agents"
                / "skills"
                / "hep-paper-formalize"
                / "scripts"
                / "init_paper_project_skeleton.py"
            ),
            "paper-foundation-fresh",
            "--workspace-root",
            str(workspace_root),
        ],
        capture_output=True,
        text=True,
    )
    assert initialized.returncode == 0, initialized.stdout + initialized.stderr
    project_dir = workspace_root / "paper-foundation-fresh"

    allocation = _run_initializer(
        repo_root,
        project_dir,
        owner="hep-paper-formalize",
        mode="setup",
    )

    candidate_dir = Path(allocation["candidate_dir"])
    assert (candidate_dir / "literature" / "digitized").is_dir()
    assert (candidate_dir / "literature" / "style").is_dir()
    assert not (candidate_dir / "manifest.json").exists()
    assert not (project_dir / "manifest.json").exists()
