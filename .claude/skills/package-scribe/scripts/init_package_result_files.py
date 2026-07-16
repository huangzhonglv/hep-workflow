#!/usr/bin/env python3
"""Initialize package-scribe result files from skill-local templates."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import math
import os
import re
import shlex
import shutil
import socket
import stat
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from _publication_transaction import (
    PathIdentity,
    PublicationLock,
    PublicationTransactionError,
    PublicationTransaction,
    TransactionCommittedCleanupError,
    atomic_rename_no_replace,
    capture_identity,
    publication_lock,
)


DETERMINISTIC_TEMPLATES = (
    ("request.md.tmpl", "request.md"),
    ("result-summary.md.tmpl", "result-summary.md"),
)
RUN_INSTRUCTIONS_TEMPLATE = ("run-instructions.md.tmpl", "run-instructions.md")
BATCH_TEMPLATES = (
    ("result-python.py.tmpl", "result-python.py"),
    ("result-meta.json.tmpl", "result-meta.json"),
)
BATCH_MANAGED_FILES = (
    "request.md",
    "result-summary.md",
    "run-instructions.md",
    "result.wl",
    "result-python.py",
    "result-meta.json",
    "wolfram-output.txt",
)
RESERVATION_FILENAME = ".reservation.json"
BATCH_ATTEMPT_ROOT = ".hep-workflow-package-attempts"
TEST_FAILURE_ENV = "HEP_WORKFLOW_TEST_FAIL_PACKAGE_INIT_AFTER"
RESULT_DIR_PATTERN = re.compile(r"^package-result[0-9]{3}$")
TASK_DIR_PATTERN = re.compile(r"^task-[0-9]{3}$")
ATTEMPT_ID_PATTERN = re.compile(r"^[a-f0-9]{32}$")


def wolframscript_argv(executable: str, result_wl_path: Path) -> list[str]:
    """Return the exact argv for Wolfram execution; never a shell command."""

    if not isinstance(executable, str) or not executable:
        raise ValueError("Wolfram executable path must be a non-empty string")
    argv = [executable, "-file", os.fspath(result_wl_path)]
    if any("\x00" in argument for argument in argv):
        raise ValueError("Wolfram argv must not contain NUL bytes")
    return argv


def render_posix_command(argv: Sequence[str]) -> str:
    """Render argv for POSIX copy/paste display only."""

    if not argv or any(not isinstance(argument, str) for argument in argv):
        raise ValueError("POSIX command display requires a non-empty string argv")
    return shlex.join(argv)


@dataclass(frozen=True)
class InitializationResult:
    """Paths and ownership token produced by one initialization."""

    result_dir: Path
    output_paths: tuple[Path, ...]
    final_task_dir: Path | None = None
    attempt_id: str | None = None


class ExitOneArgumentParser(argparse.ArgumentParser):
    """Keep the shell helper's exit status for malformed invocations."""

    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(1, f"{self.prog}: error: {message}\n")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = ExitOneArgumentParser(
        description="Initialize package-scribe result files from templates."
    )
    parser.add_argument("result_dir", nargs="?", help="Interactive result directory.")
    parser.add_argument(
        "--task-dir",
        help=(
            "Canonical batch task directory. Templates are written only to a "
            "new owned attempt directory; this path is not changed."
        ),
    )
    parser.add_argument(
        "--blocked",
        action="store_true",
        help="Initialize only request.md and result-summary.md.",
    )
    parser.add_argument(
        "--attempt-id",
        help=(
            "Reservation attempt token. It is required to resume a failed "
            "allocator-owned interactive directory."
        ),
    )
    parser.add_argument(
        "--format",
        choices=("paths", "json"),
        default="paths",
        help="Print initialized paths (default) or one machine-readable JSON object.",
    )
    args = parser.parse_args(argv)

    if bool(args.result_dir) == bool(args.task_dir):
        parser.error("provide exactly one of RESULT_DIR or --task-dir TASK_DIR")
    if args.task_dir and args.attempt_id:
        parser.error("--attempt-id is only valid for interactive RESULT_DIR resume")
    return args


def _fsync_directory(path: Path) -> None:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    fd = os.open(path, flags)
    try:
        if not stat.S_ISDIR(os.fstat(fd).st_mode):
            raise ValueError(f"cannot fsync non-directory path: {path}")
        os.fsync(fd)
    finally:
        os.close(fd)


def _regular_file_exists(path: Path, label: str) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"{label} must be a regular file, not a symlink: {path}")
    return True


def _resolve_result_directory(value: str) -> Path:
    """Resolve a CLI destination without following a final ownership symlink."""

    expanded = Path(value).expanduser()
    lexical = expanded if expanded.is_absolute() else Path.cwd() / expanded
    if lexical.is_symlink():
        raise ValueError(f"result directory must not be a symlink: {lexical}")
    parent = lexical.parent.resolve(strict=False)
    result_dir = parent / lexical.name
    if result_dir.exists():
        metadata = result_dir.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise ValueError(
                f"result directory must be absent or a real directory: {result_dir}"
            )
    return result_dir


