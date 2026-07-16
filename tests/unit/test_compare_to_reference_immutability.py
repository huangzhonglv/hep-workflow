from __future__ import annotations

import importlib

from scripts import _publication_transaction, compare_to_reference
from tests.unit.compare_reference_fixtures import hash_file, make_compare_project, run_compare


def test_reproduction_run_directory_is_immutable(repo_root, tmp_path) -> None:
    project_dir = make_compare_project(tmp_path)

    first = run_compare(repo_root, project_dir, "run-001")
    result_path = project_dir / "reproduction" / "runs" / "run-001" / "reproduction-result.json"
    first_hash = hash_file(result_path)
    second = run_compare(repo_root, project_dir, "run-001")
    second_hash = hash_file(result_path)
    third = run_compare(repo_root, project_dir, "run-002")

    assert first.returncode == 0, first.stdout + first.stderr
    assert second.returncode != 0
    assert "already exists" in second.stderr
    assert first_hash == second_hash
    assert third.returncode == 0, third.stdout + third.stderr


def test_publish_rolls_back_figures_when_second_atomic_replace_fails(
    tmp_path, monkeypatch
) -> None:
    project_dir = make_compare_project(tmp_path)
    argv = [
        "--project-dir",
        str(project_dir),
        "--analysis-id",
        "analysis-001",
        "--repro-id",
        "run-001",
    ]
    transaction_module = importlib.import_module(
        compare_to_reference.PublicationTransaction.__module__
    )
    original_replace = transaction_module._rename_no_replace
    final_run = project_dir / "reproduction" / "runs" / "run-001"

    def fail_second_replace(source, destination):
        if destination == final_run:
            raise OSError("injected second publish failure")
        return original_replace(source, destination)

    monkeypatch.setattr(transaction_module, "_rename_no_replace", fail_second_replace)
    assert compare_to_reference.run(argv) == 1
    assert not (project_dir / "reproduction" / "figures" / "run-001").exists()
    assert not (project_dir / "reproduction" / "runs" / "run-001").exists()
    assert not list((project_dir / "reproduction").glob(".staging-run-001-*"))

    monkeypatch.setattr(transaction_module, "_rename_no_replace", original_replace)
    assert compare_to_reference.run(argv) == 0
    assert (project_dir / "reproduction" / "figures" / "run-001").is_dir()
    assert (project_dir / "reproduction" / "runs" / "run-001").is_dir()


def test_manifest_publish_failure_rolls_back_complete_reproduction_generation(
    tmp_path, monkeypatch
) -> None:
    project_dir = make_compare_project(tmp_path)
    argv = [
        "--project-dir",
        str(project_dir),
        "--analysis-id",
        "analysis-001",
        "--repro-id",
        "run-001",
    ]
    manifest_path = project_dir / "manifest.json"
    original_manifest = manifest_path.read_bytes()
    transaction_module = importlib.import_module(
        compare_to_reference.PublicationTransaction.__module__
    )
    original_replace = transaction_module._rename_no_replace

    failed = False

    def fail_manifest_replace(source, destination):
        nonlocal failed
        if destination == manifest_path and not failed:
            failed = True
            raise OSError("injected manifest publish failure")
        return original_replace(source, destination)

    monkeypatch.setattr(transaction_module, "_rename_no_replace", fail_manifest_replace)
    assert compare_to_reference.run(argv) == 1
    assert manifest_path.read_bytes() == original_manifest
    assert not (project_dir / "reproduction" / "figures" / "run-001").exists()
    assert not (project_dir / "reproduction" / "runs" / "run-001").exists()

    monkeypatch.setattr(transaction_module, "_rename_no_replace", original_replace)
    assert compare_to_reference.run(argv) == 0
    manifest = compare_to_reference.load_json(manifest_path)
    assert manifest["artifacts"]["reproduction"]["runs"] == ["run-001"]
    entries = [
        entry
        for entry in manifest["history"]
        if entry.get("action") == "reproduction_run_complete"
    ]
    assert len(entries) == 1
    assert entries[0]["repro_id"] == "run-001"
    assert isinstance(entries[0].get("event_id"), str)


def test_comparator_fails_closed_while_an_authoritative_transaction_is_active(
    tmp_path, capsys
) -> None:
    project_dir = make_compare_project(tmp_path)
    argv = [
        "--project-dir",
        str(project_dir),
        "--analysis-id",
        "analysis-001",
        "--repro-id",
        "run-001",
    ]

    with _publication_transaction.PublicationTransaction.begin(
        project_dir,
        "external-writer",
    ):
        assert compare_to_reference.run(argv) == 1

    assert "incomplete publication transaction" in capsys.readouterr().err
    assert not (project_dir / "reproduction" / "runs" / "run-001").exists()


def test_committed_comparison_cleanup_warning_is_success_without_retry(
    tmp_path, monkeypatch, capsys
) -> None:
    project_dir = make_compare_project(tmp_path)
    argv = [
        "--project-dir",
        str(project_dir),
        "--analysis-id",
        "analysis-001",
        "--repro-id",
        "run-001",
    ]
    original_commit = compare_to_reference.PublicationTransaction.commit

    def commit_then_report_pending_cleanup(self, *args, **kwargs):
        original_commit(self, *args, **kwargs)
        raise compare_to_reference.TransactionCommittedCleanupError(
            self.transaction_id,
            OSError("injected cleanup interruption"),
        )

    monkeypatch.setattr(
        compare_to_reference.PublicationTransaction,
        "commit",
        commit_then_report_pending_cleanup,
    )

    assert compare_to_reference.run(argv) == 0
    warning = capsys.readouterr().err
    assert "committed successfully" in warning
    assert "Do not retry" in warning
    assert "injected cleanup interruption" in warning
    assert (
        project_dir
        / "reproduction"
        / "runs"
        / "run-001"
        / "reproduction-result.json"
    ).is_file()
