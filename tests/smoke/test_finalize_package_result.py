from __future__ import annotations

import json
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import _publication_transaction


def _script(repo_root: Path, name: str) -> Path:
    return (
        repo_root
        / ".agents"
        / "skills"
        / "package-scribe"
        / "scripts"
        / name
    )


def _load_finalizer(repo_root: Path):
    path = _script(repo_root, "finalize_package_result.py")
    spec = importlib.util.spec_from_file_location(
        "package_result_finalizer_cleanup_test",
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(path.parent))
    return module


def _copy_project(repo_root: Path, tmp_path: Path) -> Path:
    source = repo_root / "tests" / "fixtures" / "workspace-projects" / "numerics-contract"
    destination = tmp_path / "package-project"
    shutil.copytree(source, destination)
    return destination


def _manifest_candidate_inputs(project_dir: Path) -> tuple[dict, dict, dict]:
    return (
        json.loads((project_dir / "manifest.json").read_text(encoding="utf-8")),
        json.loads(
            (project_dir / "model" / "calc-tasks.json").read_text(encoding="utf-8")
        ),
        json.loads(
            (
                project_dir / "calculations" / "task-001" / "result-meta.json"
            ).read_text(encoding="utf-8")
        ),
    )


def test_stale_rerun_starts_a_new_current_calculation_generation(
    repo_root: Path,
    tmp_path: Path,
) -> None:
    project_dir = _copy_project(repo_root, tmp_path)
    manifest, calc_tasks, metadata = _manifest_candidate_inputs(project_dir)
    second_task = dict(calc_tasks["tasks"][0])
    second_task["task_id"] = "task-002"
    calc_tasks["tasks"].append(second_task)
    calculations = manifest["artifacts"]["calculations"]
    calculations["status"] = "stale"
    calculations["completed_tasks"] = ["task-001", "task-002"]
    calculations["pending_tasks"] = []
    calculations["depends_on"]["model"] = {
        "version": "v0",
        "checksum": f"sha256:{'0' * 64}",
    }
    finalizer = _load_finalizer(repo_root)

    candidate, history_action = finalizer._build_manifest_candidate(
        manifest=manifest,
        calc_tasks=calc_tasks,
        metadata=metadata,
        task_id="task-001",
        timestamp="2026-07-15T00:00:00Z",
        event_id="1" * 32,
        result_changed=False,
    )

    updated = candidate["artifacts"]["calculations"]
    assert history_action == "calc_task_task-001_revised"
    assert updated["status"] == "partial"
    assert updated["completed_tasks"] == ["task-001"]
    assert updated["pending_tasks"] == ["task-002"]
    assert updated["depends_on"]["model"] == {
        "version": manifest["artifacts"]["model"]["version"],
        "checksum": manifest["artifacts"]["model"]["checksum"],
    }


def test_current_rerun_rejects_unclassified_model_dependency_drift(
    repo_root: Path,
    tmp_path: Path,
) -> None:
    project_dir = _copy_project(repo_root, tmp_path)
    manifest, calc_tasks, metadata = _manifest_candidate_inputs(project_dir)
    manifest["artifacts"]["calculations"]["depends_on"]["model"][
        "version"
    ] = "v0"
    finalizer = _load_finalizer(repo_root)

    with pytest.raises(ValueError, match="mechanical stale transition"):
        finalizer._build_manifest_candidate(
            manifest=manifest,
            calc_tasks=calc_tasks,
            metadata=metadata,
            task_id="task-001",
            timestamp="2026-07-15T00:00:00Z",
            event_id="2" * 32,
            result_changed=False,
        )