def _batch_project_anchor(result_dir: Path) -> Path:
    """Bind a batch task to its canonical workspace project lock domain."""

    if (
        TASK_DIR_PATTERN.fullmatch(result_dir.name) is None
        or result_dir.parent.name != "calculations"
    ):
        raise ValueError(
            "batch task directory must be <project>/calculations/task-NNN"
        )
    project_dir = result_dir.parent.parent
    manifest_path = project_dir / "manifest.json"
    if not _regular_file_exists(manifest_path, "workspace project manifest"):
        raise FileNotFoundError(
            f"workspace project manifest not found for batch task: {manifest_path}"
        )
    manifest = _strict_json_load(manifest_path)
    if not isinstance(manifest, dict) or manifest.get("manifest_version") != 2:
        raise ValueError("batch task requires a manifest_version=2 workspace project")
    return project_dir


def _json_bytes(payload: dict[str, object]) -> bytes:
    return (
        json.dumps(
            payload,
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _identity_payload(identity: PathIdentity) -> dict[str, object]:
    return dict(asdict(identity))


def _require_real_directory(path: Path, label: str) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"{label} does not exist: {path}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise ValueError(f"{label} must be a real directory, not a symlink: {path}")


def _allocate_batch_attempt(
    project_dir: Path,
    final_task_dir: Path,
    *,
    blocked: bool,
) -> tuple[Path, dict[str, object]]:
    """Atomically reserve a private batch generation without touching the final task."""

    calculations_dir = final_task_dir.parent
    _require_real_directory(calculations_dir, "calculations directory")
    if final_task_dir.exists():
        _require_real_directory(final_task_dir, "existing batch task directory")
        for name in BATCH_MANAGED_FILES:
            managed = final_task_dir / name
            if managed.exists() or managed.is_symlink():
                _regular_file_exists(managed, f"existing managed result {name}")
    attempt_root = project_dir / BATCH_ATTEMPT_ROOT
    try:
        attempt_root.mkdir(mode=0o700, exist_ok=False)
        _fsync_directory(project_dir)
    except FileExistsError:
        _require_real_directory(attempt_root, "package-scribe attempt root")

    attempt_id = uuid.uuid4().hex
    if ATTEMPT_ID_PATTERN.fullmatch(attempt_id) is None:  # pragma: no cover
        raise RuntimeError("generated an invalid package-scribe attempt token")
    attempt_dir = attempt_root / f"{final_task_dir.name}--{attempt_id}"
    attempt_dir.mkdir(mode=0o700, exist_ok=False)
    _fsync_directory(attempt_root)

    reservation: dict[str, object] = {
        "version": 1,
        "kind": "package-scribe-batch-attempt",
        "task_id": final_task_dir.name,
        "attempt_id": attempt_id,
        "final_task_path": final_task_dir.relative_to(project_dir).as_posix(),
        "baseline_identity": _identity_payload(capture_identity(final_task_dir)),
        "history_event_id": uuid.uuid4().hex,
        "owner": {
            "hostname": socket.gethostname(),
            "pid": os.getpid(),
        },
        "created_at": datetime.now(timezone.utc).isoformat(),
        "state": "reserved",
        "blocked": blocked,
    }
    try:
        _atomic_create_bytes(
            attempt_dir / RESERVATION_FILENAME,
            _json_bytes(reservation),
        )
    except BaseException:
        # The directory itself is the exclusive reservation. Leave an opaque,
        # occupied attempt on metadata failure; never recycle unknown ownership.
        raise
    return attempt_dir, reservation


def _stage_bytes(path: Path, content: bytes) -> Path:
    fd, raw_path = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    staged = Path(raw_path)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        if staged.exists():
            staged.unlink()
        raise
    return staged


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    staged = _stage_bytes(path, content)
    try:
        os.replace(staged, path)
        _fsync_directory(path.parent)
    finally:
        if staged.exists():
            staged.unlink()


def _atomic_create_bytes(path: Path, content: bytes) -> None:
    staged = _stage_bytes(path, content)
    try:
        atomic_rename_no_replace(staged, path)
        _fsync_directory(path.parent)
    finally:
        if staged.exists():
            staged.unlink()


def _rendered_template_bytes(
    template_path: Path,
    replacements: dict[str, str],
) -> bytes:
    rendered = template_path.read_text(encoding="utf-8")
    for placeholder, value in replacements.items():
        rendered = rendered.replace(placeholder, value)
    return (rendered.rstrip("\n") + "\n").encode("utf-8")


def _strict_json_load(path: Path) -> object:
    def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key {key!r} in {path}")
            result[key] = value
        return result

    def reject_constant(value: str) -> object:
        raise ValueError(f"non-standard JSON constant {value!r} in {path}")

    payload = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=reject_duplicate_keys,
        parse_constant=reject_constant,
    )

    def check_finite(value: object) -> None:
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"non-finite JSON number in {path}")
        if isinstance(value, dict):
            for child in value.values():
                check_finite(child)
        elif isinstance(value, list):
            for child in value:
                check_finite(child)

    check_finite(payload)
    return payload


