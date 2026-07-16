#!/usr/bin/env python3
"""Failure-atomic publication of related files and directory trees.

The helper deliberately provides a conservative contract:

* every staged object and destination must be below one same-filesystem anchor;
* an advisory project lock serializes cooperative publishers on POSIX;
* compare-and-swap identities prevent overwriting state that changed after it
  was inspected;
* a durable journal and private backups make caught failures reversible and
  interrupted transactions recoverable when filesystem evidence is
  unambiguous; and
* rollback moves only objects whose identity still matches the candidate that
  this transaction published.  It never blindly deletes a destination.

``os.replace`` is atomic for one path, not for a collection of paths.  Readers
that require a coherent multi-path snapshot must hold ``PublicationLock``
while reading and hashing the owned artifacts.

The implementation requires POSIX ``flock`` and durable directory ``fsync``.
Unsupported platforms fail explicitly instead of silently weakening the
locking or durability contract.
"""

from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import uuid
from contextlib import AbstractContextManager
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Literal, Sequence


TRANSACTION_ROOT_NAME = ".hep-workflow-transactions"
GARBAGE_ROOT_NAME = ".garbage"
GARBAGE_OWNER_ROOT_NAME = ".owners"
ACTIVE_OWNER_ROOT_NAME = ".active-owners"
JOURNAL_NAME = "journal.json"
JOURNAL_VERSION = 3
GARBAGE_OWNER_VERSION = 1
ACTIVE_OWNER_VERSION = 1
_SCOPE_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
_TOKEN_PATTERN = re.compile(r"^[a-f0-9]{32}$")
_SHA256_PATTERN = re.compile(r"^sha256:[a-f0-9]{64}$")
_ACTIVE_OWNER_FILE_PATTERN = re.compile(
    r"^([a-f0-9]{32})--([0-9]{20})--([a-f0-9]{64})\.json$"
)


class PublicationTransactionError(RuntimeError):
    """Base class for publication-transaction failures."""


class UnsupportedTransactionPlatform(PublicationTransactionError):
    """The platform cannot provide the required locking/durability contract."""


class TransactionBusyError(PublicationTransactionError):
    """Another cooperative publisher currently owns the project lock."""


class ActiveTransactionError(PublicationTransactionError):
    """An incomplete transaction must be recovered before new publication."""


class CompareAndSwapError(PublicationTransactionError):
    """A staged candidate or destination changed after it was captured."""


class UnsafePublicationPath(PublicationTransactionError):
    """A publication path is outside the anchor or is not a regular tree."""


class TransactionRollbackError(PublicationTransactionError):
    """Rollback could not safely determine ownership of every destination."""

    def __init__(self, original_error: BaseException, issues: Sequence[str]) -> None:
        self.original_error = original_error
        self.issues = tuple(issues)
        super().__init__(
            f"publication failed ({original_error}); rollback requires recovery: "
            + "; ".join(self.issues)
        )


class TransactionCommittedCleanupError(PublicationTransactionError):
    """Publication committed durably, but private transaction cleanup failed."""

    def __init__(self, transaction_id: str, cleanup_error: BaseException) -> None:
        self.transaction_id = transaction_id
        self.cleanup_error = cleanup_error
        super().__init__(
            f"transaction {transaction_id} committed, but cleanup is pending: "
            f"{cleanup_error}"
        )


@dataclass(frozen=True)
class PathIdentity:
    """Compare-and-swap identity for an absent path, regular file, or tree."""

    kind: Literal["absent", "file", "directory"]
    sha256: str | None = None
    size: int | None = None
    mode: int | None = None
    device: int | None = None
    inode: int | None = None

    @property
    def exists(self) -> bool:
        return self.kind != "absent"


@dataclass(frozen=True)
class RecoveryResult:
    """Outcome of inspecting one private transaction directory."""

    transaction_id: str
    outcome: Literal["rolled_back", "finalized", "blocked"]
    issues: tuple[str, ...] = ()


@dataclass
class _Entry:
    staged: Path
    destination: Path
    backup: Path
    mode: Literal["replace", "create_only"]
    expected_before: PathIdentity
    candidate: PathIdentity


@dataclass(frozen=True)
class _CleanupRecord:
    transaction_id: str
    outcome: Literal["finalized", "rolled_back"]
    token: str
    garbage_name: str
    device: int
    inode: int
    owner_path: Path
    owner_device: int
    owner_inode: int
    garbage_entry: Path | None


@dataclass(frozen=True)
class _ActiveOwnerRecord:
    transaction_id: str
    scope: str
    token: str
    generation: int
    journal_sha256: str
    device: int
    inode: int
    owner_path: Path
    owner_device: int
    owner_inode: int


class _ProjectLock:
    """POSIX advisory lock held on the publication anchor directory inode."""

    def __init__(self, anchor: Path, *, blocking: bool = False) -> None:
        self._anchor = anchor.resolve(strict=True)
        self._blocking = blocking
        self._descriptor: int | None = None

    def acquire(self) -> None:
        if os.name != "posix":
            raise UnsupportedTransactionPlatform(
                "publication transactions require POSIX flock; no unsafe "
                "lock fallback is enabled"
            )
        try:
            import fcntl
        except ImportError as exc:  # pragma: no cover - non-POSIX defensive path
            raise UnsupportedTransactionPlatform(
                "publication transactions require the POSIX fcntl module"
            ) from exc

        flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = os.open(
            self._anchor,
            flags,
        )
        lock_metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(lock_metadata.st_mode):
            os.close(descriptor)
            raise UnsafePublicationPath(
                f"publication anchor lock is not a directory: {self._anchor}"
            )
        try:
            operation = fcntl.LOCK_EX
            if not self._blocking:
                operation |= fcntl.LOCK_NB
            fcntl.flock(descriptor, operation)
            current = self._anchor.stat(follow_symlinks=False)
            if (
                current.st_dev != lock_metadata.st_dev
                or current.st_ino != lock_metadata.st_ino
            ):
                raise UnsafePublicationPath(
                    f"publication anchor changed while acquiring its lock: {self._anchor}"
                )
        except OSError as exc:
            os.close(descriptor)
            if exc.errno in {errno.EACCES, errno.EAGAIN}:
                raise TransactionBusyError(
                    f"another publisher holds the anchor lock for {self._anchor}"
                ) from exc
            raise
        except BaseException:
            os.close(descriptor)
            raise
        self._descriptor = descriptor

    def release(self) -> None:
        if self._descriptor is None:
            return
        try:
            import fcntl

            fcntl.flock(self._descriptor, fcntl.LOCK_UN)
        finally:
            os.close(self._descriptor)
            self._descriptor = None

    @property
    def held(self) -> bool:
        return self._descriptor is not None


class PublicationLock(AbstractContextManager["PublicationLock"]):
    """Public project-scoped lock that can cover read/merge and publication.

    Callers that merge shared state should acquire this lock *before* reading
    that state, then pass the same handle to ``PublicationTransaction.begin``.
    This closes the lost-update window that destination CAS alone cannot close
    when two publishers derive candidates from the same earlier manifest.
    """

    def __init__(
        self,
        anchor: Path,
        scope: str,
        *,
        blocking: bool = False,
    ) -> None:
        if not _SCOPE_PATTERN.fullmatch(scope):
            raise ValueError(
                "lock scope must match ^[A-Za-z0-9_.-]{1,80}$"
            )
        self.anchor = anchor.resolve(strict=True)
        if not self.anchor.is_dir():
            raise NotADirectoryError(self.anchor)
        self.scope = scope
        self.blocking = blocking
        self.root = self.anchor / TRANSACTION_ROOT_NAME
        self._lock = _ProjectLock(self.anchor, blocking=blocking)

    @property
    def held(self) -> bool:
        return self._lock.held

    def acquire(self) -> "PublicationLock":
        if self.held:
            raise PublicationTransactionError("publication lock is already held")
        # Give interrupted/live multi-path state the more actionable diagnosis
        # before attempting the advisory lock. The post-lock check closes the
        # race with a transaction that starts between inspection and flock.
        if not self.blocking:
            assert_no_active_transactions(self.anchor)
        self._lock.acquire()
        try:
            assert_no_active_transactions(self.anchor)
        except BaseException:
            self._lock.release()
            raise
        return self

    def release(self) -> None:
        self._lock.release()

    def __enter__(self) -> "PublicationLock":
        return self.acquire()

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.release()