def _initialize_attempt(
    repo_root: Path,
    task_dir: Path,
    *,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    process_env = os.environ.copy()
    process_env.update(env or {})
    return subprocess.run(
        [
            sys.executable,
            str(_script(repo_root, "init_package_result_files.py")),
            "--task-dir",
            str(task_dir),
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
        env=process_env,
    )


def _owned_attempt_with_candidate(
    repo_root: Path,
    project_dir: Path,
    *,
    marker: str,
) -> tuple[Path, str]:
    task_dir = project_dir / "calculations" / "task-001"
    initialized = _initialize_attempt(repo_root, task_dir)
    assert initialized.returncode == 0, initialized.stdout + initialized.stderr
    allocation = json.loads(initialized.stdout)
    attempt_dir = Path(allocation["path"])
    for entry in task_dir.iterdir():
        if entry.is_file():
            shutil.copy2(entry, attempt_dir / entry.name)
    with (attempt_dir / "result-summary.md").open("a", encoding="utf-8") as handle:
        handle.write(f"\n{marker}\n")
    return attempt_dir, allocation["attempt_id"]


def _finalize(
    repo_root: Path,
    task_dir: Path,
    attempt_dir: Path,
    attempt_id: str,
    *,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    process_env = os.environ.copy()
    process_env.update(env or {})
    return subprocess.run(
        [
            sys.executable,
            str(_script(repo_root, "finalize_package_result.py")),
            "--task-dir",
            str(task_dir),
            "--attempt-dir",
            str(attempt_dir),
            "--attempt-id",
            attempt_id,
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
        env=process_env,
    )


def test_generation_failure_never_touches_last_good_or_manifest(
    repo_root: Path,
    tmp_path: Path,
) -> None:
    project_dir = _copy_project(repo_root, tmp_path)
    task_dir = project_dir / "calculations" / "task-001"
    task_before = _publication_transaction.capture_identity(task_dir)
    manifest_before = (project_dir / "manifest.json").read_bytes()

    failed = _initialize_attempt(
        repo_root,
        task_dir,
        env={"HEP_WORKFLOW_TEST_FAIL_PACKAGE_INIT_AFTER": "result-summary.md"},
    )

    assert failed.returncode != 0
    assert "injected package initializer failure" in failed.stderr
    assert _publication_transaction.capture_identity(task_dir) == task_before
    assert (project_dir / "manifest.json").read_bytes() == manifest_before


def test_validation_failure_never_touches_last_good_or_manifest(
    repo_root: Path,
    tmp_path: Path,
) -> None:
    project_dir = _copy_project(repo_root, tmp_path)
    task_dir = project_dir / "calculations" / "task-001"
    attempt_dir, attempt_id = _owned_attempt_with_candidate(
        repo_root,
        project_dir,
        marker="validation-failure-candidate",
    )
    (attempt_dir / "result-meta.json").write_text("{broken\n", encoding="utf-8")
    task_before = _publication_transaction.capture_identity(task_dir)
    manifest_before = (project_dir / "manifest.json").read_bytes()

    failed = _finalize(repo_root, task_dir, attempt_dir, attempt_id)

    assert failed.returncode != 0
    assert _publication_transaction.capture_identity(task_dir) == task_before
    assert (project_dir / "manifest.json").read_bytes() == manifest_before
    reservation = json.loads(
        (attempt_dir / ".reservation.json").read_text(encoding="utf-8")
    )
    assert reservation["state"] == "initialized"


def test_blocked_generation_is_diagnostic_only_and_preserves_last_good(
    repo_root: Path,
    tmp_path: Path,
) -> None:
    project_dir = _copy_project(repo_root, tmp_path)
    task_dir = project_dir / "calculations" / "task-001"
    attempt_dir, attempt_id = _owned_attempt_with_candidate(
        repo_root,
        project_dir,
        marker="blocked-diagnostic-attempt",
    )
    metadata_path = attempt_dir / "result-meta.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["calculation_provenance"] = "blocked"
    metadata["translation_status"] = "failed"
    metadata["package_x_methods"] = []
    metadata.pop("derivation_evidence", None)
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    task_before = _publication_transaction.capture_identity(task_dir)
    manifest_before = (project_dir / "manifest.json").read_bytes()

    failed = _finalize(repo_root, task_dir, attempt_dir, attempt_id)

    assert failed.returncode != 0
    assert "diagnostic-only" in failed.stderr
    assert _publication_transaction.capture_identity(task_dir) == task_before
    assert (project_dir / "manifest.json").read_bytes() == manifest_before


def test_partial_generation_is_diagnostic_only_and_preserves_last_good(
    repo_root: Path,
    tmp_path: Path,
) -> None:
    project_dir = _copy_project(repo_root, tmp_path)
    task_dir = project_dir / "calculations" / "task-001"
    attempt_dir, attempt_id = _owned_attempt_with_candidate(
        repo_root,
        project_dir,
        marker="partial-diagnostic-attempt",
    )
    metadata_path = attempt_dir / "result-meta.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["translation_status"] = "partial"
    metadata["translation_notes"] = "PV function translation remains pending."
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    task_before = _publication_transaction.capture_identity(task_dir)
    manifest_before = (project_dir / "manifest.json").read_bytes()

    failed = _finalize(repo_root, task_dir, attempt_dir, attempt_id)

    assert failed.returncode != 0
    assert "blocked/partial/failed" in failed.stderr
    assert _publication_transaction.capture_identity(task_dir) == task_before
    assert (project_dir / "manifest.json").read_bytes() == manifest_before


def test_metadata_parameter_omission_cannot_silently_use_python_default(
    repo_root: Path,
    tmp_path: Path,
) -> None:
    project_dir = _copy_project(repo_root, tmp_path)
    task_dir = project_dir / "calculations" / "task-001"
    attempt_dir, attempt_id = _owned_attempt_with_candidate(
        repo_root,
        project_dir,
        marker="metadata-omits-defaulted-input",
    )
    metadata_path = attempt_dir / "result-meta.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["parameters"] = [
        parameter
        for parameter in metadata["parameters"]
        if parameter["canonical_name"] != "v_Delta"
    ]
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    task_before = _publication_transaction.capture_identity(task_dir)
    manifest_before = (project_dir / "manifest.json").read_bytes()

    failed = _finalize(repo_root, task_dir, attempt_dir, attempt_id)

    assert failed.returncode != 0
    assert "must exactly match python_function" in failed.stderr
    assert _publication_transaction.capture_identity(task_dir) == task_before
    assert (project_dir / "manifest.json").read_bytes() == manifest_before


def test_python_backend_cannot_accept_undeclared_kwargs_channel(
    repo_root: Path,
    tmp_path: Path,
) -> None:
    project_dir = _copy_project(repo_root, tmp_path)
    task_dir = project_dir / "calculations" / "task-001"
    attempt_dir, attempt_id = _owned_attempt_with_candidate(
        repo_root,
        project_dir,
        marker="undeclared-kwargs-channel",
    )
    python_path = attempt_dir / "result-python.py"
    source = python_path.read_text(encoding="utf-8")
    source = source.replace(
        "v_Delta: float = 1.0e-3)",
        "v_Delta: float = 1.0e-3, **kwargs)",
    )
    assert "**kwargs" in source
    python_path.write_text(source, encoding="utf-8")

    failed = _finalize(repo_root, task_dir, attempt_dir, attempt_id)

    assert failed.returncode != 0
    assert "must not accept **kwargs" in failed.stderr


def test_python_backend_declared_callable_cannot_be_decorated(
    repo_root: Path,
    tmp_path: Path,
) -> None:
    project_dir = _copy_project(repo_root, tmp_path)
    task_dir = project_dir / "calculations" / "task-001"
    attempt_dir, attempt_id = _owned_attempt_with_candidate(
        repo_root,
        project_dir,
        marker="decorated-python-callable",
    )
    python_path = attempt_dir / "result-python.py"
    source = python_path.read_text(encoding="utf-8")
    source = source.replace(
        "def compute_br_mu_to_egamma(",
        "@staticmethod\ndef compute_br_mu_to_egamma(",
    )
    assert "@staticmethod" in source
    python_path.write_text(source, encoding="utf-8")

    failed = _finalize(repo_root, task_dir, attempt_dir, attempt_id)

    assert failed.returncode != 0
    assert "must not use decorators" in failed.stderr


def test_python_backend_declared_callable_cannot_be_rebound_after_definition(
    repo_root: Path,
    tmp_path: Path,
) -> None:
    project_dir = _copy_project(repo_root, tmp_path)
    task_dir = project_dir / "calculations" / "task-001"
    attempt_dir, attempt_id = _owned_attempt_with_candidate(
        repo_root,
        project_dir,
        marker="rebound-python-callable",
    )
    python_path = attempt_dir / "result-python.py"
    with python_path.open("a", encoding="utf-8") as handle:
        handle.write("\ncompute_br_mu_to_egamma = lambda **kwargs: 0.0\n")

    failed = _finalize(repo_root, task_dir, attempt_dir, attempt_id)

    assert failed.returncode != 0
    assert "is rebound after its selected definition" in failed.stderr


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [("role", "fixed"), ("unit", "TeV")],
)
def test_metadata_parameter_contract_must_match_model_spec(
    repo_root: Path,
    tmp_path: Path,
    field: str,
    invalid_value: str,
) -> None:
    project_dir = _copy_project(repo_root, tmp_path)
    task_dir = project_dir / "calculations" / "task-001"
    attempt_dir, attempt_id = _owned_attempt_with_candidate(
        repo_root,
        project_dir,
        marker=f"metadata-{field}-drift",
    )
    metadata_path = attempt_dir / "result-meta.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    parameter = next(
        item for item in metadata["parameters"] if item["canonical_name"] == "v_Delta"
    )
    parameter[field] = invalid_value
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")

    failed = _finalize(repo_root, task_dir, attempt_dir, attempt_id)

    assert failed.returncode != 0
    assert f"v_Delta' {field}" in failed.stderr


def test_loop_attempt_cannot_publish_manual_tree_algebra_provenance(
    repo_root: Path,
    tmp_path: Path,
) -> None:
    project_dir = _copy_project(repo_root, tmp_path)
    task_dir = project_dir / "calculations" / "task-001"
    attempt_dir, attempt_id = _owned_attempt_with_candidate(
        repo_root,
        project_dir,
        marker="loop-manual-tree-provenance",
    )
    metadata_path = attempt_dir / "result-meta.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["calculation_provenance"] = "manual_tree_algebra"
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")

    failed = _finalize(repo_root, task_dir, attempt_dir, attempt_id)

    assert failed.returncode != 0
    assert "loop task cannot use manual_tree_algebra" in failed.stderr


@pytest.mark.parametrize("failure_boundary", ["1", "2", "3"])
def test_each_publication_boundary_rolls_back_task_reservation_and_manifest(
    repo_root: Path,
    tmp_path: Path,
    failure_boundary: str,
) -> None:
    project_dir = _copy_project(repo_root, tmp_path)
    task_dir = project_dir / "calculations" / "task-001"
    attempt_dir, attempt_id = _owned_attempt_with_candidate(
        repo_root,
        project_dir,
        marker=f"publication-boundary-{failure_boundary}",
    )
    task_before = _publication_transaction.capture_identity(task_dir)
    reservation_before = (attempt_dir / ".reservation.json").read_bytes()
    manifest_before = (project_dir / "manifest.json").read_bytes()

    failed = _finalize(
        repo_root,
        task_dir,
        attempt_dir,
        attempt_id,
        env={"HEP_WORKFLOW_TEST_FAIL_PACKAGE_FINALIZE_AFTER": failure_boundary},
    )

    assert failed.returncode != 0
    assert "injected package finalization failure" in failed.stderr
    assert _publication_transaction.capture_identity(task_dir) == task_before
    assert (attempt_dir / ".reservation.json").read_bytes() == reservation_before
    assert (project_dir / "manifest.json").read_bytes() == manifest_before


def test_successful_revision_marks_dependent_numerics_stale_and_is_idempotent(
    repo_root: Path,
    tmp_path: Path,
) -> None:
    project_dir = _copy_project(repo_root, tmp_path)
    task_dir = project_dir / "calculations" / "task-001"
    marker = "atomically-published-revision"
    attempt_dir, attempt_id = _owned_attempt_with_candidate(
        repo_root,
        project_dir,
        marker=marker,
    )

    published = _finalize(repo_root, task_dir, attempt_dir, attempt_id)

    assert published.returncode == 0, published.stdout + published.stderr
    payload = json.loads(published.stdout)
    assert payload["status"] == "published"
    assert payload["history_action"] == "calc_task_task-001_revised"
    assert marker in (task_dir / "result-summary.md").read_text(encoding="utf-8")
    result_meta = json.loads((task_dir / "result-meta.json").read_text(encoding="utf-8"))
    graph = result_meta["input_provenance"]
    assert graph["verification_status"] == "verified"
    assert any(
        entry["path"].endswith("scripts/finalize_package_result.py")
        for entry in graph["entries"]
    )

    manifest = json.loads((project_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["artifacts"]["calculations"]["completed_tasks"] == ["task-001"]
    analysis = manifest["artifacts"]["numerics"]["analyses"][0]
    assert analysis["status"] == "stale"
    assert manifest["artifacts"]["numerics"]["status"] == "stale"
    event_id = json.loads(
        (attempt_dir / ".reservation.json").read_text(encoding="utf-8")
    )["history_event_id"]
    matching_events = [
        entry for entry in manifest["history"] if entry.get("event_id") == event_id
    ]
    assert len(matching_events) == 1
    workspace_validation = subprocess.run(
        [
            sys.executable,
            str(repo_root / "scripts" / "validate_workspace_projects.py"),
            "--workspace-root",
            str(project_dir.parent),
            project_dir.name,
        ],
        capture_output=True,
        text=True,
    )
    assert workspace_validation.returncode == 0, (
        workspace_validation.stdout + workspace_validation.stderr
    )

    manifest_after_first = (project_dir / "manifest.json").read_bytes()
    task_after_first = _publication_transaction.capture_identity(task_dir)
    repeated = _finalize(repo_root, task_dir, attempt_dir, attempt_id)

    assert repeated.returncode == 0, repeated.stdout + repeated.stderr
    assert json.loads(repeated.stdout)["status"] == "already_published"
    assert (project_dir / "manifest.json").read_bytes() == manifest_after_first
    assert _publication_transaction.capture_identity(task_dir) == task_after_first
    repeated_manifest = json.loads(manifest_after_first)
    assert len(
        [entry for entry in repeated_manifest["history"] if entry.get("event_id") == event_id]
    ) == 1


def test_destination_drift_after_attempt_reservation_fails_closed(
    repo_root: Path,
    tmp_path: Path,
) -> None:
    project_dir = _copy_project(repo_root, tmp_path)
    task_dir = project_dir / "calculations" / "task-001"
    attempt_dir, attempt_id = _owned_attempt_with_candidate(
        repo_root,
        project_dir,
        marker="candidate-that-must-not-win",
    )
    manifest_before = (project_dir / "manifest.json").read_bytes()
    with (task_dir / "result-summary.md").open("a", encoding="utf-8") as handle:
        handle.write("\nexternal-new-owner\n")
    drifted = _publication_transaction.capture_identity(task_dir)

    failed = _finalize(repo_root, task_dir, attempt_dir, attempt_id)

    assert failed.returncode != 0
    assert "changed after this attempt was reserved" in failed.stderr
    assert _publication_transaction.capture_identity(task_dir) == drifted
    assert (project_dir / "manifest.json").read_bytes() == manifest_before


def test_committed_cleanup_warning_is_success_without_retry(
    repo_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project_dir = _copy_project(repo_root, tmp_path)
    task_dir = project_dir / "calculations" / "task-001"
    attempt_dir, attempt_id = _owned_attempt_with_candidate(
        repo_root,
        project_dir,
        marker="cleanup-pending-publication",
    )
    finalizer = _load_finalizer(repo_root)
    original_commit = finalizer.PublicationTransaction.commit

    def commit_then_report_pending_cleanup(self, *args, **kwargs):
        original_commit(self, *args, **kwargs)
        raise finalizer.TransactionCommittedCleanupError(
            self.transaction_id,
            OSError("injected cleanup interruption"),
        )

    monkeypatch.setattr(
        finalizer.PublicationTransaction,
        "commit",
        commit_then_report_pending_cleanup,
    )
    exit_code = finalizer.main(
        [
            "--task-dir",
            str(task_dir),
            "--attempt-dir",
            str(attempt_dir),
            "--attempt-id",
            attempt_id,
            "--format",
            "json",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert json.loads(captured.out)["cleanup_pending"] is True
    assert "committed successfully" in captured.err
    assert "Do not retry" in captured.err
    assert "injected cleanup interruption" in captured.err
    assert json.loads(
        (attempt_dir / ".reservation.json").read_text(encoding="utf-8")
    )["state"] == "published"