def _load_reservation(
    result_dir: Path,
    *,
    attempt_id: str | None,
) -> dict[str, object] | None:
    path = result_dir / RESERVATION_FILENAME
    if not path.exists():
        if (
            RESULT_DIR_PATTERN.fullmatch(result_dir.name)
            and result_dir.parent.name == "package-scribe"
        ):
            raise FileNotFoundError(
                "allocator-owned result directory is missing reservation metadata"
            )
        return None
    payload = _strict_json_load(path)
    if not isinstance(payload, dict):
        raise ValueError(f"invalid reservation metadata: {path}")
    if payload.get("kind") != "package-scribe-interactive-result":
        raise ValueError(f"unexpected reservation kind in {path}")
    if payload.get("version") != 1:
        raise ValueError(f"unexpected reservation version in {path}")
    if payload.get("resource_id") != result_dir.name:
        raise ValueError(f"reservation resource_id does not match {result_dir.name}")
    recorded_attempt = payload.get("attempt_id")
    if not isinstance(recorded_attempt, str) or not recorded_attempt:
        raise ValueError(f"reservation is missing attempt_id: {path}")
    if attempt_id is None:
        raise PermissionError(
            "allocator-owned result directory requires --attempt-id"
        )
    state = payload.get("state")
    if state not in {"reserved", "failed", "initialized"}:
        raise ValueError(f"unexpected reservation state {state!r} in {path}")
    if attempt_id is not None and attempt_id != recorded_attempt:
        raise PermissionError("reservation attempt token does not match")
    if state == "failed" and attempt_id is None:
        raise PermissionError(
            "failed reservation requires --attempt-id for authenticated resume"
        )
    return payload


def _publish_all_or_none(
    anchor: Path,
    result_dir: Path,
    candidates: list[tuple[Path, bytes]],
    *,
    lock: PublicationLock,
) -> None:
    """Publish all managed files through the shared durable transaction."""

    if len({path for path, _ in candidates}) != len(candidates):
        raise ValueError("initializer candidate paths must be unique")
    if not candidates:
        return
    with PublicationTransaction.begin(
        anchor,
        "package-init",
        lock=lock,
    ) as transaction:
        for destination, content in candidates:
            staged = transaction.stage_path(
                f"{result_dir.name}/{destination.name}"
            )
            staged.write_bytes(content)
            transaction.add(
                staged,
                destination,
                mode="replace",
                expected_before=capture_identity(destination),
            )

        failure_target = os.environ.get(TEST_FAILURE_ENV)

        def after_publish(destination: Path, index: int) -> None:
            if failure_target in {str(index), destination.name}:
                raise OSError(
                    f"injected package initializer failure after {destination.name}"
                )

        transaction.commit(after_publish_entry=after_publish)


