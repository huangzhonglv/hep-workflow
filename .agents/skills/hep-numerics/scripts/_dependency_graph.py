"""Content-addressed dependency graph primitives.

This module deliberately contains no workflow-specific dependency discovery.
Producers supply an explicit set of :class:`DependencySpec` objects; consumers
independently derive the set they expect and pass it back to
``verify_dependency_graph``.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
from stat import S_ISLNK, S_ISREG
from typing import Any, Iterable, Sequence


GRAPH_VERSION = "sha256-bytes-v1"
VERIFIED = "verified"
LEGACY_UNVERIFIED = "legacy-unverified"
SCOPES = frozenset({"project", "repository"})
_SHA256_PREFIX = "sha256:"


@dataclass(frozen=True, slots=True)
class DependencySpec:
    """One expected dependency, identified independently of its checksum."""

    scope: str
    role: str
    path: str


def sha256_file(path: str | os.PathLike[str]) -> str:
    """Return the SHA-256 of a regular file's exact bytes."""

    candidate = Path(path)
    try:
        metadata = candidate.lstat()
    except OSError as exc:
        raise ValueError(f"cannot stat dependency file {candidate}: {exc}") from exc
    if S_ISLNK(metadata.st_mode):
        raise ValueError(f"dependency file must not be a symlink: {candidate}")
    if not S_ISREG(metadata.st_mode):
        raise ValueError(f"dependency path is not a regular file: {candidate}")

    digest = hashlib.sha256()
    try:
        with candidate.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise ValueError(f"cannot read dependency file {candidate}: {exc}") from exc
    return _SHA256_PREFIX + digest.hexdigest()


def _validate_scope(scope: object) -> str:
    if not isinstance(scope, str) or scope not in SCOPES:
        raise ValueError(f"dependency scope must be one of {sorted(SCOPES)}, got {scope!r}")
    return scope


def _validate_role(role: object) -> str:
    if (
        not isinstance(role, str)
        or not role
        or role != role.strip()
        or any(character.isspace() for character in role)
    ):
        raise ValueError("dependency role must be a nonempty whitespace-free string")
    return role


def _validate_relative_path(path: object) -> str:
    if not isinstance(path, str) or not path:
        raise ValueError("dependency path must be a nonempty string")
    if "\\" in path:
        raise ValueError(f"dependency path must use POSIX separators: {path!r}")
    if path.startswith("/") or (
        len(path) >= 3 and path[0].isalpha() and path[1:3] == ":/"
    ):
        raise ValueError(f"dependency path must be relative: {path!r}")
    if "\x00" in path:
        raise ValueError("dependency path must not contain NUL")
    components = path.split("/")
    if any(component in {"", ".", ".."} for component in components):
        raise ValueError(
            f"dependency path contains an empty, dot, or parent component: {path!r}"
        )
    return path


def _resolved_root(root: str | os.PathLike[str]) -> Path:
    candidate = Path(root)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"cannot resolve dependency root {candidate}: {exc}") from exc
    if not resolved.is_dir():
        raise ValueError(f"dependency root is not a directory: {candidate}")
    return resolved


def _relative_input_path(
    root: str | os.PathLike[str],
    path: str | os.PathLike[str],
) -> str:
    root_path = Path(root)
    resolved_root = _resolved_root(root_path)
    candidate = Path(path)
    if not candidate.is_absolute():
        return _validate_relative_path(os.fspath(path))

    try:
        resolved_candidate = candidate.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise ValueError(
            f"cannot resolve absolute dependency path {candidate}: {exc}"
        ) from exc
    if not resolved_candidate.is_relative_to(resolved_root):
        raise ValueError(f"absolute dependency path is outside its root: {candidate}")

    # Preserve the caller's lexical suffix below a trusted spelling of the root,
    # so _safe_regular_file can still reject every symlink component below that
    # anchor.  The ancestor search supports system aliases such as macOS /var ->
    # /private/var without normalizing a candidate-side symlink out of evidence.
    relative: Path | None = None
    for possible_root in (root_path.absolute(), resolved_root):
        try:
            relative = candidate.relative_to(possible_root)
            break
        except ValueError:
            continue
    if relative is None:
        for possible_root in candidate.parents:
            try:
                if possible_root.resolve(strict=False) == resolved_root:
                    relative = candidate.relative_to(possible_root)
                    break
            except (OSError, RuntimeError):
                continue
    if relative is None:
        raise ValueError(
            "absolute dependency path cannot be expressed beneath its trusted "
            f"root without resolving candidate components: {candidate}"
        )
    return _validate_relative_path(relative.as_posix())