def publication_lock(
    anchor: Path,
    scope: str,
    *,
    blocking: bool = False,
) -> PublicationLock:
    """Return a context manager for one project-scoped publication lock."""

    return PublicationLock(anchor, scope, blocking=blocking)


def _safe_relative_path(value: str | Path, *, label: str) -> PurePosixPath:
    text = value.as_posix() if isinstance(value, Path) else value
    if not isinstance(text, str) or not text or "\x00" in text:
        raise UnsafePublicationPath(f"{label} must be a nonempty relative path")
    if "\\" in text:
        raise UnsafePublicationPath(f"{label} must not contain backslashes: {text!r}")
    raw_parts = text.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise UnsafePublicationPath(
            f"{label} must not contain empty/dot traversal segments: {text!r}"
        )
    relative = PurePosixPath(text)
    if relative.is_absolute():
        raise UnsafePublicationPath(
            f"{label} must not be absolute or contain empty/dot traversal segments: {text!r}"
        )
    return relative


def _resolve_below(path: Path, root: Path, *, label: str) -> Path:
    root_resolved = root.resolve(strict=True)
    candidate = path if path.is_absolute() else root_resolved / path
    if any(part in {"", ".", ".."} for part in candidate.parts[1:]):
        raise UnsafePublicationPath(
            f"{label} contains an unsafe path segment: {path}"
        )
    try:
        relative = candidate.relative_to(root_resolved)
    except ValueError:
        relative = None
        # The trusted anchor itself may be spelled through an OS alias such as
        # macOS /tmp -> /private/tmp or a case-insensitive path alias. Match
        # only that ancestor inode; components below it remain lexical and are
        # checked with lstat so internal symlinks never disappear via resolve().
        for prefix in (candidate, *candidate.parents):
            try:
                same_anchor = prefix.exists() and os.path.samefile(
                    prefix,
                    root_resolved,
                )
            except OSError:
                same_anchor = False
            if same_anchor:
                relative = candidate.relative_to(prefix)
                break
        if relative is None:
            raise UnsafePublicationPath(
                f"{label} escapes transaction anchor: {path}"
            )
    if not relative.parts:
        raise UnsafePublicationPath(f"{label} must be below, not equal to, its root: {path}")
    if any(part in {"", ".", ".."} for part in relative.parts):
        raise UnsafePublicationPath(
            f"{label} contains an unsafe path segment: {path}"
        )
    current = root_resolved
    for index, part in enumerate(relative.parts):
        current = current / part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(metadata.st_mode):
            raise UnsafePublicationPath(
                f"{label} contains a symlink component: {current}"
            )
        if index < len(relative.parts) - 1 and not stat.S_ISDIR(metadata.st_mode):
            raise UnsafePublicationPath(
                f"{label} has a non-directory parent component: {current}"
            )
    # Normalize only the trusted root spelling.  Returning the canonical-root
    # path keeps journal-relative paths stable when callers entered through an
    # OS alias, while the component walk above still rejects every symlink
    # below that root.
    return root_resolved.joinpath(*relative.parts)


def _nearest_existing(path: Path) -> Path:
    candidate = path
    while not candidate.exists():
        parent = candidate.parent
        if parent == candidate:
            raise FileNotFoundError(path)
        candidate = parent
    return candidate


def _require_same_filesystem(anchor: Path, paths: Iterable[Path]) -> None:
    expected_device = anchor.stat().st_dev
    for path in paths:
        existing = _nearest_existing(path)
        device = existing.stat().st_dev
        if device != expected_device:
            raise PublicationTransactionError(
                f"publication path crosses filesystems: {path} has device {device}, "
                f"anchor {anchor} has device {expected_device}"
            )


