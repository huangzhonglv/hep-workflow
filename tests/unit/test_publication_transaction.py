from __future__ import annotations

import os
import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

from scripts import _publication_transaction as publication


pytestmark = pytest.mark.skipif(
    os.name != "posix",
    reason="the publication contract explicitly requires POSIX flock/fsync",
)


def write_candidate(
    transaction: publication.PublicationTransaction,
    relative_path: str,
    content: str,
) -> Path:
    path = transaction.stage_path(relative_path)
    path.write_text(content, encoding="utf-8")
    return path


def abandon_for_recovery(transaction: publication.PublicationTransaction) -> None:
    """Model process death after filesystem operations, without normal abort."""

    transaction._publication_lock.release()
    transaction._owns_lock = False
    transaction._closed = True


def test_replace_commit_and_active_transaction_detection(tmp_path: Path) -> None:
    destination = tmp_path / "result.txt"
    destination.write_text("old\n", encoding="utf-8")
    expected = publication.capture_identity(destination)

    transaction = publication.PublicationTransaction.begin(tmp_path, "scan")
    staged = write_candidate(transaction, "result.txt", "new\n")
    candidate = publication.capture_identity(staged)
    transaction.add(
        staged,
        destination,
        mode="replace",
        expected_before=expected,
    )

    assert publication.active_transactions(tmp_path) == (transaction.transaction_id,)
    with pytest.raises(publication.ActiveTransactionError):
        publication.assert_no_active_transactions(tmp_path)
    publication.assert_no_active_transactions(
        tmp_path, exclude=(transaction.transaction_id,)
    )

    transaction.commit()

    assert destination.read_text(encoding="utf-8") == "new\n"
    assert publication.capture_identity(destination) == candidate
    assert publication.active_transactions(tmp_path) == ()
    assert not (
        tmp_path
        / publication.TRANSACTION_ROOT_NAME
        / publication.ACTIVE_OWNER_ROOT_NAME
    ).exists()


def test_external_lock_covers_read_merge_and_commit(tmp_path: Path) -> None:
    destination = tmp_path / "manifest.json"
    destination.write_text('{"generation": 1}\n', encoding="utf-8")

    with publication.publication_lock(tmp_path, "manifest-merge") as lock:
        assert lock.held
        captured = publication.capture_identity(destination)
        transaction = publication.PublicationTransaction.begin(
            tmp_path,
            "manifest-update",
            lock=lock,
        )
        staged = write_candidate(
            transaction,
            "manifest.json",
            '{"generation": 2}\n',
        )
        transaction.add(
            staged,
            destination,
            mode="replace",
            expected_before=captured,
        )
        transaction.commit()

        assert lock.held, "an externally owned lock must outlive transaction commit"
        competing = publication.publication_lock(tmp_path, "competing-writer")
        with pytest.raises(publication.TransactionBusyError):
            competing.acquire()

    with publication.publication_lock(tmp_path, "after-release") as reacquired:
        assert reacquired.held


def test_lock_identity_is_stable_across_process_tmpdir_values(tmp_path: Path) -> None:
    alternate_tmp = tmp_path / "alternate-process-tmp"
    alternate_tmp.mkdir()
    child = """
from pathlib import Path
from scripts import _publication_transaction as publication

try:
    publication.publication_lock(Path(__import__('sys').argv[1]), 'child').acquire()
except publication.TransactionBusyError:
    raise SystemExit(23)
raise SystemExit(0)
"""
    environment = dict(os.environ)
    environment["TMPDIR"] = str(alternate_tmp)

    with publication.publication_lock(tmp_path, "parent"):
        result = subprocess.run(
            [sys.executable, "-c", child, str(tmp_path)],
            cwd=Path(__file__).resolve().parents[2],
            env=environment,
            capture_output=True,
            text=True,
        )

    assert result.returncode == 23, result.stdout + result.stderr


def test_create_only_cas_preserves_concurrent_destination(tmp_path: Path) -> None:
    destination = tmp_path / "immutable-run"
    transaction = publication.PublicationTransaction.begin(tmp_path, "repro")
    staged = transaction.stage_path("immutable-run")
    staged.mkdir()
    (staged / "result.json").write_text("{}\n", encoding="utf-8")
    transaction.add(
        staged,
        destination,
        mode="create_only",
        expected_before=publication.capture_identity(destination),
    )

    destination.mkdir()
    (destination / "owner.txt").write_text("other publisher\n", encoding="utf-8")

    with pytest.raises(publication.CompareAndSwapError):
        transaction.commit()

    assert (destination / "owner.txt").read_text(encoding="utf-8") == "other publisher\n"
    assert publication.active_transactions(tmp_path) == ()