def _safe_regular_file(
    root: str | os.PathLike[str],
    relative_path: str,
) -> tuple[Path, tuple[int, int]]:
    relative_path = _validate_relative_path(relative_path)
    resolved_root = _resolved_root(root)
    current = resolved_root
    components = relative_path.split("/")
    for index, component in enumerate(components):
        current = current / component
        try:
            metadata = current.lstat()
        except OSError as exc:
            raise ValueError(
                f"cannot stat dependency path {relative_path!r}: {exc}"
            ) from exc
        if S_ISLNK(metadata.st_mode):
            raise ValueError(
                f"dependency path contains a symlink component: {relative_path!r}"
            )
        if index < len(components) - 1 and not current.is_dir():
            raise ValueError(
                f"dependency path has a non-directory component: {relative_path!r}"
            )

    if not S_ISREG(metadata.st_mode):
        raise ValueError(f"dependency path is not a regular file: {relative_path!r}")
    try:
        resolved_file = current.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"cannot resolve dependency file {relative_path!r}: {exc}") from exc
    if not resolved_file.is_relative_to(resolved_root):
        raise ValueError(f"dependency path escapes its root: {relative_path!r}")
    return resolved_file, (metadata.st_dev, metadata.st_ino)


def make_spec(
    scope: str,
    role: str,
    root: str | os.PathLike[str],
    path: str | os.PathLike[str],
) -> DependencySpec:
    """Create a spec with a canonical root-relative POSIX path.

    ``path`` may be root-relative or an absolute path lexically below ``root``;
    only the canonical relative spelling is retained in the returned spec.
    """

    checked_scope = _validate_scope(scope)
    checked_role = _validate_role(role)
    relative_path = _relative_input_path(root, path)
    _safe_regular_file(root, relative_path)
    return DependencySpec(checked_scope, checked_role, relative_path)


def _root_for_scope(
    scope: str,
    project_root: str | os.PathLike[str],
    repo_root: str | os.PathLike[str],
) -> Path:
    return _resolved_root(project_root if scope == "project" else repo_root)


def _canonical_graph_bytes(entries: Sequence[dict[str, str]]) -> bytes:
    payload = {
        "entries": sorted(
            entries,
            key=lambda entry: (entry["scope"], entry["path"], entry["role"]),
        ),
        "verification_status": VERIFIED,
        "version": GRAPH_VERSION,
    }
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _root_sha256(entries: Sequence[dict[str, str]]) -> str:
    return _SHA256_PREFIX + hashlib.sha256(_canonical_graph_bytes(entries)).hexdigest()


def _materialize_specs(
    project_root: str | os.PathLike[str],
    repo_root: str | os.PathLike[str],
    specs: Iterable[DependencySpec],
) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    keys: set[tuple[str, str, str]] = set()
    inodes: dict[tuple[int, int], tuple[str, str, str]] = {}

    for index, spec in enumerate(specs):
        if not isinstance(spec, DependencySpec):
            raise ValueError(f"dependency spec {index} is not a DependencySpec")
        scope = _validate_scope(spec.scope)
        role = _validate_role(spec.role)
        relative_path = _validate_relative_path(spec.path)
        key = (scope, relative_path, role)
        if key in keys:
            raise ValueError(f"duplicate dependency key: {key!r}")
        keys.add(key)

        root = _root_for_scope(scope, project_root, repo_root)
        file_path, inode = _safe_regular_file(root, relative_path)
        prior = inodes.get(inode)
        if prior is not None:
            raise ValueError(
                f"dependency paths alias the same filesystem object: {prior!r} and {key!r}"
            )
        inodes[inode] = key
        entries.append(
            {
                "scope": scope,
                "role": role,
                "path": relative_path,
                "sha256": sha256_file(file_path),
            }
        )

    return sorted(entries, key=lambda entry: (entry["scope"], entry["path"], entry["role"]))


def build_dependency_graph(
    project_root: str | os.PathLike[str],
    repo_root: str | os.PathLike[str],
    specs: Iterable[DependencySpec],
) -> dict[str, Any]:
    """Build a deterministic verified dependency graph."""

    entries = _materialize_specs(project_root, repo_root, specs)
    if not entries:
        raise ValueError("verified dependency graph requires at least one dependency spec")
    return {
        "version": GRAPH_VERSION,
        "verification_status": VERIFIED,
        "entries": entries,
        "root_sha256": _root_sha256(entries),
    }


def _valid_sha256(value: object) -> bool:
    if not isinstance(value, str) or not value.startswith(_SHA256_PREFIX):
        return False
    digest = value[len(_SHA256_PREFIX) :]
    return len(digest) == 64 and all(character in "0123456789abcdef" for character in digest)