def _hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode):
        os.close(descriptor)
        raise UnsafePublicationPath(f"file changed to a non-regular object: {path}")
    with os.fdopen(descriptor, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return f"sha256:{digest.hexdigest()}", size


def _tree_digest(root: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    total_size = 0
    entries = sorted(root.rglob("*"), key=lambda path: os.fsencode(path.relative_to(root)))
    for path in entries:
        relative = os.fsencode(path.relative_to(root))
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise UnsafePublicationPath(f"symlink is not publishable: {path}")
        if stat.S_ISDIR(metadata.st_mode):
            digest.update(b"D\0" + relative + b"\0")
            digest.update(str(stat.S_IMODE(metadata.st_mode)).encode("ascii") + b"\0")
            continue
        if not stat.S_ISREG(metadata.st_mode):
            raise UnsafePublicationPath(f"non-regular tree entry is not publishable: {path}")
        file_digest, file_size = _hash_file(path)
        total_size += file_size
        digest.update(b"F\0" + relative + b"\0")
        digest.update(str(stat.S_IMODE(metadata.st_mode)).encode("ascii") + b"\0")
        digest.update(str(file_size).encode("ascii") + b"\0")
        digest.update(file_digest.encode("ascii") + b"\0")
    return f"sha256:{digest.hexdigest()}", total_size


def capture_identity(path: Path) -> PathIdentity:
    """Capture a strong identity without accepting symlinks or special files."""

    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return PathIdentity(kind="absent")
    if stat.S_ISLNK(metadata.st_mode):
        raise UnsafePublicationPath(f"symlink is not a publication object: {path}")
    if stat.S_ISREG(metadata.st_mode):
        digest, size = _hash_file(path)
        kind: Literal["file", "directory"] = "file"
    elif stat.S_ISDIR(metadata.st_mode):
        digest, size = _tree_digest(path)
        kind = "directory"
    else:
        raise UnsafePublicationPath(f"non-regular publication object: {path}")
    return PathIdentity(
        kind=kind,
        sha256=digest,
        size=size,
        mode=stat.S_IMODE(metadata.st_mode),
        device=metadata.st_dev,
        inode=metadata.st_ino,
    )


def _identity_from_json(payload: object) -> PathIdentity:
    if not isinstance(payload, dict):
        raise ValueError("identity must be an object")
    identity = PathIdentity(
        kind=payload.get("kind"),  # type: ignore[arg-type]
        sha256=payload.get("sha256"),  # type: ignore[arg-type]
        size=payload.get("size"),  # type: ignore[arg-type]
        mode=payload.get("mode"),  # type: ignore[arg-type]
        device=payload.get("device"),  # type: ignore[arg-type]
        inode=payload.get("inode"),  # type: ignore[arg-type]
    )
    if identity.kind == "absent":
        if any(
            value is not None
            for value in (
                identity.sha256,
                identity.size,
                identity.mode,
                identity.device,
                identity.inode,
            )
        ):
            raise ValueError("absent identity must not contain filesystem metadata")
        return identity
    if identity.kind not in {"file", "directory"}:
        raise ValueError(f"unknown identity kind: {identity.kind!r}")
    if not isinstance(identity.sha256, str) or not identity.sha256.startswith("sha256:"):
        raise ValueError("present identity requires a sha256 digest")
    if any(
        not isinstance(value, int) or isinstance(value, bool) or value < 0
        for value in (
            identity.size,
            identity.mode,
            identity.device,
            identity.inode,
        )
    ):
        raise ValueError("present identity contains invalid filesystem metadata")
    return identity


def _identities_match(left: PathIdentity, right: PathIdentity) -> bool:
    return left == right


def _fsync_file(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise UnsafePublicationPath(f"cannot fsync non-regular file: {path}")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_directory(path: Path) -> None:
    if os.name != "posix":
        raise UnsupportedTransactionPlatform(
            "durable directory fsync is required and only supported on POSIX"
        )
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise UnsupportedTransactionPlatform(
            f"cannot open directory for durable fsync: {path}: {exc}"
        ) from exc
    try:
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise UnsafePublicationPath(f"cannot fsync non-directory: {path}")
        try:
            os.fsync(descriptor)
        except OSError as exc:
            if exc.errno in {errno.EBADF, errno.EINVAL, errno.ENOTSUP}:
                raise UnsupportedTransactionPlatform(
                    f"filesystem does not support durable directory fsync for {path}"
                ) from exc
            raise
    finally:
        os.close(descriptor)


def _fsync_tree(path: Path) -> None:
    identity = capture_identity(path)
    if identity.kind == "file":
        _fsync_file(path)
        _fsync_directory(path.parent)
        return
    if identity.kind != "directory":
        raise UnsafePublicationPath(f"cannot fsync absent publication candidate: {path}")
    directories = [path, *(entry for entry in path.rglob("*") if entry.is_dir())]
    files = [entry for entry in path.rglob("*") if entry.is_file()]
    for file_path in files:
        _fsync_file(file_path)
    for directory in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        _fsync_directory(directory)


def _rename_no_replace(source: Path, destination: Path) -> None:
    """Atomically rename while refusing to overwrite a racing destination."""

    if os.name != "posix":
        raise UnsupportedTransactionPlatform(
            "atomic no-replace rename requires a supported POSIX platform"
        )
    libc = ctypes.CDLL(None, use_errno=True)
    source_bytes = os.fsencode(source)
    destination_bytes = os.fsencode(destination)
    if sys.platform == "darwin":
        try:
            rename = libc.renamex_np
        except AttributeError as exc:
            raise UnsupportedTransactionPlatform(
                "macOS renamex_np(RENAME_EXCL) is required"
            ) from exc
        rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        result = rename(source_bytes, destination_bytes, 0x00000004)
    elif sys.platform.startswith("linux"):
        try:
            rename = libc.renameat2
        except AttributeError as exc:
            raise UnsupportedTransactionPlatform(
                "Linux renameat2(RENAME_NOREPLACE) is required"
            ) from exc
        rename.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        rename.restype = ctypes.c_int
        result = rename(-100, source_bytes, -100, destination_bytes, 0x00000001)
    else:
        raise UnsupportedTransactionPlatform(
            f"atomic no-replace rename is not implemented for {sys.platform!r}"
        )
    if result == 0:
        return
    error_number = ctypes.get_errno()
    if error_number in {errno.EEXIST, errno.ENOTEMPTY}:
        raise FileExistsError(
            error_number,
            "atomic publication destination already exists",
            destination,
        )
    if error_number in {
        errno.ENOSYS,
        errno.EINVAL,
        getattr(errno, "EOPNOTSUPP", errno.ENOTSUP),
        errno.ENOTSUP,
    }:
        raise UnsupportedTransactionPlatform(
            f"filesystem lacks atomic no-replace rename for {destination}"
        )
    raise OSError(error_number, os.strerror(error_number), destination)


def atomic_rename_no_replace(source: Path, destination: Path) -> None:
    """Atomically move ``source`` only when ``destination`` is still absent."""

    _rename_no_replace(source, destination)


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return (
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False).encode("utf-8")
        + b"\n"
    )


def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    data = _json_bytes(payload)
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)
    os.replace(temporary, path)
    _fsync_directory(path.parent)


def _read_regular_bytes(path: Path, *, label: str) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise UnsafePublicationPath(f"{label} must be a regular file: {path}")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            return handle.read()
    finally:
        os.close(descriptor)


def _strict_json_decode(data: bytes, *, label: str) -> dict[str, Any]:
    def reject_constant(value: str) -> None:
        raise ValueError(f"non-standard JSON constant {value!r}")

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key {key!r}")
            result[key] = value
        return result

    payload = json.loads(
        data,
        parse_constant=reject_constant,
        object_pairs_hook=unique_object,
    )
    if not isinstance(payload, dict):
        raise ValueError(f"{label} root must be an object")
    return payload


def _strict_json_load_with_bytes(
    path: Path,
    *,
    label: str = "journal",
) -> tuple[dict[str, Any], bytes]:
    data = _read_regular_bytes(path, label=label)
    return _strict_json_decode(data, label=label), data


def _strict_json_load(path: Path, *, label: str = "journal") -> dict[str, Any]:
    payload, _ = _strict_json_load_with_bytes(path, label=label)
    return payload


def _transaction_entries(anchor: Path) -> list[Path]:
    root = anchor / TRANSACTION_ROOT_NAME
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        raise UnsafePublicationPath(
            f"transaction root must be a real directory: {root}"
        )
    if not root.exists():
        return []
    entries: list[Path] = []
    for path in sorted(root.iterdir()):
        if path.name in {GARBAGE_ROOT_NAME, ACTIVE_OWNER_ROOT_NAME}:
            if path.is_symlink() or not path.is_dir():
                raise UnsafePublicationPath(
                    f"transaction private metadata root must be a real directory: {path}"
                )
            continue
        entries.append(path)
    return entries


def _garbage_root(anchor: Path) -> Path:
    root = anchor / TRANSACTION_ROOT_NAME
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        raise UnsafePublicationPath(
            f"transaction root must be a real directory: {root}"
        )
    garbage_root = root / GARBAGE_ROOT_NAME
    if garbage_root.is_symlink() or (
        garbage_root.exists() and not garbage_root.is_dir()
    ):
        raise UnsafePublicationPath(
            f"transaction garbage root must be a real directory: {garbage_root}"
        )
    return garbage_root


def _garbage_identity(path: Path) -> tuple[str, Literal["finalized", "rolled_back"]]:
    prefix, separator, transaction_id = path.name.partition("--")
    scope, token_separator, transaction_token = transaction_id.rpartition("-")
    if (
        separator != "--"
        or prefix not in {"finalized", "rolled_back"}
        or token_separator != "-"
        or not _SCOPE_PATTERN.fullmatch(scope)
        or not _TOKEN_PATTERN.fullmatch(transaction_token)
    ):
        raise ValueError(f"invalid transaction garbage entry name: {path.name!r}")
    return transaction_id, prefix  # type: ignore[return-value]


def _cleanup_owner_payload(
    *,
    transaction_id: str,
    outcome: Literal["finalized", "rolled_back"],
    token: str,
    garbage_name: str,
    device: int,
    inode: int,
) -> dict[str, Any]:
    return {
        "version": GARBAGE_OWNER_VERSION,
        "transaction_id": transaction_id,
        "outcome": outcome,
        "token": token,
        "garbage_name": garbage_name,
        "device": device,
        "inode": inode,
    }


def _write_cleanup_owner(owner_path: Path, payload: dict[str, Any]) -> None:
    """Durably install an external ownership record without overwriting one."""

    temporary = owner_path.with_name(f".{owner_path.name}.{uuid.uuid4().hex}.tmp")
    data = json.dumps(payload, indent=2, sort_keys=True, allow_nan=False).encode("utf-8") + b"\n"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)
    try:
        _rename_no_replace(temporary, owner_path)
        _fsync_directory(owner_path.parent)
    except BaseException:
        try:
            temporary.unlink()
            _fsync_directory(temporary.parent)
        except BaseException:
            pass
        raise


def _active_owner_root(root: Path, *, create: bool = False) -> Path:
    root_metadata = root.lstat()
    if stat.S_ISLNK(root_metadata.st_mode) or not stat.S_ISDIR(root_metadata.st_mode):
        raise UnsafePublicationPath(
            f"transaction root must be a real directory: {root}"
        )
    owner_root = root / ACTIVE_OWNER_ROOT_NAME
    if owner_root.is_symlink() or (owner_root.exists() and not owner_root.is_dir()):
        raise UnsafePublicationPath(
            f"active ownership root must be a real directory: {owner_root}"
        )
    if create and not owner_root.exists():
        owner_root.mkdir(mode=0o700)
        _fsync_directory(root)
    return owner_root


def _active_owner_payload(
    *,
    transaction_id: str,
    scope: str,
    token: str,
    generation: int,
    journal_sha256: str,
    device: int,
    inode: int,
) -> dict[str, Any]:
    return {
        "version": ACTIVE_OWNER_VERSION,
        "transaction_id": transaction_id,
        "transaction_name": transaction_id,
        "scope": scope,
        "token": token,
        "generation": generation,
        "journal_sha256": journal_sha256,
        "device": device,
        "inode": inode,
    }