def test_atomic_install_never_overwrites_racing_unknown_destination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "scan.csv"
    destination.write_text("old\n", encoding="utf-8")
    transaction = publication.PublicationTransaction.begin(tmp_path, "atomic-cas")
    staged = write_candidate(transaction, "scan.csv", "candidate\n")
    transaction.add(
        staged,
        destination,
        mode="replace",
        expected_before=publication.capture_identity(destination),
    )
    original_rename = publication._rename_no_replace
    raced = False

    def create_unknown_before_install(source: Path, target: Path) -> None:
        nonlocal raced
        if source == staged and target == destination and not raced:
            raced = True
            destination.write_text("external owner\n", encoding="utf-8")
        original_rename(source, target)

    monkeypatch.setattr(
        publication,
        "_rename_no_replace",
        create_unknown_before_install,
    )
    with pytest.raises(publication.TransactionRollbackError):
        transaction.commit()

    assert raced
    assert destination.read_text(encoding="utf-8") == "external owner\n"
    assert publication.active_transactions(tmp_path) == (transaction.transaction_id,)


def test_replace_rejects_file_directory_kind_change(tmp_path: Path) -> None:
    destination = tmp_path / "managed-output"
    destination.mkdir()
    (destination / "user-data.txt").write_text("preserve\n", encoding="utf-8")
    transaction = publication.PublicationTransaction.begin(tmp_path, "kind-check")
    staged = write_candidate(transaction, "managed-output", "replacement\n")

    with pytest.raises(publication.UnsafePublicationPath, match="file and directory"):
        transaction.add(
            staged,
            destination,
            mode="replace",
            expected_before=publication.capture_identity(destination),
        )

    transaction.abort()
    assert (destination / "user-data.txt").read_text(encoding="utf-8") == "preserve\n"