def initialize_result_files(
    result_dir: Path,
    *,
    batch_mode: bool,
    blocked: bool,
    attempt_id: str | None = None,
) -> InitializationResult:
    template_dir = Path(__file__).resolve().parent.parent / "templates"
    if not template_dir.is_dir():
        raise FileNotFoundError(f"template directory not found: {template_dir}")

    result_dir_preexisted = result_dir.exists()
    if not batch_mode:
        result_dir.mkdir(parents=True, exist_ok=True)
    try:
        transaction_anchor = (
            _batch_project_anchor(result_dir)
            if batch_mode
            else result_dir.parent
        )
        with publication_lock(
            transaction_anchor,
            "package-scribe",
            blocking=True,
        ) as lock:
            if batch_mode:
                working_dir, reservation = _allocate_batch_attempt(
                    transaction_anchor,
                    result_dir,
                    blocked=blocked,
                )
                owner_attempt_id = str(reservation["attempt_id"])
                rendered_result_dir = result_dir
            else:
                working_dir = result_dir
                reservation = _load_reservation(result_dir, attempt_id=attempt_id)
                owner_attempt_id = attempt_id
                rendered_result_dir = result_dir

            result_wl_path = rendered_result_dir / "result.wl"
            wolframscript_bin = (
                os.environ.get("WOLFRAMSCRIPT_BIN")
                or shutil.which("wolframscript")
                or "wolframscript"
            )
            wolfram_argv = wolframscript_argv(
                wolframscript_bin,
                result_wl_path,
            )
            replacements = {
                "{{GENERATED_AT}}": datetime.now().astimezone().strftime(
                    "%Y-%m-%d %H:%M:%S %z"
                ),
                "{{RESULT_DIR}}": str(rendered_result_dir),
                "{{RESULT_WL_PATH}}": str(result_wl_path),
                "{{RUN_COMMAND}}": render_posix_command(wolfram_argv),
            }

            candidates: list[tuple[Path, bytes]] = []
            output_paths: list[Path] = []

            def add_rendered(template_name: str, output_name: str) -> None:
                output_path = working_dir / output_name
                if output_path.is_symlink() or (
                    output_path.exists() and not output_path.is_file()
                ):
                    raise ValueError(
                        f"managed output must be absent or a regular file: {output_path}"
                    )
                if output_path.exists() and not batch_mode:
                    print(f"skip existing: {output_path}", file=sys.stderr)
                    return
                candidates.append(
                    (
                        output_path,
                        _rendered_template_bytes(
                            template_dir / template_name,
                            replacements,
                        ),
                    )
                )
                output_paths.append(output_path)

            for template_name, output_name in DETERMINISTIC_TEMPLATES:
                add_rendered(template_name, output_name)

            if not blocked:
                add_rendered(*RUN_INSTRUCTIONS_TEMPLATE)

            if batch_mode and not blocked:
                for template_name, output_name in BATCH_TEMPLATES:
                    output_path = working_dir / output_name
                    if output_path.is_symlink() or (
                        output_path.exists() and not output_path.is_file()
                    ):
                        raise ValueError(
                            f"managed output must be absent or a regular file: {output_path}"
                        )
                    candidates.append((output_path, (template_dir / template_name).read_bytes()))
                    output_paths.append(output_path)

            if reservation is not None:
                updated_reservation = dict(reservation)
                updated_reservation["state"] = "initialized"
                updated_reservation["initialized_at"] = datetime.now().astimezone().isoformat()
                updated_reservation["blocked"] = blocked
                candidates.append(
                    (
                        working_dir / RESERVATION_FILENAME,
                        _json_bytes(updated_reservation),
                    )
                )

            try:
                _publish_all_or_none(
                    transaction_anchor,
                    working_dir,
                    candidates,
                    lock=lock,
                )
            except TransactionCommittedCleanupError as exc:
                print(
                    "warning: result-file initialization committed successfully, but "
                    f"private cleanup is pending for transaction {exc.transaction_id}: "
                    f"{exc.cleanup_error}. Do not retry this command; use "
                    "recover_publication_transactions.py for the same publication anchor.",
                    file=sys.stderr,
                )
            except Exception:
                if reservation is not None:
                    reservation_path = working_dir / RESERVATION_FILENAME
                    current = _strict_json_load(reservation_path)
                    if (
                        isinstance(current, dict)
                        and current.get("attempt_id") == owner_attempt_id
                        and current.get("state") == "reserved"
                    ):
                        current["state"] = "failed"
                        current["failed_at"] = datetime.now().astimezone().isoformat()
                        _atomic_write_bytes(
                            reservation_path,
                            _json_bytes(current),
                        )
                raise
            return InitializationResult(
                result_dir=working_dir,
                output_paths=tuple(output_paths),
                final_task_dir=result_dir if batch_mode else None,
                attempt_id=owner_attempt_id if batch_mode else None,
            )
    except Exception:
        if (
            not batch_mode
            and not result_dir_preexisted
            and result_dir.exists()
            and not any(result_dir.iterdir())
        ):
            result_dir.rmdir()
        raise


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        result_dir = _resolve_result_directory(args.task_dir or args.result_dir)
        initialized = initialize_result_files(
            result_dir,
            batch_mode=args.task_dir is not None,
            blocked=args.blocked,
            attempt_id=args.attempt_id,
        )
    except TransactionCommittedCleanupError as exc:
        print(
            "warning: result-file initialization committed successfully, but "
            f"private cleanup is pending for transaction {exc.transaction_id}: "
            f"{exc.cleanup_error}. Do not retry this command; use "
            "recover_publication_transactions.py for the same publication anchor.",
            file=sys.stderr,
        )
        return 0
    except (
        OSError,
        UnicodeError,
        ValueError,
        PermissionError,
        json.JSONDecodeError,
        PublicationTransactionError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.format == "json":
        payload: dict[str, object] = {
            "path": str(initialized.result_dir),
            "output_paths": [str(path) for path in initialized.output_paths],
        }
        if initialized.final_task_dir is not None:
            payload["final_task_dir"] = str(initialized.final_task_dir)
        if initialized.attempt_id is not None:
            payload["attempt_id"] = initialized.attempt_id
        print(json.dumps(payload, sort_keys=True))
    else:
        for output_path in initialized.output_paths:
            print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