def verify_dependency_graph(
    graph: object,
    project_root: str | os.PathLike[str],
    repo_root: str | os.PathLike[str],
    expected_specs: Iterable[DependencySpec] | None = None,
    required_roles: Iterable[str] | None = None,
    allow_legacy: bool = False,
    check_current_bytes: bool = True,
) -> list[str]:
    """Return deterministic validation errors for a persisted dependency graph.

    ``check_current_bytes=False`` is reserved for validating historical evidence
    that is already classified as stale. It still requires safe existing files,
    canonical entries, a valid graph root, and exact expected key coverage; only
    equality between recorded hashes and the files' current bytes is skipped.
    """

    if not isinstance(graph, dict):
        return ["dependency graph must be an object"]

    errors: list[str] = []
    allowed_top_level = {
        "version",
        "verification_status",
        "entries",
        "root_sha256",
        "reason",
    }
    extra_fields = sorted(set(graph) - allowed_top_level)
    if extra_fields:
        errors.append(f"dependency graph has unexpected fields: {extra_fields}")
    if graph.get("version") != GRAPH_VERSION:
        errors.append(
            f"dependency graph version must be {GRAPH_VERSION!r}, got {graph.get('version')!r}"
        )

    status = graph.get("verification_status")
    if status == LEGACY_UNVERIFIED:
        reason = graph.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            errors.append("legacy-unverified dependency graph requires a nonempty reason")
        if "entries" in graph or "root_sha256" in graph:
            errors.append(
                "legacy-unverified dependency graph must not contain entries or root_sha256"
            )
        if not allow_legacy:
            errors.append("legacy-unverified dependency graph is not allowed")
        return errors
    if status != VERIFIED:
        errors.append(
            "dependency graph verification_status must be 'verified' or 'legacy-unverified'"
        )
        return errors
    if "reason" in graph:
        errors.append("verified dependency graph must not contain reason")

    raw_entries = graph.get("entries")
    if not isinstance(raw_entries, list):
        errors.append("verified dependency graph requires an entries array")
        return errors
    if not raw_entries:
        errors.append("verified dependency graph entries must not be empty")

    valid_entries: list[dict[str, str]] = []
    keys: set[tuple[str, str, str]] = set()
    inodes: dict[tuple[int, int], tuple[str, str, str]] = {}
    for index, raw_entry in enumerate(raw_entries):
        prefix = f"entries[{index}]"
        if not isinstance(raw_entry, dict):
            errors.append(f"{prefix} must be an object")
            continue
        expected_fields = {"scope", "role", "path", "sha256"}
        if set(raw_entry) != expected_fields:
            errors.append(
                f"{prefix} fields must be exactly {sorted(expected_fields)}, "
                f"got {sorted(raw_entry)}"
            )
            continue
        try:
            scope = _validate_scope(raw_entry.get("scope"))
            role = _validate_role(raw_entry.get("role"))
            relative_path = _validate_relative_path(raw_entry.get("path"))
        except ValueError as exc:
            errors.append(f"{prefix}: {exc}")
            continue
        declared_sha256 = raw_entry.get("sha256")
        if not _valid_sha256(declared_sha256):
            errors.append(f"{prefix}.sha256 must be lowercase sha256:<64 hex>")
            continue

        key = (scope, relative_path, role)
        if key in keys:
            errors.append(f"duplicate dependency key: {key!r}")
            continue
        keys.add(key)
        try:
            root = _root_for_scope(scope, project_root, repo_root)
            file_path, inode = _safe_regular_file(root, relative_path)
            prior = inodes.get(inode)
            if prior is not None:
                errors.append(
                    "dependency paths alias the same filesystem object: "
                    f"{prior!r} and {key!r}"
                )
            else:
                inodes[inode] = key
            if check_current_bytes:
                actual_sha256 = sha256_file(file_path)
                if declared_sha256 != actual_sha256:
                    errors.append(
                        f"{prefix}.sha256 does not match current exact bytes for "
                        f"{scope}:{relative_path}"
                    )
        except ValueError as exc:
            errors.append(f"{prefix}: {exc}")

        valid_entries.append(
            {
                "scope": scope,
                "role": role,
                "path": relative_path,
                "sha256": declared_sha256,
            }
        )

    canonical_entries = sorted(
        valid_entries,
        key=lambda entry: (entry["scope"], entry["path"], entry["role"]),
    )
    if len(valid_entries) == len(raw_entries) and valid_entries != canonical_entries:
        errors.append("dependency graph entries are not in canonical scope/path/role order")

    declared_root = graph.get("root_sha256")
    if not _valid_sha256(declared_root):
        errors.append(
            "verified dependency graph requires root_sha256 as lowercase sha256:<64 hex>"
        )
    elif len(valid_entries) == len(raw_entries):
        expected_root = _root_sha256(canonical_entries)
        if declared_root != expected_root:
            errors.append("dependency graph root_sha256 does not match canonical entries")

    if expected_specs is not None:
        try:
            expected_entries = _materialize_specs(project_root, repo_root, expected_specs)
        except (TypeError, ValueError) as exc:
            errors.append(f"invalid expected dependency specs: {exc}")
        else:
            expected_keys = {
                (entry["scope"], entry["path"], entry["role"])
                for entry in expected_entries
            }
            actual_keys = {
                (entry["scope"], entry["path"], entry["role"])
                for entry in valid_entries
            }
            missing = sorted(expected_keys - actual_keys)
            unexpected = sorted(actual_keys - expected_keys)
            if missing:
                errors.append(f"dependency graph is missing expected entries: {missing}")
            if unexpected:
                errors.append(f"dependency graph has unexpected entries: {unexpected}")

    if required_roles is not None:
        checked_roles: set[str] = set()
        for role in required_roles:
            try:
                checked_roles.add(_validate_role(role))
            except ValueError as exc:
                errors.append(f"invalid required role: {exc}")
        actual_roles = {entry["role"] for entry in valid_entries}
        missing_roles = sorted(checked_roles - actual_roles)
        if missing_roles:
            errors.append(f"dependency graph is missing required roles: {missing_roles}")

    return errors