def _active_owner_name(token: str, generation: int, journal_sha256: str) -> str:
    if not _TOKEN_PATTERN.fullmatch(token):
        raise ValueError("active ownership token is invalid")
    if (
        not isinstance(generation, int)
        or isinstance(generation, bool)
        or generation < 0
        or generation >= 10**20
    ):
        raise ValueError("active journal generation is invalid")
    if not _SHA256_PATTERN.fullmatch(journal_sha256):
        raise ValueError("active journal digest is invalid")
    return (
        f"{token}--{generation:020d}--"
        f"{journal_sha256.removeprefix('sha256:')}.json"
    )


def _write_active_attestation(
    root: Path,
    transaction_dir: Path,
    *,
    transaction_id: str,
    scope: str,
    token: str,
    generation: int,
    journal_bytes: bytes,
) -> None:
    metadata = transaction_dir.stat(follow_symlinks=False)
    if not stat.S_ISDIR(metadata.st_mode):
        raise UnsafePublicationPath(
            f"transaction attestation source must be a real directory: {transaction_dir}"
        )
    journal_sha256 = f"sha256:{hashlib.sha256(journal_bytes).hexdigest()}"
    owner_root = _active_owner_root(root, create=True)
    owner_path = owner_root / _active_owner_name(
        token,
        generation,
        journal_sha256,
    )
    _write_cleanup_owner(
        owner_path,
        _active_owner_payload(
            transaction_id=transaction_id,
            scope=scope,
            token=token,
            generation=generation,
            journal_sha256=journal_sha256,
            device=metadata.st_dev,
            inode=metadata.st_ino,
        ),
    )


def _load_active_owner(owner_path: Path) -> _ActiveOwnerRecord:
    owner_metadata = owner_path.lstat()
    if stat.S_ISLNK(owner_metadata.st_mode) or not stat.S_ISREG(owner_metadata.st_mode):
        raise UnsafePublicationPath(
            f"active ownership record must be a regular file: {owner_path}"
        )
    match = _ACTIVE_OWNER_FILE_PATTERN.fullmatch(owner_path.name)
    if match is None:
        raise ValueError(f"invalid active ownership record name: {owner_path.name!r}")
    filename_token, filename_generation, filename_digest = match.groups()
    payload = _strict_json_load(owner_path, label="active ownership record")
    current_owner_metadata = owner_path.lstat()
    if (
        current_owner_metadata.st_dev != owner_metadata.st_dev
        or current_owner_metadata.st_ino != owner_metadata.st_ino
        or not stat.S_ISREG(current_owner_metadata.st_mode)
    ):
        raise UnsafePublicationPath(
            f"active ownership record changed while it was read: {owner_path}"
        )
    expected = {
        "version",
        "transaction_id",
        "transaction_name",
        "scope",
        "token",
        "generation",
        "journal_sha256",
        "device",
        "inode",
    }
    if set(payload) != expected or payload.get("version") != ACTIVE_OWNER_VERSION:
        raise ValueError("active ownership record fields/version are invalid")
    transaction_id = payload.get("transaction_id")
    transaction_name = payload.get("transaction_name")
    scope = payload.get("scope")
    token = payload.get("token")
    generation = payload.get("generation")
    journal_sha256 = payload.get("journal_sha256")
    device = payload.get("device")
    inode = payload.get("inode")
    if (
        not isinstance(transaction_id, str)
        or transaction_name != transaction_id
        or not isinstance(scope, str)
        or not _SCOPE_PATTERN.fullmatch(scope)
        or not transaction_id.startswith(f"{scope}-")
    ):
        raise ValueError("active ownership transaction identity is invalid")
    if token != filename_token or not isinstance(token, str):
        raise ValueError("active ownership token disagrees with its filename")
    if (
        not isinstance(generation, int)
        or isinstance(generation, bool)
        or generation < 0
        or generation >= 10**20
        or generation != int(filename_generation)
    ):
        raise ValueError("active ownership generation disagrees with its filename")
    expected_digest = f"sha256:{filename_digest}"
    if journal_sha256 != expected_digest:
        raise ValueError("active ownership journal digest disagrees with its filename")
    if any(
        not isinstance(value, int) or isinstance(value, bool) or value < 0
        for value in (device, inode)
    ):
        raise ValueError("active ownership record contains invalid inode metadata")
    return _ActiveOwnerRecord(
        transaction_id=transaction_id,
        scope=scope,
        token=token,
        generation=generation,
        journal_sha256=expected_digest,
        device=device,
        inode=inode,
        owner_path=owner_path,
        owner_device=owner_metadata.st_dev,
        owner_inode=owner_metadata.st_ino,
    )


def _active_owner_records(root: Path) -> list[_ActiveOwnerRecord]:
    owner_root = _active_owner_root(root)
    if not owner_root.exists():
        return []
    return [_load_active_owner(path) for path in sorted(owner_root.iterdir())]


def _authenticate_active_journal(
    root: Path,
    transaction_dir: Path,
    payload: dict[str, Any],
    journal_bytes: bytes,
) -> _ActiveOwnerRecord:
    transaction_metadata = transaction_dir.stat(follow_symlinks=False)
    if not stat.S_ISDIR(transaction_metadata.st_mode):
        raise UnsafePublicationPath(
            f"transaction recovery source must be a real directory: {transaction_dir}"
        )
    transaction_id = payload.get("transaction_id")
    scope = payload.get("scope")
    token = payload.get("cleanup_token")
    generation = payload.get("generation")
    journal_sha256 = f"sha256:{hashlib.sha256(journal_bytes).hexdigest()}"
    matches = [
        record
        for record in _active_owner_records(root)
        if record.transaction_id == transaction_id
        and record.scope == scope
        and record.token == token
        and record.generation == generation
        and record.journal_sha256 == journal_sha256
        and record.device == transaction_metadata.st_dev
        and record.inode == transaction_metadata.st_ino
    ]
    if len(matches) != 1:
        raise UnsafePublicationPath(
            "journal lacks exactly one matching external active ownership "
            "attestation (token/generation/hash/inode)"
        )
    return matches[0]


def _purge_active_owner_records(
    root: Path,
    *,
    transaction_id: str,
    token: str,
    device: int,
    inode: int,
) -> None:
    owner_root = _active_owner_root(root)
    if not owner_root.exists():
        return
    records = _active_owner_records(root)
    selected = [
        record
        for record in records
        if record.transaction_id == transaction_id or record.token == token
    ]
    for record in selected:
        if (
            record.transaction_id != transaction_id
            or record.token != token
            or record.device != device
            or record.inode != inode
        ):
            raise UnsafePublicationPath(
                "active ownership record conflicts with transaction cleanup identity"
            )
    for record in selected:
        metadata = record.owner_path.lstat()
        if (
            metadata.st_dev != record.owner_device
            or metadata.st_ino != record.owner_inode
            or not stat.S_ISREG(metadata.st_mode)
        ):
            raise UnsafePublicationPath(
                f"active ownership record changed before unlink: {record.owner_path}"
            )
        record.owner_path.unlink()
    if selected:
        _fsync_directory(owner_root)
    if owner_root.exists() and not any(owner_root.iterdir()):
        owner_root.rmdir()
        _fsync_directory(root)