def test_recovery_cli_is_dry_run_by_default_and_requires_explicit_recover(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "result.txt"
    transaction = publication.PublicationTransaction.begin(tmp_path, "cli-recovery")
    staged = write_candidate(transaction, "result.txt", "candidate\n")
    transaction.add(
        staged,
        destination,
        mode="create_only",
        expected_before=publication.capture_identity(destination),
    )
    transaction_id = transaction.transaction_id
    abandon_for_recovery(transaction)
    script = Path(__file__).resolve().parents[2] / "scripts" / "recover_publication_transactions.py"

    inspected = subprocess.run(
        [
            sys.executable,
            str(script),
            "--project-dir",
            str(tmp_path),
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
    )
    assert inspected.returncode == 1
    assert json.loads(inspected.stdout) == [
        {"issues": [], "outcome": "active", "transaction_id": transaction_id}
    ]
    assert publication.active_transactions(tmp_path) == (transaction_id,)

    recovered = subprocess.run(
        [
            sys.executable,
            str(script),
            "--project-dir",
            str(tmp_path),
            "--recover",
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
    )
    assert recovered.returncode == 0, recovered.stdout + recovered.stderr
    assert json.loads(recovered.stdout) == [
        {"issues": [], "outcome": "rolled_back", "transaction_id": transaction_id}
    ]
    assert publication.active_transactions(tmp_path) == ()
    assert not destination.exists()


def test_failure_after_backup_move_restores_old_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "scan.csv"
    destination.write_text("old\n", encoding="utf-8")
    old_identity = publication.capture_identity(destination)
    transaction = publication.PublicationTransaction.begin(tmp_path, "scan")
    staged = write_candidate(transaction, "scan.csv", "new\n")
    transaction.add(
        staged,
        destination,
        mode="replace",
        expected_before=old_identity,
    )
    original_replace = publication._rename_no_replace

    def fail_candidate_move(source: object, target: object) -> None:
        if Path(source) == staged and Path(target) == destination:
            raise OSError("injected candidate move failure")
        original_replace(source, target)

    monkeypatch.setattr(publication, "_rename_no_replace", fail_candidate_move)

    with pytest.raises(OSError, match="injected candidate move failure"):
        transaction.commit()

    assert publication.capture_identity(destination) == old_identity
    assert publication.active_transactions(tmp_path) == ()


def test_second_entry_failure_rolls_back_every_published_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destinations = [tmp_path / "scan.csv", tmp_path / "scan.meta.json"]
    for index, destination in enumerate(destinations):
        destination.write_text(f"old-{index}\n", encoding="utf-8")
    old_identities = [publication.capture_identity(path) for path in destinations]

    transaction = publication.PublicationTransaction.begin(tmp_path, "pair")
    staged_paths = [
        write_candidate(transaction, destination.name, f"new-{index}\n")
        for index, destination in enumerate(destinations)
    ]
    for staged, destination, expected in zip(
        staged_paths, destinations, old_identities, strict=True
    ):
        transaction.add(
            staged,
            destination,
            mode="replace",
            expected_before=expected,
        )

    original_replace = publication._rename_no_replace

    def fail_second_candidate(source: object, target: object) -> None:
        if Path(source) == staged_paths[1] and Path(target) == destinations[1]:
            raise OSError("injected second-entry failure")
        original_replace(source, target)

    monkeypatch.setattr(publication, "_rename_no_replace", fail_second_candidate)

    with pytest.raises(OSError, match="second-entry"):
        transaction.commit()

    assert [publication.capture_identity(path) for path in destinations] == old_identities
    assert publication.active_transactions(tmp_path) == ()


@pytest.mark.parametrize("failure_boundary", ["fsync", "journal"])
def test_failure_after_candidate_rename_still_restores_prior_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_boundary: str,
) -> None:
    destination = tmp_path / "summary.md"
    destination.write_text("old summary\n", encoding="utf-8")
    old_identity = publication.capture_identity(destination)
    transaction = publication.PublicationTransaction.begin(tmp_path, "summary")
    staged = write_candidate(transaction, "summary.md", "new summary\n")
    candidate_identity = publication.capture_identity(staged)
    transaction.add(
        staged,
        destination,
        mode="replace",
        expected_before=old_identity,
    )

    if failure_boundary == "fsync":
        original_fsync_directory = publication._fsync_directory

        def fail_published_parent(path: Path) -> None:
            if (
                path == destination.parent
                and publication.capture_identity(destination) == candidate_identity
            ):
                raise OSError("injected post-rename fsync failure")
            original_fsync_directory(path)

        monkeypatch.setattr(publication, "_fsync_directory", fail_published_parent)
    else:
        original_write_journal = transaction._write_journal

        def fail_published_journal() -> None:
            if publication.capture_identity(destination) == candidate_identity:
                raise OSError("injected post-rename journal failure")
            original_write_journal()

        monkeypatch.setattr(transaction, "_write_journal", fail_published_journal)

    with pytest.raises(OSError, match="post-rename"):
        transaction.commit()

    assert publication.capture_identity(destination) == old_identity
    assert publication.active_transactions(tmp_path) == ()


def test_post_publish_failure_restores_directory_tree(tmp_path: Path) -> None:
    destination = tmp_path / "figures"
    destination.mkdir()
    (destination / "plot.png").write_bytes(b"old-png")
    old_identity = publication.capture_identity(destination)

    transaction = publication.PublicationTransaction.begin(tmp_path, "figures")
    staged = transaction.stage_path("figures")
    staged.mkdir()
    (staged / "plot.png").write_bytes(b"new-png")
    (staged / "plot.pdf").write_bytes(b"new-pdf")
    transaction.add(
        staged,
        destination,
        mode="replace",
        expected_before=old_identity,
    )

    def fail_validation() -> None:
        raise ValueError("injected post-publication validation failure")

    with pytest.raises(ValueError, match="post-publication"):
        transaction.commit(post_publish_check=fail_validation)

    assert publication.capture_identity(destination) == old_identity
    assert sorted(path.name for path in destination.iterdir()) == ["plot.png"]


def test_rollback_never_deletes_destination_changed_by_another_writer(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "manifest.json"
    destination.write_text("old\n", encoding="utf-8")
    transaction = publication.PublicationTransaction.begin(tmp_path, "manifest")
    staged = write_candidate(transaction, "manifest.json", "candidate\n")
    transaction.add(
        staged,
        destination,
        mode="replace",
        expected_before=publication.capture_identity(destination),
    )

    def external_write_then_fail() -> None:
        destination.write_text("external owner\n", encoding="utf-8")
        raise RuntimeError("injected external mutation")

    with pytest.raises(publication.TransactionRollbackError) as captured:
        transaction.commit(post_publish_check=external_write_then_fail)

    assert isinstance(captured.value.original_error, RuntimeError)
    assert destination.read_text(encoding="utf-8") == "external owner\n"
    assert publication.active_transactions(tmp_path) == (transaction.transaction_id,)

    results = publication.recover_incomplete_transactions(tmp_path)
    assert results[0].outcome == "blocked"
    assert destination.read_text(encoding="utf-8") == "external owner\n"


def test_silent_drift_after_entry_is_detected_before_committed_journal(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "manifest.json"
    destination.write_text("old\n", encoding="utf-8")
    transaction = publication.PublicationTransaction.begin(tmp_path, "final-recheck")
    staged = write_candidate(transaction, "manifest.json", "candidate\n")
    transaction.add(
        staged,
        destination,
        mode="replace",
        expected_before=publication.capture_identity(destination),
    )

    def mutate_without_raising(path: Path, index: int) -> None:
        path.write_text("external after entry\n", encoding="utf-8")

    with pytest.raises(publication.TransactionRollbackError):
        transaction.commit(after_publish_entry=mutate_without_raising)

    assert destination.read_text(encoding="utf-8") == "external after entry\n"
    assert publication.active_transactions(tmp_path) == (transaction.transaction_id,)


def test_recovery_rolls_back_unmarked_partial_publication(tmp_path: Path) -> None:
    destination = tmp_path / "scan.csv"
    destination.write_text("old\n", encoding="utf-8")
    old_identity = publication.capture_identity(destination)
    transaction = publication.PublicationTransaction.begin(tmp_path, "crash")
    staged = write_candidate(transaction, "scan.csv", "new\n")
    transaction.add(
        staged,
        destination,
        mode="replace",
        expected_before=old_identity,
    )
    entry = transaction._entries[0]
    transaction._status = "publishing"
    transaction._write_journal()
    os.replace(destination, entry.backup)
    os.replace(staged, destination)
    abandon_for_recovery(transaction)

    results = publication.recover_incomplete_transactions(tmp_path)

    assert results == (
        publication.RecoveryResult(transaction.transaction_id, "rolled_back"),
    )
    assert publication.capture_identity(destination) == old_identity
    assert publication.active_transactions(tmp_path) == ()


def test_recovery_finalizes_durable_commit_without_rolling_it_back(tmp_path: Path) -> None:
    destination = tmp_path / "result.txt"
    destination.write_text("old\n", encoding="utf-8")
    transaction = publication.PublicationTransaction.begin(tmp_path, "cleanup")
    staged = write_candidate(transaction, "result.txt", "new\n")
    candidate = publication.capture_identity(staged)
    transaction.add(
        staged,
        destination,
        mode="replace",
        expected_before=publication.capture_identity(destination),
    )
    entry = transaction._entries[0]
    os.replace(destination, entry.backup)
    os.replace(staged, destination)
    transaction._status = "committed"
    transaction._write_journal()
    abandon_for_recovery(transaction)

    results = publication.recover_incomplete_transactions(tmp_path)

    assert results == (
        publication.RecoveryResult(transaction.transaction_id, "finalized"),
    )
    assert publication.capture_identity(destination) == candidate
    assert publication.active_transactions(tmp_path) == ()


def test_recovery_rejects_authenticated_directory_with_forged_journal_claim(
    tmp_path: Path,
) -> None:
    """A journal cannot self-authorize moving an arbitrary current destination."""

    victim = tmp_path / "manifest.json"
    victim.write_text("authoritative\n", encoding="utf-8")
    transaction = publication.PublicationTransaction.begin(tmp_path, "forged")
    forged = {
        "version": publication.JOURNAL_VERSION,
        "transaction_id": transaction.transaction_id,
        "scope": "forged",
        "status": "publishing",
        "cleanup_token": transaction.cleanup_token,
        "generation": transaction._journal_generation,
        "entries": [
            {
                "staged": "staging/loot",
                "destination": "manifest.json",
                "backup": "backups/000000",
                "mode": "create_only",
                "expected_before": asdict(publication.PathIdentity(kind="absent")),
                "candidate": asdict(publication.capture_identity(victim)),
            }
        ],
    }
    publication._atomic_json_write(transaction.journal_path, forged)
    abandon_for_recovery(transaction)

    results = publication.recover_incomplete_transactions(tmp_path)

    assert results[0].outcome == "blocked"
    assert "attestation" in "; ".join(results[0].issues)
    assert victim.read_text(encoding="utf-8") == "authoritative\n"
    assert publication.active_transactions(tmp_path) == (transaction.transaction_id,)


def test_valid_looking_raw_transaction_without_active_owner_is_blocked(
    tmp_path: Path,
) -> None:
    victim = tmp_path / "manifest.json"
    victim.write_text("authoritative\n", encoding="utf-8")
    root = tmp_path / publication.TRANSACTION_ROOT_NAME
    transaction_id = "raw-" + "a" * 32
    transaction_dir = root / transaction_id
    (transaction_dir / "staging").mkdir(parents=True)
    (transaction_dir / "backups").mkdir()
    payload = {
        "version": publication.JOURNAL_VERSION,
        "transaction_id": transaction_id,
        "scope": "raw",
        "status": "publishing",
        "cleanup_token": "b" * 32,
        "generation": 0,
        "entries": [
            {
                "staged": "staging/loot",
                "destination": "manifest.json",
                "backup": "backups/000000",
                "mode": "create_only",
                "expected_before": asdict(publication.PathIdentity(kind="absent")),
                "candidate": asdict(publication.capture_identity(victim)),
            }
        ],
    }
    publication._atomic_json_write(transaction_dir / publication.JOURNAL_NAME, payload)

    results = publication.recover_incomplete_transactions(tmp_path)

    assert results[0].outcome == "blocked"
    assert "attestation" in "; ".join(results[0].issues)
    assert victim.read_text(encoding="utf-8") == "authoritative\n"


def test_owner_first_crash_keeps_previous_journal_generation_recoverable(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "result.txt"
    transaction = publication.PublicationTransaction.begin(tmp_path, "owner-first")
    staged = write_candidate(transaction, "result.txt", "candidate\n")
    transaction.add(
        staged,
        destination,
        mode="create_only",
        expected_before=publication.capture_identity(destination),
    )
    future_generation = transaction._journal_generation + 1
    future_payload = transaction._journal_payload(generation=future_generation)
    future_payload["status"] = "publishing"
    publication._write_active_attestation(
        transaction.root,
        transaction.transaction_dir,
        transaction_id=transaction.transaction_id,
        scope=transaction.scope,
        token=transaction.cleanup_token,
        generation=future_generation,
        journal_bytes=publication._json_bytes(future_payload),
    )
    # Model death after the future attestation is durable but before journal
    # replacement. The prior journal/attestation pair remains authoritative.
    abandon_for_recovery(transaction)

    results = publication.recover_incomplete_transactions(tmp_path)

    assert results == (
        publication.RecoveryResult(transaction.transaction_id, "rolled_back"),
    )
    assert not destination.exists()
    assert publication.active_transactions(tmp_path) == ()


def test_active_owner_inode_mismatch_blocks_replaced_transaction_tree(
    tmp_path: Path,
) -> None:
    transaction = publication.PublicationTransaction.begin(tmp_path, "active-inode")
    transaction_id = transaction.transaction_id
    journal_bytes = transaction.journal_path.read_bytes()
    transaction_dir = transaction.transaction_dir
    abandon_for_recovery(transaction)

    retired = tmp_path / "retired-authentic-transaction"
    transaction_dir.rename(retired)
    transaction_dir.mkdir()
    (transaction_dir / "staging").mkdir()
    (transaction_dir / "backups").mkdir()
    (transaction_dir / publication.JOURNAL_NAME).write_bytes(journal_bytes)
    sentinel = transaction_dir / "preserve.txt"
    sentinel.write_text("replacement owner\n", encoding="utf-8")

    results = publication.recover_incomplete_transactions(tmp_path)

    assert results[0].outcome == "blocked"
    assert "attestation" in "; ".join(results[0].issues)
    assert sentinel.read_text(encoding="utf-8") == "replacement owner\n"
    assert publication.active_transactions(tmp_path) == (transaction_id,)


def test_crash_after_garbage_rename_before_active_owner_retirement_recovers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "result.txt"
    destination.write_text("old\n", encoding="utf-8")
    transaction = publication.PublicationTransaction.begin(tmp_path, "retire-window")
    staged = write_candidate(transaction, "result.txt", "new\n")
    candidate = publication.capture_identity(staged)
    transaction.add(
        staged,
        destination,
        mode="replace",
        expected_before=publication.capture_identity(destination),
    )
    original_purge = publication._purge_active_owner_records

    def interrupt_active_owner_retirement(*args: object, **kwargs: object) -> None:
        raise OSError("injected death before active owner retirement")

    monkeypatch.setattr(
        publication,
        "_purge_active_owner_records",
        interrupt_active_owner_retirement,
    )
    with pytest.raises(publication.TransactionCommittedCleanupError):
        transaction.commit()

    assert publication.capture_identity(destination) == candidate
    assert publication.active_transactions(tmp_path) == ()
    assert publication.pending_transaction_cleanups(tmp_path) == (
        (transaction.transaction_id, "finalized"),
    )
    assert (
        tmp_path
        / publication.TRANSACTION_ROOT_NAME
        / publication.ACTIVE_OWNER_ROOT_NAME
    ).is_dir()

    monkeypatch.setattr(publication, "_purge_active_owner_records", original_purge)
    recovered = publication.recover_incomplete_transactions(tmp_path)
    assert recovered == (
        publication.RecoveryResult(transaction.transaction_id, "finalized"),
    )
    assert publication.pending_transaction_cleanups(tmp_path) == ()


def test_interrupted_private_cleanup_cannot_poison_committed_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "result.txt"
    destination.write_text("old\n", encoding="utf-8")
    transaction = publication.PublicationTransaction.begin(tmp_path, "cleanup-crash")
    staged = write_candidate(transaction, "result.txt", "new\n")
    candidate = publication.capture_identity(staged)
    transaction.add(
        staged,
        destination,
        mode="replace",
        expected_before=publication.capture_identity(destination),
    )
    original_rmtree = publication.shutil.rmtree

    def interrupt_after_journal_removal(path: Path) -> None:
        journal = Path(path) / publication.JOURNAL_NAME
        journal.unlink()
        raise OSError("injected interruption during recursive private cleanup")

    monkeypatch.setattr(publication.shutil, "rmtree", interrupt_after_journal_removal)
    with pytest.raises(publication.TransactionCommittedCleanupError):
        transaction.commit()

    assert publication.capture_identity(destination) == candidate
    assert publication.active_transactions(tmp_path) == ()
    assert publication.pending_transaction_cleanups(tmp_path) == (
        (transaction.transaction_id, "finalized"),
    )

    script = Path(__file__).resolve().parents[2] / "scripts" / "recover_publication_transactions.py"
    inspected = subprocess.run(
        [
            sys.executable,
            str(script),
            "--project-dir",
            str(tmp_path),
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
    )
    assert inspected.returncode == 1
    assert json.loads(inspected.stdout)[0]["outcome"] == "cleanup_pending"

    monkeypatch.setattr(publication.shutil, "rmtree", original_rmtree)
    recovered = publication.recover_incomplete_transactions(tmp_path)
    assert recovered == (
        publication.RecoveryResult(transaction.transaction_id, "finalized"),
    )
    assert publication.pending_transaction_cleanups(tmp_path) == ()

    with publication.PublicationTransaction.begin(tmp_path, "clean-retry") as retry:
        newer = write_candidate(retry, "result.txt", "newer\n")
        retry.add(
            newer,
            destination,
            mode="replace",
            expected_before=publication.capture_identity(destination),
        )
        retry.commit()
    assert destination.read_text(encoding="utf-8") == "newer\n"


def test_constructor_cleanup_quarantines_partial_directory_without_journal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_atomic_json_write = publication._atomic_json_write
    original_rmtree = publication.shutil.rmtree

    def fail_initial_journal(path: Path, payload: dict[str, object]) -> None:
        raise OSError("injected initial journal failure")

    def interrupt_private_cleanup(path: Path) -> None:
        raise OSError("injected partial-construction cleanup interruption")

    monkeypatch.setattr(publication, "_atomic_json_write", fail_initial_journal)
    monkeypatch.setattr(publication.shutil, "rmtree", interrupt_private_cleanup)
    with pytest.raises(publication.TransactionRollbackError) as captured:
        publication.PublicationTransaction.begin(tmp_path, "construction")

    assert isinstance(captured.value.original_error, OSError)
    assert publication.active_transactions(tmp_path) == ()
    pending = publication.pending_transaction_cleanups(tmp_path)
    assert len(pending) == 1
    assert pending[0][1] == "rolled_back"

    monkeypatch.setattr(publication, "_atomic_json_write", original_atomic_json_write)
    monkeypatch.setattr(publication.shutil, "rmtree", original_rmtree)
    recovered = publication.recover_incomplete_transactions(tmp_path)
    assert recovered == (
        publication.RecoveryResult(pending[0][0], "rolled_back"),
    )
    assert publication.active_transactions(tmp_path) == ()


def test_recovery_refuses_to_finalize_committed_journal_after_external_drift(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "result.txt"
    destination.write_text("old\n", encoding="utf-8")
    transaction = publication.PublicationTransaction.begin(tmp_path, "committed-drift")
    staged = write_candidate(transaction, "result.txt", "new\n")
    transaction.add(
        staged,
        destination,
        mode="replace",
        expected_before=publication.capture_identity(destination),
    )
    entry = transaction._entries[0]
    os.replace(destination, entry.backup)
    os.replace(staged, destination)
    transaction._status = "committed"
    transaction._write_journal()
    destination.write_text("external after commit\n", encoding="utf-8")
    abandon_for_recovery(transaction)

    results = publication.recover_incomplete_transactions(tmp_path)

    assert results[0].outcome == "blocked"
    assert "no longer matches" in results[0].issues[0]
    assert destination.read_text(encoding="utf-8") == "external after commit\n"
    assert publication.active_transactions(tmp_path) == (transaction.transaction_id,)


def test_corrupt_journal_is_reported_and_left_for_manual_recovery(tmp_path: Path) -> None:
    transaction = publication.PublicationTransaction.begin(tmp_path, "corrupt")
    transaction.journal_path.write_text('{"status": "staging", "status": 2}\n')
    abandon_for_recovery(transaction)

    results = publication.recover_incomplete_transactions(tmp_path)

    assert len(results) == 1
    assert results[0].outcome == "blocked"
    assert "duplicate JSON key" in results[0].issues[0]
    assert publication.active_transactions(tmp_path) == (transaction.transaction_id,)


def test_unknown_transaction_root_entry_blocks_readers_and_recovery(tmp_path: Path) -> None:
    root = tmp_path / publication.TRANSACTION_ROOT_NAME
    root.mkdir()
    unexpected = root / "unexpected-owner"
    unexpected.write_text("do not delete\n", encoding="utf-8")

    assert publication.active_transactions(tmp_path) == (unexpected.name,)
    with pytest.raises(publication.ActiveTransactionError):
        publication.assert_no_active_transactions(tmp_path)

    results = publication.recover_incomplete_transactions(tmp_path)

    assert results == (
        publication.RecoveryResult(
            unexpected.name,
            "blocked",
            ("transaction-root entry is not a real directory",),
        ),
    )
    assert unexpected.read_text(encoding="utf-8") == "do not delete\n"


def test_valid_looking_garbage_without_owner_record_is_never_deleted(
    tmp_path: Path,
) -> None:
    garbage_entry = (
        tmp_path
        / publication.TRANSACTION_ROOT_NAME
        / publication.GARBAGE_ROOT_NAME
        / ("finalized--scan-" + "a" * 32)
    )
    garbage_entry.mkdir(parents=True)
    sentinel = garbage_entry / "user-owned.txt"
    sentinel.write_text("preserve me\n", encoding="utf-8")

    with pytest.raises(publication.UnsafePublicationPath, match="ownership"):
        publication.pending_transaction_cleanups(tmp_path)
    recovered = publication.recover_incomplete_transactions(tmp_path)

    assert recovered[0].outcome == "blocked"
    assert "ownership" in "; ".join(recovered[0].issues)
    assert sentinel.read_text(encoding="utf-8") == "preserve me\n"


def test_cleanup_owner_inode_mismatch_blocks_replaced_garbage_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "result.txt"
    destination.write_text("old\n", encoding="utf-8")
    transaction = publication.PublicationTransaction.begin(tmp_path, "inode-guard")
    staged = write_candidate(transaction, "result.txt", "new\n")
    transaction.add(
        staged,
        destination,
        mode="replace",
        expected_before=publication.capture_identity(destination),
    )
    original_rmtree = publication.shutil.rmtree

    def interrupt_cleanup(path: Path) -> None:
        raise OSError("interrupt cleanup")

    monkeypatch.setattr(publication.shutil, "rmtree", interrupt_cleanup)
    with pytest.raises(publication.TransactionCommittedCleanupError):
        transaction.commit()

    record = publication._cleanup_records(tmp_path)[0]
    assert record.garbage_entry is not None
    retired_private = tmp_path / "retired-private"
    record.garbage_entry.rename(retired_private)
    replacement = record.garbage_entry
    replacement.mkdir()
    sentinel = replacement / "user-owned.txt"
    sentinel.write_text("preserve replacement\n", encoding="utf-8")
    monkeypatch.setattr(publication.shutil, "rmtree", original_rmtree)

    recovered = publication.recover_incomplete_transactions(tmp_path)

    assert recovered[0].outcome == "blocked"
    assert "inode" in "; ".join(recovered[0].issues)
    assert sentinel.read_text(encoding="utf-8") == "preserve replacement\n"


def test_symlinked_transaction_root_is_rejected_fail_closed(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / publication.TRANSACTION_ROOT_NAME).symlink_to(
        outside,
        target_is_directory=True,
    )

    with pytest.raises(publication.UnsafePublicationPath, match="transaction root"):
        publication.active_transactions(tmp_path)
    with pytest.raises(publication.UnsafePublicationPath, match="transaction root"):
        publication.publication_lock(tmp_path, "unsafe").acquire()


def test_candidate_drift_and_unsafe_paths_fail_without_destination_mutation(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "result.txt"
    destination.write_text("old\n", encoding="utf-8")
    old_identity = publication.capture_identity(destination)
    transaction = publication.PublicationTransaction.begin(tmp_path, "drift")
    staged = write_candidate(transaction, "result.txt", "candidate\n")
    transaction.add(
        staged,
        destination,
        mode="replace",
        expected_before=old_identity,
    )
    staged.write_text("changed after add\n", encoding="utf-8")

    with pytest.raises(publication.CompareAndSwapError, match="staged candidate changed"):
        transaction.commit()

    assert publication.capture_identity(destination) == old_identity
    assert publication.active_transactions(tmp_path) == ()

    with publication.PublicationTransaction.begin(tmp_path, "paths") as paths_transaction:
        with pytest.raises(publication.UnsafePublicationPath):
            paths_transaction.stage_path("../escape")
        safe = write_candidate(paths_transaction, "safe.txt", "safe\n")
        with pytest.raises(publication.UnsafePublicationPath):
            paths_transaction.add(
                safe,
                tmp_path.parent / "outside.txt",
                mode="create_only",
                expected_before=publication.PathIdentity(kind="absent"),
            )


@pytest.mark.parametrize("unsafe", [".", "./x", "x//y", "x/./y", "x\\y"])
def test_staging_paths_reject_normalization_and_backslash_aliases(
    tmp_path: Path,
    unsafe: str,
) -> None:
    with publication.PublicationTransaction.begin(tmp_path, "strict-path") as transaction:
        with pytest.raises(publication.UnsafePublicationPath):
            transaction.stage_path(unsafe)


def test_internal_parent_symlink_cannot_redirect_publication(tmp_path: Path) -> None:
    real_parent = tmp_path / "other-results"
    victim = real_parent / "analysis-001"
    victim.mkdir(parents=True)
    sentinel = victim / "sentinel.txt"
    sentinel.write_text("external owner\n", encoding="utf-8")
    requested_parent = tmp_path / "numerics" / "scan-results"
    requested_parent.parent.mkdir()
    requested_parent.symlink_to(real_parent, target_is_directory=True)
    requested = requested_parent / "analysis-001"

    transaction = publication.PublicationTransaction.begin(tmp_path, "symlink-parent")
    staged = transaction.stage_path("candidate")
    staged.mkdir()
    (staged / "new.txt").write_text("candidate\n", encoding="utf-8")
    with pytest.raises(publication.UnsafePublicationPath, match="symlink component"):
        transaction.add(
            staged,
            requested,
            mode="replace",
            expected_before=publication.capture_identity(requested),
        )
    transaction.abort()

    assert sentinel.read_text(encoding="utf-8") == "external owner\n"
    assert sorted(path.name for path in victim.iterdir()) == ["sentinel.txt"]


def test_anchor_path_alias_maps_only_the_trusted_root(tmp_path: Path) -> None:
    anchor = tmp_path / "real-anchor"
    anchor.mkdir()
    alias = tmp_path / "anchor-alias"
    alias.symlink_to(anchor, target_is_directory=True)
    destination = alias / "result.txt"

    with publication.PublicationTransaction.begin(alias, "root-alias") as transaction:
        staged = write_candidate(transaction, "result.txt", "published\n")
        transaction.add(
            staged,
            destination,
            mode="create_only",
            expected_before=publication.PathIdentity(kind="absent"),
        )
        transaction.commit()

    assert (anchor / "result.txt").read_text(encoding="utf-8") == "published\n"


def test_unsupported_lock_platform_fails_explicitly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock = publication.publication_lock(tmp_path, "portable")
    monkeypatch.setattr(publication.os, "name", "unsupported")

    with pytest.raises(publication.UnsupportedTransactionPlatform, match="POSIX flock"):
        lock.acquire()
