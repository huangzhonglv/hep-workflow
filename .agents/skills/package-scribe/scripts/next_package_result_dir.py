#!/usr/bin/env python3
"""Allocate the next standalone package-scribe result directory."""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import stat
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

RESULT_DIR_PATTERN = re.compile(r"^package-result([0-9]{3})$")
ATTEMPT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
RESERVATION_FILENAME = ".reservation.json"
MIN_RESULT_INDEX = 1
MAX_RESULT_INDEX = 999


def find_repo_root(base_dir: Path) -> Path:
    """Return the nearest ancestor containing workspace/, or base_dir."""
    for candidate in (base_dir, *base_dir.parents):
        workspace = candidate / "workspace"
        try:
            metadata = workspace.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise ValueError(
                f"repository workspace must be a real directory, not a symlink: {workspace}"
            )
        if stat.S_ISDIR(metadata.st_mode):
            return candidate
    return base_dir


def _directory_flags() -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )


def _require_fd_matches_path(path: Path, descriptor: int, label: str) -> None:
    """Require one lexical path to still name the no-follow opened directory."""

    try:
        path_metadata = path.lstat()
    except FileNotFoundError as exc:
        raise ValueError(f"{label} disappeared during allocation: {path}") from exc
    descriptor_metadata = os.fstat(descriptor)
    if stat.S_ISLNK(path_metadata.st_mode) or not stat.S_ISDIR(path_metadata.st_mode):
        raise ValueError(f"{label} must be a real directory, not a symlink: {path}")
    if not stat.S_ISDIR(descriptor_metadata.st_mode) or (
        path_metadata.st_dev,
        path_metadata.st_ino,
    ) != (descriptor_metadata.st_dev, descriptor_metadata.st_ino):
        raise ValueError(f"{label} changed identity during allocation: {path}")


def _open_or_create_directory_at(
    parent_descriptor: int,
    name: str,
    path: Path,
    label: str,
    *,
    mode: int = 0o755,
) -> int:
    """Open one child using no-follow dirfd operations, creating it if absent."""

    try:
        os.mkdir(name, mode=mode, dir_fd=parent_descriptor)
        os.fsync(parent_descriptor)
    except FileExistsError:
        pass
    try:
        descriptor = os.open(name, _directory_flags(), dir_fd=parent_descriptor)
    except OSError as exc:
        raise ValueError(f"{label} must be a real directory, not a symlink: {path}") from exc
    try:
        _require_fd_matches_path(path, descriptor, label)
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def _write_reservation_at(
    result_descriptor: int,
    payload: dict[str, object],
) -> None:
    """Durably create reservation metadata inside an exclusively owned directory."""

    encoded = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    descriptor = os.open(
        RESERVATION_FILENAME,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
        dir_fd=result_descriptor,
    )
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)
    os.fsync(result_descriptor)


def _reservation_payload(result_dir: Path, attempt_id: str) -> dict[str, object]:
    return {
        "version": 1,
        "kind": "package-scribe-interactive-result",
        "resource_id": result_dir.name,
        "attempt_id": attempt_id,
        "owner": {
            "hostname": socket.gethostname(),
            "pid": os.getpid(),
        },
        "created_at": datetime.now(timezone.utc).isoformat(),
        "state": "reserved",
    }


def allocate_result_dir(base_dir: Path, *, attempt_id: str | None = None) -> Path:
    """Atomically reserve and return one unique package-resultNNN directory."""

    if os.name != "posix" or not {os.mkdir, os.open} <= os.supports_dir_fd:
        raise OSError(
            "durable package-result reservation requires POSIX no-follow dirfd operations"
        )
    if attempt_id is None:
        attempt_id = uuid.uuid4().hex
    if ATTEMPT_ID_PATTERN.fullmatch(attempt_id) is None:
        raise ValueError(
            "attempt id must start with an ASCII letter/digit and contain only "
            "ASCII letters, digits, dot, underscore, or hyphen"
        )

    repo_root = find_repo_root(base_dir)
    try:
        repo_descriptor = os.open(repo_root, _directory_flags())
    except OSError as exc:
        raise ValueError(f"repository root must be a real directory: {repo_root}") from exc
    try:
        _require_fd_matches_path(repo_root, repo_descriptor, "repository root")
        workspace = repo_root / "workspace"
        workspace_descriptor = _open_or_create_directory_at(
            repo_descriptor,
            "workspace",
            workspace,
            "repository workspace",
        )
        try:
            results_root = workspace / "package-scribe"
            results_descriptor = _open_or_create_directory_at(
                workspace_descriptor,
                "package-scribe",
                results_root,
                "package-scribe result root",
            )
            try:
                for index in range(MIN_RESULT_INDEX, MAX_RESULT_INDEX + 1):
                    result_name = f"package-result{index:03d}"
                    result_dir = results_root / result_name
                    try:
                        os.mkdir(result_name, mode=0o755, dir_fd=results_descriptor)
                        # The directory itself is the exclusive reservation and
                        # must be durable even if metadata creation later fails.
                        os.fsync(results_descriptor)
                    except FileExistsError:
                        # Another allocator won this exact ID. Any unknown object
                        # remains occupied; never follow or recycle it.
                        continue

                    result_descriptor = os.open(
                        result_name,
                        _directory_flags(),
                        dir_fd=results_descriptor,
                    )
                    try:
                        _require_fd_matches_path(
                            result_dir,
                            result_descriptor,
                            "allocated package result",
                        )
                        _write_reservation_at(
                            result_descriptor,
                            _reservation_payload(result_dir, attempt_id),
                        )
                        _require_fd_matches_path(
                            results_root,
                            results_descriptor,
                            "package-scribe result root",
                        )
                        _require_fd_matches_path(
                            result_dir,
                            result_descriptor,
                            "allocated package result",
                        )
                    finally:
                        os.close(result_descriptor)
                    return result_dir
            finally:
                os.close(results_descriptor)
        finally:
            os.close(workspace_descriptor)
    finally:
        os.close(repo_descriptor)

    raise RuntimeError(
        "no free package-scribe result identifier remains in the supported "
        "package-result001..package-result999 range"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Allocate the next workspace/package-scribe result directory."
    )
    parser.add_argument(
        "input_dir",
        nargs="?",
        default=".",
        help="Directory from which to locate the repository workspace.",
    )
    parser.add_argument(
        "--attempt-id",
        help="Optional caller-supplied attempt token recorded in reservation metadata.",
    )
    parser.add_argument(
        "--format",
        choices=("path", "json"),
        default="path",
        help="Output only the path (default) or a JSON object including the attempt token.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_dir = Path(args.input_dir).expanduser()

    try:
        base_dir = input_dir.resolve(strict=True)
        if not base_dir.is_dir():
            raise NotADirectoryError(input_dir)
        next_dir = allocate_result_dir(base_dir, attempt_id=args.attempt_id)
        reservation = json.loads(
            (next_dir / RESERVATION_FILENAME).read_text(encoding="utf-8")
        )
    except (OSError, RuntimeError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        print(f"error: cannot allocate result directory: {exc}", file=sys.stderr)
        return 1

    if args.format == "json":
        print(
            json.dumps(
                {
                    "path": str(next_dir),
                    "attempt_id": reservation["attempt_id"],
                },
                sort_keys=True,
            )
        )
    else:
        print(next_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