def _load_cleanup_owner(owner_path: Path, garbage_root: Path) -> _CleanupRecord:
    owner_metadata = owner_path.lstat()
    if stat.S_ISLNK(owner_metadata.st_mode) or not stat.S_ISREG(owner_metadata.st_mode):
        raise UnsafePublicationPath(
            f"cleanup ownership record must be a regular file: {owner_path}"
        )
    token = owner_path.name.removesuffix(".json")
    if owner_path.name != f"{token}.json" or not _TOKEN_PATTERN.fullmatch(token):
        raise ValueError(f"invalid cleanup ownership record name: {owner_path.name!r}")
    payload = _strict_json_load(owner_path, label="cleanup ownership record")
    expected = {
        "version",
        "transaction_id",
        "outcome",
        "token",
        "garbage_name",
        "device",
        "inode",
    }
    if set(payload) != expected or payload.get("version") != GARBAGE_OWNER_VERSION:
        raise ValueError("cleanup ownership record fields/version are invalid")
    if payload.get("token") != token:
        raise ValueError("cleanup ownership token does not match its filename")
    transaction_id = payload.get("transaction_id")
    outcome = payload.get("outcome")
    garbage_name = payload.get("garbage_name")
    device = payload.get("device")
    inode = payload.get("inode")
    if not isinstance(garbage_name, str):
        raise ValueError("cleanup ownership record garbage_name must be a string")
    transaction_from_name, outcome_from_name = _garbage_identity(
        garbage_root / garbage_name
    )
    if transaction_id != transaction_from_name or outcome != outcome_from_name:
        raise ValueError("cleanup ownership record disagrees with its garbage name")
    if any(
        not isinstance(value, int) or isinstance(value, bool) or value < 0
        for value in (device, inode)
    ):
        raise ValueError("cleanup ownership record contains invalid inode metadata")
    garbage_entry = garbage_root / garbage_name
    if garbage_entry.exists() or garbage_entry.is_symlink():
        if garbage_entry.is_symlink() or not garbage_entry.is_dir():
            raise UnsafePublicationPath(
                f"authenticated garbage entry must be a real directory: {garbage_entry}"
            )
        metadata = garbage_entry.stat(follow_symlinks=False)
        if metadata.st_dev != device or metadata.st_ino != inode:
            raise UnsafePublicationPath(
                f"garbage entry inode no longer matches ownership record: {garbage_entry}"
            )
        entry: Path | None = garbage_entry
    else:
        entry = None
    return _CleanupRecord(
        transaction_id=str(transaction_id),
        outcome=outcome,  # type: ignore[arg-type]
        token=token,
        garbage_name=garbage_name,
        device=device,
        inode=inode,
        owner_path=owner_path,
        owner_device=owner_metadata.st_dev,
        owner_inode=owner_metadata.st_ino,
        garbage_entry=entry,
    )


def _cleanup_records(anchor: Path) -> list[_CleanupRecord]:
    garbage_root = _garbage_root(anchor)
    if not garbage_root.exists():
        return []
    owner_root = garbage_root / GARBAGE_OWNER_ROOT_NAME
    if owner_root.is_symlink() or (owner_root.exists() and not owner_root.is_dir()):
        raise UnsafePublicationPath(
            f"cleanup ownership root must be a real directory: {owner_root}"
        )
    entries = {
        path.name: path
        for path in garbage_root.iterdir()
        if path.name != GARBAGE_OWNER_ROOT_NAME
    }
    if not owner_root.exists():
        if entries:
            raise UnsafePublicationPath(
                "garbage entries lack external cleanup ownership records: "
                + ", ".join(sorted(entries))
            )
        return []
    records = [_load_cleanup_owner(path, garbage_root) for path in sorted(owner_root.iterdir())]
    names = [record.garbage_name for record in records]
    if len(names) != len(set(names)):
        raise UnsafePublicationPath("duplicate cleanup ownership records name one garbage entry")
    unauthenticated = sorted(set(entries) - set(names))
    if unauthenticated:
        raise UnsafePublicationPath(
            "garbage entries lack matching cleanup ownership records: "
            + ", ".join(unauthenticated)
        )
    return records


def active_transactions(anchor: Path) -> tuple[str, ...]:
    """Return every transaction directory that still requires resolution."""

    anchor = anchor.resolve(strict=True)
    return tuple(path.name for path in _transaction_entries(anchor))


def pending_transaction_cleanups(
    anchor: Path,
) -> tuple[tuple[str, Literal["finalized", "rolled_back"]], ...]:
    """Return safely quarantined private trees whose deletion is incomplete."""

    anchor = anchor.resolve(strict=True)
    return tuple(
        (record.transaction_id, record.outcome)
        for record in _cleanup_records(anchor)
    )


def assert_no_active_transactions(
    anchor: Path,
    *,
    exclude: Iterable[str] = (),
) -> None:
    """Fail closed while a multi-path publication may be incomplete."""

    excluded = set(exclude)
    active = tuple(item for item in active_transactions(anchor) if item not in excluded)
    if active:
        raise ActiveTransactionError(
            "incomplete publication transaction(s) require recovery: " + ", ".join(active)
        )


def _entry_payload(entry: _Entry, anchor: Path, transaction_dir: Path) -> dict[str, Any]:
    return {
        "staged": entry.staged.relative_to(transaction_dir).as_posix(),
        "destination": entry.destination.relative_to(anchor).as_posix(),
        "backup": entry.backup.relative_to(transaction_dir).as_posix(),
        "mode": entry.mode,
        "expected_before": asdict(entry.expected_before),
        "candidate": asdict(entry.candidate),
    }


def _paths_overlap(left: Path, right: Path) -> bool:
    return left == right or left in right.parents or right in left.parents


class PublicationTransaction:
    """Stage and failure-atomically publish related filesystem objects."""

    def __init__(
        self,
        anchor: Path,
        scope: str,
        *,
        lock: PublicationLock | None = None,
    ) -> None:
        if not _SCOPE_PATTERN.fullmatch(scope):
            raise ValueError(
                "transaction scope must match ^[A-Za-z0-9_.-]{1,80}$"
            )
        self.anchor = anchor.resolve(strict=True)
        if not self.anchor.is_dir():
            raise NotADirectoryError(self.anchor)
        self.scope = scope
        self.root = self.anchor / TRANSACTION_ROOT_NAME
        if lock is None:
            self._publication_lock = PublicationLock(self.anchor, scope)
            self._publication_lock.acquire()
            self._owns_lock = True
        else:
            if not lock.held:
                raise PublicationTransactionError(
                    "the supplied publication lock is not held"
                )
            if lock.anchor != self.anchor:
                raise PublicationTransactionError(
                    "the supplied publication lock belongs to a different anchor"
                )
            self._publication_lock = lock
            self._owns_lock = False
        try:
            assert_no_active_transactions(self.anchor)
            if self.root.is_symlink() or (
                self.root.exists() and not self.root.is_dir()
            ):
                raise UnsafePublicationPath(
                    f"transaction root must be a real directory: {self.root}"
                )
            self.root.mkdir(exist_ok=True)
            _fsync_directory(self.anchor)
            self.transaction_id = f"{scope}-{uuid.uuid4().hex}"
            self.cleanup_token = uuid.uuid4().hex
            self.transaction_dir = self.root / self.transaction_id
            self.staging_dir = self.transaction_dir / "staging"
            self.backup_dir = self.transaction_dir / "backups"
            self.transaction_dir.mkdir(exist_ok=False)
            self.staging_dir.mkdir()
            self.backup_dir.mkdir()
            _fsync_directory(self.transaction_dir)
            _fsync_directory(self.root)
            self._entries: list[_Entry] = []
            self._status = "staging"
            self._journal_generation = -1
            self._closed = False
            self._write_journal()
        except BaseException as construction_error:
            transaction_dir = getattr(self, "transaction_dir", None)
            cleanup_error: BaseException | None = None
            if isinstance(transaction_dir, Path) and transaction_dir.exists():
                # Construction has not exposed a transaction object and has
                # not published any entry. Atomically retire even a partial
                # no-journal directory before interruptible cleanup.
                try:
                    _remove_transaction_dir(
                        self.root,
                        transaction_dir,
                        outcome="rolled_back",
                        cleanup_token=self.cleanup_token,
                    )
                except BaseException as exc:
                    cleanup_error = exc
            if self.root.exists() and self.root.is_dir() and not any(self.root.iterdir()):
                try:
                    self.root.rmdir()
                    _fsync_directory(self.anchor)
                except Exception:
                    pass
            self._release_owned_lock()
            if cleanup_error is not None:
                raise TransactionRollbackError(
                    construction_error,
                    (f"private transaction construction cleanup failed: {cleanup_error}",),
                ) from cleanup_error
            raise

    @classmethod
    def begin(
        cls,
        anchor: Path,
        scope: str,
        *,
        lock: PublicationLock | None = None,
    ) -> "PublicationTransaction":
        return cls(anchor, scope, lock=lock)

    def _release_owned_lock(self) -> None:
        if self._owns_lock:
            self._publication_lock.release()

    @property
    def journal_path(self) -> Path:
        return self.transaction_dir / JOURNAL_NAME

    def _journal_payload(self, *, generation: int | None = None) -> dict[str, Any]:
        if generation is None:
            generation = self._journal_generation
        return {
            "version": JOURNAL_VERSION,
            "transaction_id": self.transaction_id,
            "scope": self.scope,
            "status": self._status,
            "cleanup_token": self.cleanup_token,
            "generation": generation,
            "entries": [
                _entry_payload(entry, self.anchor, self.transaction_dir)
                for entry in self._entries
            ],
        }

    def _write_journal(self) -> None:
        generation = self._journal_generation + 1
        payload = self._journal_payload(generation=generation)
        journal_bytes = _json_bytes(payload)
        # The external immutable attestation is durable before the journal
        # generation becomes visible. A crash between the two leaves the old
        # journal authenticated; an unauthenticated new journal is never acted
        # upon by recovery.
        _write_active_attestation(
            self.root,
            self.transaction_dir,
            transaction_id=self.transaction_id,
            scope=self.scope,
            token=self.cleanup_token,
            generation=generation,
            journal_bytes=journal_bytes,
        )
        _atomic_json_write(self.journal_path, payload)
        self._journal_generation = generation

    def stage_path(self, relative_path: str | Path) -> Path:
        """Return a contained staging path, creating only private parents."""

        if self._closed or self._status != "staging":
            raise PublicationTransactionError("transaction is no longer accepting staged paths")
        relative = _safe_relative_path(relative_path, label="staging path")
        path = self.staging_dir.joinpath(*relative.parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        _fsync_directory(path.parent)
        return path

    def add(
        self,
        staged: Path,
        destination: Path,
        *,
        mode: Literal["replace", "create_only"],
        expected_before: PathIdentity,
    ) -> None:
        """Register one candidate with an explicit expected destination identity."""

        if self._closed or self._status != "staging":
            raise PublicationTransactionError("transaction is no longer accepting entries")
        if mode not in {"replace", "create_only"}:
            raise ValueError(f"unknown publication mode: {mode!r}")
        staged = _resolve_below(staged, self.staging_dir, label="staged candidate")
        destination = _resolve_below(destination, self.anchor, label="destination")
        if not staged.exists():
            raise FileNotFoundError(staged)
        if not destination.parent.is_dir():
            raise FileNotFoundError(
                f"destination parent must exist before publication: {destination.parent}"
            )
        candidate = capture_identity(staged)
        if mode == "create_only" and expected_before.kind != "absent":
            raise ValueError("create_only requires an explicitly captured absent destination")
        if expected_before.exists and expected_before.kind != candidate.kind:
            raise UnsafePublicationPath(
                "replacement cannot change a destination between file and directory: "
                f"{destination} is {expected_before.kind}, candidate is {candidate.kind}"
            )
        for existing in self._entries:
            if _paths_overlap(existing.destination, destination):
                raise ValueError(
                    f"overlapping publication destinations are not supported: "
                    f"{existing.destination} and {destination}"
                )
            if _paths_overlap(existing.staged, staged):
                raise ValueError(
                    f"overlapping staged candidates are not supported: "
                    f"{existing.staged} and {staged}"
                )
        _require_same_filesystem(self.anchor, (staged, destination.parent))
        backup = self.backup_dir / f"{len(self._entries):06d}"
        self._entries.append(
            _Entry(
                staged=staged,
                destination=destination,
                backup=backup,
                mode=mode,
                expected_before=expected_before,
                candidate=candidate,
            )
        )
        self._write_journal()

    def _prepare_candidates(self) -> None:
        if not self._entries:
            raise PublicationTransactionError("cannot commit an empty transaction")
        for entry in self._entries:
            current = capture_identity(entry.staged)
            if not _identities_match(current, entry.candidate):
                raise CompareAndSwapError(
                    f"staged candidate changed before publication: {entry.staged}"
                )
            _fsync_tree(entry.staged)

    def _publish_entry(self, entry: _Entry) -> None:
        _resolve_below(entry.staged, self.staging_dir, label="staged candidate")
        _resolve_below(entry.destination, self.anchor, label="destination")
        _resolve_below(entry.backup, self.transaction_dir, label="backup")
        current = capture_identity(entry.destination)
        if not _identities_match(current, entry.expected_before):
            raise CompareAndSwapError(
                f"destination changed after capture: {entry.destination}; "
                f"expected {entry.expected_before}, found {current}"
            )
        if entry.mode == "create_only" and current.kind != "absent":
            raise CompareAndSwapError(
                f"create-only destination now exists: {entry.destination}"
            )
        if capture_identity(entry.backup).kind != "absent":
            raise CompareAndSwapError(
                f"private backup path is unexpectedly occupied: {entry.backup}"
            )
        if current.exists:
            _rename_no_replace(entry.destination, entry.backup)
            _fsync_directory(entry.backup.parent)
            _fsync_directory(entry.destination.parent)
        _rename_no_replace(entry.staged, entry.destination)
        _fsync_directory(entry.destination.parent)
        _fsync_directory(entry.staged.parent)
        published = capture_identity(entry.destination)
        if not _identities_match(published, entry.candidate):
            raise CompareAndSwapError(
                f"published destination does not match candidate: {entry.destination}"
            )
        self._write_journal()

    def commit(
        self,
        *,
        validate_candidate: Callable[[], None] | None = None,
        pre_publish_check: Callable[[], None] | None = None,
        post_publish_check: Callable[[], None] | None = None,
        after_publish_entry: Callable[[Path, int], None] | None = None,
    ) -> None:
        """Publish every entry or restore the captured prior snapshot."""

        if self._closed or self._status != "staging":
            raise PublicationTransactionError(
                "transaction cannot be committed in its current state"
            )
        try:
            if validate_candidate is not None:
                validate_candidate()
            self._prepare_candidates()
            if pre_publish_check is not None:
                pre_publish_check()
            self._status = "publishing"
            self._write_journal()
            for index, entry in enumerate(self._entries, start=1):
                self._publish_entry(entry)
                if after_publish_entry is not None:
                    after_publish_entry(entry.destination, index)
            if post_publish_check is not None:
                post_publish_check()
            committed_issues = _committed_state_issues(self.anchor, self._entries)
            if committed_issues:
                raise CompareAndSwapError(
                    "published destination drifted before commit: "
                    + "; ".join(committed_issues)
                )
            self._status = "committed"
            self._write_journal()
        except BaseException as exc:
            issues = _rollback_entries(self.anchor, self.transaction_dir, self._entries)
            if issues:
                self._status = "recovery_required"
                try:
                    self._write_journal()
                finally:
                    self._closed = True
                    self._release_owned_lock()
                raise TransactionRollbackError(exc, issues) from exc
            try:
                _remove_transaction_dir(
                    self.root,
                    self.transaction_dir,
                    outcome="rolled_back",
                    cleanup_token=self.cleanup_token,
                )
            except BaseException as cleanup_error:
                self._closed = True
                self._release_owned_lock()
                raise TransactionRollbackError(
                    exc,
                    (f"private transaction cleanup failed: {cleanup_error}",),
                ) from cleanup_error
            self._closed = True
            self._release_owned_lock()
            raise

        try:
            _remove_transaction_dir(
                self.root,
                self.transaction_dir,
                outcome="finalized",
                cleanup_token=self.cleanup_token,
            )
        except BaseException as cleanup_error:
            self._closed = True
            self._release_owned_lock()
            raise TransactionCommittedCleanupError(
                self.transaction_id, cleanup_error
            ) from cleanup_error
        self._closed = True
        self._release_owned_lock()

    def abort(self) -> None:
        """Discard private staged candidates before publication starts."""

        if self._closed:
            return
        if self._status != "staging":
            raise PublicationTransactionError("only a staging transaction can be aborted")
        try:
            _remove_transaction_dir(
                self.root,
                self.transaction_dir,
                outcome="rolled_back",
                cleanup_token=self.cleanup_token,
            )
        finally:
            self._closed = True
            self._release_owned_lock()

    def __enter__(self) -> "PublicationTransaction":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if not self._closed and self._status == "staging":
            self.abort()


def _cleanup_empty_private_roots(root: Path, garbage_root: Path) -> None:
    owner_root = garbage_root / GARBAGE_OWNER_ROOT_NAME
    if owner_root.exists() and not any(owner_root.iterdir()):
        owner_root.rmdir()
        _fsync_directory(garbage_root)
    if garbage_root.exists() and not any(garbage_root.iterdir()):
        garbage_root.rmdir()
        _fsync_directory(root)
    if root.exists() and not any(root.iterdir()):
        anchor = root.parent
        root.rmdir()
        _fsync_directory(anchor)


def _purge_cleanup_record(root: Path, record: _CleanupRecord) -> None:
    garbage_root = root / GARBAGE_ROOT_NAME
    owner_root = garbage_root / GARBAGE_OWNER_ROOT_NAME
    current = _load_cleanup_owner(record.owner_path, garbage_root)
    if current != record:
        raise UnsafePublicationPath(
            f"cleanup ownership changed before purge: {record.owner_path}"
        )
    # Retire every immutable journal attestation only after the transaction
    # directory has been durably renamed into authenticated garbage. If the
    # process dies before this point, recovery still has an authenticated
    # active journal; if it dies afterwards, the garbage owner is sufficient.
    _purge_active_owner_records(
        root,
        transaction_id=record.transaction_id,
        token=record.token,
        device=record.device,
        inode=record.inode,
    )
    if record.garbage_entry is not None:
        try:
            shutil.rmtree(record.garbage_entry)
        except BaseException:
            if record.garbage_entry.exists():
                try:
                    _fsync_directory(garbage_root)
                except BaseException:
                    pass
            raise
        _fsync_directory(garbage_root)

    owner_metadata = record.owner_path.lstat()
    if (
        owner_metadata.st_dev != record.owner_device
        or owner_metadata.st_ino != record.owner_inode
        or not stat.S_ISREG(owner_metadata.st_mode)
    ):
        raise UnsafePublicationPath(
            f"cleanup ownership record changed before unlink: {record.owner_path}"
        )
    record.owner_path.unlink()
    _fsync_directory(owner_root)
    _cleanup_empty_private_roots(root, garbage_root)


def _remove_transaction_dir(
    root: Path,
    transaction_dir: Path,
    *,
    outcome: Literal["finalized", "rolled_back"],
    cleanup_token: str,
) -> None:
    """Atomically retire active state before interruptible recursive deletion."""

    resolved_root = root.resolve(strict=True)
    resolved_transaction = transaction_dir.resolve(strict=True)
    if resolved_transaction.parent != resolved_root:
        raise UnsafePublicationPath(
            f"refusing to clean transaction directory outside root: {transaction_dir}"
        )
    garbage_root = resolved_root / GARBAGE_ROOT_NAME
    if garbage_root.is_symlink() or (
        garbage_root.exists() and not garbage_root.is_dir()
    ):
        raise UnsafePublicationPath(
            f"transaction garbage root must be a real directory: {garbage_root}"
        )
    garbage_root.mkdir(exist_ok=True)
    _fsync_directory(resolved_root)
    owner_root = garbage_root / GARBAGE_OWNER_ROOT_NAME
    if owner_root.is_symlink() or (owner_root.exists() and not owner_root.is_dir()):
        raise UnsafePublicationPath(
            f"cleanup ownership root must be a real directory: {owner_root}"
        )
    owner_root.mkdir(mode=0o700, exist_ok=True)
    _fsync_directory(garbage_root)
    if not _TOKEN_PATTERN.fullmatch(cleanup_token):
        raise ValueError("cleanup token must be 32 lowercase hexadecimal characters")
    garbage_entry = garbage_root / f"{outcome}--{resolved_transaction.name}"
    if garbage_entry.exists() or garbage_entry.is_symlink():
        raise UnsafePublicationPath(
            f"transaction garbage destination is already occupied: {garbage_entry}"
        )
    transaction_metadata = resolved_transaction.stat(follow_symlinks=False)
    if not stat.S_ISDIR(transaction_metadata.st_mode):
        raise UnsafePublicationPath(
            f"transaction cleanup source must be a real directory: {resolved_transaction}"
        )
    owner_path = owner_root / f"{cleanup_token}.json"
    if owner_path.exists() or owner_path.is_symlink():
        raise UnsafePublicationPath(
            f"cleanup ownership token is already occupied: {owner_path}"
        )
    _write_cleanup_owner(
        owner_path,
        _cleanup_owner_payload(
            transaction_id=resolved_transaction.name,
            outcome=outcome,
            token=cleanup_token,
            garbage_name=garbage_entry.name,
            device=transaction_metadata.st_dev,
            inode=transaction_metadata.st_ino,
        ),
    )
    _rename_no_replace(resolved_transaction, garbage_entry)
    # The external ownership record survives partial recursive deletion. Once
    # both directory parents are durable, recovery authenticates the typed
    # outcome and original directory inode without trusting a basename alone.
    _fsync_directory(garbage_root)
    _fsync_directory(resolved_root)
    record = _load_cleanup_owner(owner_path, garbage_root)
    _purge_cleanup_record(resolved_root, record)


def _rollback_entries(
    anchor: Path,
    transaction_dir: Path,
    entries: Sequence[_Entry],
) -> list[str]:
    issues: list[str] = []
    for entry in reversed(entries):
        try:
            _resolve_below(entry.staged, transaction_dir / "staging", label="staged candidate")
            _resolve_below(entry.destination, anchor, label="destination")
            _resolve_below(entry.backup, transaction_dir, label="backup")
            destination_identity = capture_identity(entry.destination)
            staged_identity = capture_identity(entry.staged)
            backup_identity = capture_identity(entry.backup)

            # Candidate still staged and no backup means this entry was never
            # touched.  A cooperative or external publisher may legitimately
            # have changed the destination before our CAS check failed; leave
            # that state alone instead of claiming ownership of it.
            if (
                _identities_match(staged_identity, entry.candidate)
                and backup_identity.kind == "absent"
            ):
                continue

            if _identities_match(destination_identity, entry.candidate):
                if staged_identity.kind != "absent":
                    issues.append(
                        f"both destination and staging contain candidate for {entry.destination}"
                    )
                    continue
                entry.staged.parent.mkdir(parents=True, exist_ok=True)
                _rename_no_replace(entry.destination, entry.staged)
                _fsync_directory(entry.staged.parent)
                _fsync_directory(entry.destination.parent)
                destination_identity = PathIdentity(kind="absent")
            elif not (
                destination_identity.kind == "absent"
                or _identities_match(destination_identity, entry.expected_before)
            ):
                issues.append(
                    f"destination identity is not owned by transaction: {entry.destination}"
                )
                continue

            if entry.expected_before.kind == "absent":
                if destination_identity.kind != "absent":
                    issues.append(
                        f"create-only destination could not be restored to absent: "
                        f"{entry.destination}"
                    )
                if backup_identity.kind != "absent":
                    issues.append(
                        f"unexpected backup exists for create-only destination: {entry.backup}"
                    )
                continue

            if _identities_match(destination_identity, entry.expected_before):
                continue
            if not _identities_match(backup_identity, entry.expected_before):
                issues.append(
                    f"prior-state backup is missing or changed for {entry.destination}"
                )
                continue
            _rename_no_replace(entry.backup, entry.destination)
            _fsync_directory(entry.destination.parent)
            _fsync_directory(entry.backup.parent)
            restored = capture_identity(entry.destination)
            if not _identities_match(restored, entry.expected_before):
                issues.append(f"restored identity mismatch for {entry.destination}")
        except BaseException as exc:
            issues.append(f"rollback error for {entry.destination}: {exc}")
    return issues


def _committed_state_issues(
    anchor: Path,
    entries: Sequence[_Entry],
) -> list[str]:
    issues: list[str] = []
    for entry in entries:
        try:
            _resolve_below(
                entry.destination,
                anchor,
                label="committed destination",
            )
            current = capture_identity(entry.destination)
        except BaseException as exc:
            issues.append(f"cannot verify committed destination {entry.destination}: {exc}")
            continue
        if not _identities_match(current, entry.candidate):
            issues.append(
                f"committed destination no longer matches its candidate: {entry.destination}"
            )
    return issues


def _entries_from_journal(
    anchor: Path,
    transaction_dir: Path,
    payload: dict[str, Any],
) -> list[_Entry]:
    expected_journal_keys = {
        "version",
        "transaction_id",
        "scope",
        "status",
        "cleanup_token",
        "generation",
        "entries",
    }
    if set(payload) != expected_journal_keys:
        raise ValueError(
            "journal fields differ from the closed transaction schema"
        )
    if payload.get("version") != JOURNAL_VERSION:
        raise ValueError(f"unsupported journal version: {payload.get('version')!r}")
    if payload.get("transaction_id") != transaction_dir.name:
        raise ValueError("journal transaction_id does not match directory name")
    scope = payload.get("scope")
    if not isinstance(scope, str) or not _SCOPE_PATTERN.fullmatch(scope):
        raise ValueError("journal scope is invalid")
    if not transaction_dir.name.startswith(f"{scope}-"):
        raise ValueError("journal transaction_id is not owned by its scope")
    cleanup_token = payload.get("cleanup_token")
    if not isinstance(cleanup_token, str) or not _TOKEN_PATTERN.fullmatch(cleanup_token):
        raise ValueError("journal cleanup_token is invalid")
    generation = payload.get("generation")
    if (
        not isinstance(generation, int)
        or isinstance(generation, bool)
        or generation < 0
        or generation >= 10**20
    ):
        raise ValueError("journal generation is invalid")
    raw_entries = payload.get("entries")
    if not isinstance(raw_entries, list):
        raise ValueError("journal entries must be an array")
    entries: list[_Entry] = []
    for index, raw in enumerate(raw_entries):
        if not isinstance(raw, dict):
            raise ValueError(f"journal entry {index} must be an object")
        if set(raw) != {
            "staged",
            "destination",
            "backup",
            "mode",
            "expected_before",
            "candidate",
        }:
            raise ValueError(f"journal entry {index} has unknown or missing fields")
        staged_rel = _safe_relative_path(raw.get("staged"), label="journal staged path")
        destination_rel = _safe_relative_path(
            raw.get("destination"), label="journal destination path"
        )
        backup_rel = _safe_relative_path(raw.get("backup"), label="journal backup path")
        if not staged_rel.parts or staged_rel.parts[0] != "staging":
            raise ValueError(f"journal entry {index} staged path is outside staging/")
        if not backup_rel.parts or backup_rel.parts[0] != "backups":
            raise ValueError(f"journal entry {index} backup path is outside backups/")
        staged = _resolve_below(
            transaction_dir.joinpath(*staged_rel.parts),
            transaction_dir,
            label="journal staged candidate",
        )
        backup = _resolve_below(
            transaction_dir.joinpath(*backup_rel.parts),
            transaction_dir,
            label="journal backup",
        )
        destination = _resolve_below(
            anchor.joinpath(*destination_rel.parts), anchor, label="journal destination"
        )
        mode = raw.get("mode")
        if mode not in {"replace", "create_only"}:
            raise ValueError(f"journal entry {index} has invalid mode {mode!r}")
        entry = _Entry(
            staged=staged,
            destination=destination,
            backup=backup,
            mode=mode,
            expected_before=_identity_from_json(raw.get("expected_before")),
            candidate=_identity_from_json(raw.get("candidate")),
        )
        if entry.mode == "create_only" and entry.expected_before.kind != "absent":
            raise ValueError(
                f"journal entry {index} create_only state was not captured absent"
            )
        if (
            entry.expected_before.exists
            and entry.expected_before.kind != entry.candidate.kind
        ):
            raise ValueError(
                f"journal entry {index} changes destination object kind"
            )
        for existing in entries:
            if _paths_overlap(existing.destination, destination):
                raise ValueError("journal contains overlapping destinations")
            if _paths_overlap(existing.staged, staged):
                raise ValueError("journal contains overlapping staged candidates")
            if _paths_overlap(existing.backup, backup):
                raise ValueError("journal contains overlapping backup paths")
        entries.append(entry)
    return entries


def recover_incomplete_transactions(anchor: Path) -> tuple[RecoveryResult, ...]:
    """Recover every journal whose filesystem state has one safe interpretation."""

    anchor = anchor.resolve(strict=True)
    if not anchor.is_dir():
        raise NotADirectoryError(anchor)
    root = anchor / TRANSACTION_ROOT_NAME
    lock = _ProjectLock(anchor)
    lock.acquire()
    results: list[RecoveryResult] = []
    try:
        if not root.exists():
            return ()
        try:
            cleanup_records = _cleanup_records(anchor)
        except BaseException as exc:
            cleanup_records = []
            results.append(RecoveryResult("garbage", "blocked", (str(exc),)))
        for record in cleanup_records:
            try:
                _purge_cleanup_record(root, record)
                results.append(
                    RecoveryResult(record.transaction_id, record.outcome)
                )
            except BaseException as exc:
                results.append(
                    RecoveryResult(record.transaction_id, "blocked", (str(exc),))
                )
        if not root.exists():
            return tuple(results)
        for transaction_dir in _transaction_entries(anchor):
            if transaction_dir.is_symlink() or not transaction_dir.is_dir():
                results.append(
                    RecoveryResult(
                        transaction_dir.name,
                        "blocked",
                        ("transaction-root entry is not a real directory",),
                    )
                )
                continue
            journal_path = transaction_dir / JOURNAL_NAME
            try:
                payload, journal_bytes = _strict_json_load_with_bytes(
                    journal_path,
                    label="transaction journal",
                )
                _authenticate_active_journal(
                    root,
                    transaction_dir,
                    payload,
                    journal_bytes,
                )
                entries = _entries_from_journal(anchor, transaction_dir, payload)
                cleanup_token = str(payload["cleanup_token"])
                status = payload.get("status")
                if status not in {
                    "staging",
                    "publishing",
                    "recovery_required",
                    "committed",
                }:
                    raise ValueError(f"invalid journal status: {status!r}")
                if status == "committed":
                    issues = _committed_state_issues(anchor, entries)
                    if issues:
                        results.append(
                            RecoveryResult(
                                transaction_dir.name,
                                "blocked",
                                tuple(issues),
                            )
                        )
                        continue
                    _remove_transaction_dir(
                        root,
                        transaction_dir,
                        outcome="finalized",
                        cleanup_token=cleanup_token,
                    )
                    results.append(
                        RecoveryResult(transaction_dir.name, "finalized")
                    )
                    continue
                issues = _rollback_entries(anchor, transaction_dir, entries)
                if issues:
                    results.append(
                        RecoveryResult(
                            transaction_dir.name,
                            "blocked",
                            tuple(issues),
                        )
                    )
                    continue
                _remove_transaction_dir(
                    root,
                    transaction_dir,
                    outcome="rolled_back",
                    cleanup_token=cleanup_token,
                )
                results.append(
                    RecoveryResult(transaction_dir.name, "rolled_back")
                )
            except BaseException as exc:
                results.append(
                    RecoveryResult(
                        transaction_dir.name,
                        "blocked",
                        (str(exc),),
                    )
                )
    finally:
        lock.release()
    return tuple(results)


__all__ = [
    "ActiveTransactionError",
    "CompareAndSwapError",
    "PathIdentity",
    "PublicationLock",
    "PublicationTransaction",
    "PublicationTransactionError",
    "RecoveryResult",
    "TransactionBusyError",
    "TransactionCommittedCleanupError",
    "TransactionRollbackError",
    "UnsupportedTransactionPlatform",
    "UnsafePublicationPath",
    "active_transactions",
    "atomic_rename_no_replace",
    "assert_no_active_transactions",
    "capture_identity",
    "pending_transaction_cleanups",
    "publication_lock",
    "recover_incomplete_transactions",
]
