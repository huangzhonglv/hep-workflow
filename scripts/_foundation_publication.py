#!/usr/bin/env python3
"""Owned candidate attempts for transactional foundation-skill publication."""

from __future__ import annotations

import importlib.util
import io
import json
import os
import re
import shutil
import stat
import sys
import uuid
from copy import deepcopy
from contextlib import redirect_stdout
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Literal

from _publication_transaction import (
    PathIdentity,
    PublicationTransaction,
    TransactionCommittedCleanupError,
    capture_identity,
    publication_lock,
)
from _strict_json import load_json


ATTEMPT_ROOT_NAME = ".hep-workflow-foundation-attempts"
RESERVATION_NAME = ".reservation.json"
CANDIDATE_DIR_NAME = "candidate"
ATTEMPT_ID_PATTERN = re.compile(r"^[a-f0-9]{32}$")
MODEL_COMPLETE_PATTERN = re.compile(r"^model_complete_v[0-9]+$")
MODEL_VERSION_PATTERN = re.compile(r"^v([0-9]+)$")
FOUNDATION_ROOTS = ("idea", "model", "constraints", "literature")
TEST_FAILURE_ENV = "HEP_WORKFLOW_TEST_FAIL_FOUNDATION_AFTER"


@dataclass(frozen=True)
class ModeSpec:
    owner: str
    mode: str
    roots: tuple[str, ...]
    artifact_fields: frozenset[str]
    manifest_policy: str
    allowed_actions: frozenset[str]
    require_model_completion: bool = False
    required_actions: frozenset[str] = frozenset()


MODE_SPECS: dict[tuple[str, str], ModeSpec] = {
    ("hep-idea", "initialize"): ModeSpec(
        owner="hep-idea",
        mode="initialize",
        roots=("idea", "model", "constraints"),
        artifact_fields=frozenset({"idea", "model", "constraints"}),
        manifest_policy="absent",
        allowed_actions=frozenset(
            {"idea_complete", "constraints_complete", "model_complete"}
        ),
        require_model_completion=True,
        required_actions=frozenset({"idea_complete", "constraints_complete"}),
    ),
    ("hep-idea", "revise"): ModeSpec(
        owner="hep-idea",
        mode="revise",
        roots=("model", "constraints"),
        artifact_fields=frozenset({"model", "constraints"}),
        manifest_policy="present",
        allowed_actions=frozenset(
            {
                "model_complete",
                "model_updated",
                "constraints_updated",
                "benchmarks_updated",
            }
        ),
    ),
    ("hep-idea", "direct"): ModeSpec(
        owner="hep-idea",
        mode="direct",
        roots=("model", "constraints"),
        artifact_fields=frozenset({"model", "constraints"}),
        manifest_policy="optional",
        allowed_actions=frozenset(
            {
                "model_complete",
                "model_updated",
                "constraints_complete",
                "constraints_updated",
                "benchmarks_updated",
            }
        ),
    ),
    ("hep-paper-formalize", "setup"): ModeSpec(
        owner="hep-paper-formalize",
        mode="setup",
        roots=("literature",),
        artifact_fields=frozenset({"literature"}),
        manifest_policy="optional",
        allowed_actions=frozenset({"literature_complete", "literature_updated"}),
    ),
    ("hep-paper-formalize", "formalize"): ModeSpec(
        owner="hep-paper-formalize",
        mode="formalize",
        roots=("model", "constraints", "literature"),
        artifact_fields=frozenset({"model", "constraints", "literature"}),
        manifest_policy="present",
        allowed_actions=frozenset(
            {"model_complete", "constraints_complete", "literature_updated"}
        ),
        require_model_completion=True,
        required_actions=frozenset({"constraints_complete"}),
    ),
}


@dataclass(frozen=True)
class Attempt:
    project_dir: Path
    attempt_dir: Path
    candidate_dir: Path
    attempt_id: str
    owner: str
    mode: str


@dataclass(frozen=True)
class FinalizationResult:
    status: str
    attempt: Attempt
    cleanup_pending: bool = False


@dataclass(frozen=True)
class PublicationEntry:
    relative: str
    staged: Path
    destination: Path
    expected_before: PathIdentity
    mode: Literal["replace", "create_only"]


def mode_spec(owner: str, mode: str) -> ModeSpec:
    try:
        return MODE_SPECS[(owner, mode)]
    except KeyError as exc:
        supported = ", ".join(
            f"{known_owner}:{known_mode}"
            for known_owner, known_mode in sorted(MODE_SPECS)
        )
        raise ValueError(
            f"unsupported foundation publication owner/mode {owner!r}/{mode!r}; "
            f"expected one of {supported}"
        ) from exc


def _utc_timestamp() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _require_real_directory(path: Path, label: str) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"{label} does not exist: {path}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise ValueError(f"{label} must be a real directory: {path}")


def _require_regular_file(path: Path, label: str, *, nonempty: bool = False) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"{label} does not exist: {path}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"{label} must be a regular file: {path}")
    if nonempty and metadata.st_size == 0:
        raise ValueError(f"{label} must not be empty: {path}")


def _write_json(path: Path, payload: dict[str, Any], *, create_only: bool = False) -> None:
    encoded = (
        json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    ).encode("utf-8")
    mode = "xb" if create_only else "wb"
    with path.open(mode) as handle:
        handle.write(encoded)


def _copy_regular_file(source: Path, destination: Path) -> None:
    _require_regular_file(source, "candidate source file")
    metadata = source.lstat()
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        current = os.fstat(descriptor)
        if not stat.S_ISREG(current.st_mode):
            raise ValueError(f"source changed to a non-regular file: {source}")
        with os.fdopen(descriptor, "rb") as reader, destination.open("xb") as writer:
            descriptor = -1
            shutil.copyfileobj(reader, writer)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    destination.chmod(stat.S_IMODE(metadata.st_mode))


def _copy_tree(
    source: Path,
    destination: Path,
    *,
    hidden_policy: str = "reject",
) -> None:
    if hidden_policy not in {"reject", "skip"}:
        raise ValueError(f"unknown hidden-path policy: {hidden_policy!r}")
    _require_real_directory(source, "candidate source directory")
    destination.mkdir(mode=stat.S_IMODE(source.lstat().st_mode), parents=True)
    for entry in sorted(
        source.rglob("*"),
        key=lambda path: os.fsencode(path.relative_to(source)),
    ):
        relative = entry.relative_to(source)
        if any(part.startswith(".") for part in relative.parts):
            if hidden_policy == "skip":
                continue
            raise ValueError(f"hidden foundation candidate path is not publishable: {relative}")
        metadata = entry.lstat()
        target = destination / relative
        if stat.S_ISLNK(metadata.st_mode):
            raise ValueError(f"foundation candidate path must not be a symlink: {entry}")
        if stat.S_ISDIR(metadata.st_mode):
            target.mkdir(mode=stat.S_IMODE(metadata.st_mode))
        elif stat.S_ISREG(metadata.st_mode):
            _copy_regular_file(entry, target)
        else:
            raise ValueError(f"foundation candidate contains a special object: {entry}")


def _identity_from_payload(payload: object, label: str) -> PathIdentity:
    expected = {"kind", "sha256", "size", "mode", "device", "inode"}
    if not isinstance(payload, dict) or set(payload) != expected:
        raise ValueError(f"{label} must contain exactly {sorted(expected)}")
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
            raise ValueError(f"{label} absent identity contains filesystem metadata")
        return identity
    if identity.kind not in {"file", "directory"}:
        raise ValueError(f"{label} has invalid kind {identity.kind!r}")
    if not isinstance(identity.sha256, str) or re.fullmatch(
        r"sha256:[a-f0-9]{64}", identity.sha256
    ) is None:
        raise ValueError(f"{label} has an invalid SHA-256")
    if any(
        not isinstance(value, int) or isinstance(value, bool) or value < 0
        for value in (
            identity.size,
            identity.mode,
            identity.device,
            identity.inode,
        )
    ):
        raise ValueError(f"{label} contains invalid filesystem metadata")
    return identity


def _semantic_identity(identity: PathIdentity) -> dict[str, Any]:
    return {
        "kind": identity.kind,
        "sha256": identity.sha256,
        "size": identity.size,
        "mode": identity.mode,
    }


def _semantic_identity_matches(path: Path, payload: object) -> bool:
    expected = {"kind", "sha256", "size", "mode"}
    if not isinstance(payload, dict) or set(payload) != expected:
        raise ValueError("published identity has an invalid shape")
    return _semantic_identity(capture_identity(path)) == payload


def _attempt_baseline(project_dir: Path, spec: ModeSpec) -> dict[str, Any]:
    return {
        relative: asdict(capture_identity(project_dir / relative))
        for relative in (*spec.roots, "manifest.json")
    }


def _validate_manifest_policy(spec: ModeSpec, manifest_identity: PathIdentity) -> None:
    if spec.manifest_policy == "absent" and manifest_identity.kind != "absent":
        raise ValueError(f"{spec.owner}:{spec.mode} requires an absent manifest.json")
    if spec.manifest_policy == "present" and manifest_identity.kind != "file":
        raise ValueError(f"{spec.owner}:{spec.mode} requires an existing manifest.json")
    if manifest_identity.kind not in {"absent", "file"}:
        raise ValueError("manifest.json must be absent or a regular file")


def _require_empty_fresh_roots(project_dir: Path, spec: ModeSpec) -> None:
    for root_name in spec.roots:
        root = project_dir / root_name
        if root.exists():
            _require_real_directory(root, f"fresh {root_name} root")
            for entry in root.rglob("*"):
                metadata = entry.lstat()
                if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(
                    metadata.st_mode
                ):
                    raise ValueError(
                        f"fresh {spec.owner}:{spec.mode} root contains an "
                        f"authoritative file or unsafe object: {entry}"
                    )


def initialize_attempt(
    project_dir: Path,
    *,
    owner: str,
    mode: str,
) -> Attempt:
    spec = mode_spec(owner, mode)
    project_dir = project_dir.resolve(strict=True)
    _require_real_directory(project_dir, "workspace project")

    with publication_lock(
        project_dir,
        "foundation-attempt-init",
        blocking=True,
    ):
        baseline = _attempt_baseline(project_dir, spec)
        manifest_identity = _identity_from_payload(
            baseline["manifest.json"],
            "manifest baseline",
        )
        _validate_manifest_policy(spec, manifest_identity)
        if manifest_identity.kind == "absent":
            _require_empty_fresh_roots(project_dir, spec)

        attempt_root = project_dir / ATTEMPT_ROOT_NAME
        if attempt_root.is_symlink() or (
            attempt_root.exists() and not attempt_root.is_dir()
        ):
            raise ValueError(f"foundation attempt root must be a real directory: {attempt_root}")
        attempt_root.mkdir(exist_ok=True)
        attempt_id = uuid.uuid4().hex
        attempt_dir = attempt_root / f"{owner}--{mode}--{attempt_id}"
        attempt_dir.mkdir(exist_ok=False)
        candidate_dir = attempt_dir / CANDIDATE_DIR_NAME
        candidate_dir.mkdir()

        for root_name in spec.roots:
            source = project_dir / root_name
            destination = candidate_dir / root_name
            before = capture_identity(source)
            if before.kind == "absent":
                destination.mkdir()
            elif before.kind == "directory":
                _copy_tree(source, destination, hidden_policy="skip")
            else:
                raise ValueError(f"foundation root must be a directory: {source}")
            if capture_identity(source) != before:
                raise ValueError(f"foundation root changed while seeding attempt: {source}")

        manifest_path = project_dir / "manifest.json"
        if manifest_identity.kind == "file":
            _copy_regular_file(manifest_path, candidate_dir / "manifest.json")
            if capture_identity(manifest_path) != manifest_identity:
                raise ValueError("manifest.json changed while seeding foundation attempt")

        reservation = {
            "version": 1,
            "kind": "foundation-publication-attempt",
            "status": "open",
            "owner": owner,
            "mode": mode,
            "attempt_id": attempt_id,
            "project_name": project_dir.name,
            "allowed_roots": list(spec.roots),
            "baseline": baseline,
            "created_at": _utc_timestamp(),
        }
        _write_json(
            attempt_dir / RESERVATION_NAME,
            reservation,
            create_only=True,
        )

    return Attempt(
        project_dir=project_dir,
        attempt_dir=attempt_dir,
        candidate_dir=candidate_dir,
        attempt_id=attempt_id,
        owner=owner,
        mode=mode,
    )


def _canonical_attempt(
    project_dir: Path,
    attempt_dir: Path,
    *,
    owner: str,
    mode: str,
    attempt_id: str,
) -> Attempt:
    if ATTEMPT_ID_PATTERN.fullmatch(attempt_id) is None:
        raise ValueError("attempt id must be 32 lowercase hexadecimal characters")
    expected = (
        project_dir
        / ATTEMPT_ROOT_NAME
        / f"{owner}--{mode}--{attempt_id}"
    )
    if attempt_dir != expected:
        raise PermissionError(
            f"attempt directory is not canonical for owner/mode/token: {attempt_dir}"
        )
    _require_real_directory(attempt_dir, "foundation attempt directory")
    candidate_dir = attempt_dir / CANDIDATE_DIR_NAME
    _require_real_directory(candidate_dir, "foundation candidate directory")
    return Attempt(
        project_dir=project_dir,
        attempt_dir=attempt_dir,
        candidate_dir=candidate_dir,
        attempt_id=attempt_id,
        owner=owner,
        mode=mode,
    )


def _load_reservation(attempt: Attempt, spec: ModeSpec) -> dict[str, Any]:
    reservation_path = attempt.attempt_dir / RESERVATION_NAME
    _require_regular_file(reservation_path, "foundation reservation", nonempty=True)
    payload = load_json(reservation_path)
    if not isinstance(payload, dict):
        raise ValueError("foundation reservation must be a JSON object")
    open_keys = {
        "version",
        "kind",
        "status",
        "owner",
        "mode",
        "attempt_id",
        "project_name",
        "allowed_roots",
        "baseline",
        "created_at",
    }
    published_keys = open_keys | {"published_at", "published_identities"}
    if payload.get("status") == "open":
        expected_keys = open_keys
    elif payload.get("status") == "published":
        expected_keys = published_keys
    else:
        raise ValueError("foundation reservation status must be open or published")
    if set(payload) != expected_keys:
        raise ValueError(
            "foundation reservation fields do not match its closed contract"
        )
    if payload.get("version") != 1 or payload.get("kind") != "foundation-publication-attempt":
        raise ValueError("unexpected foundation reservation type/version")
    for key, expected in (
        ("owner", attempt.owner),
        ("mode", attempt.mode),
        ("attempt_id", attempt.attempt_id),
        ("project_name", attempt.project_dir.name),
        ("allowed_roots", list(spec.roots)),
    ):
        if payload.get(key) != expected:
            raise ValueError(f"foundation reservation {key} does not match the request")
    baseline = payload.get("baseline")
    expected_paths = {*spec.roots, "manifest.json"}
    if not isinstance(baseline, dict) or set(baseline) != expected_paths:
        raise ValueError("foundation reservation baseline path set is invalid")
    for relative, identity_payload in baseline.items():
        _identity_from_payload(identity_payload, f"baseline {relative}")
    return payload


def _validate_candidate_layout(attempt: Attempt, spec: ModeSpec) -> None:
    allowed = {*spec.roots, "manifest.json"}
    actual = {entry.name for entry in attempt.candidate_dir.iterdir()}
    if actual != allowed:
        raise ValueError(
            "foundation candidate top-level entries must exactly equal "
            f"{sorted(allowed)}, got {sorted(actual)}"
        )
    for root_name in spec.roots:
        _require_real_directory(
            attempt.candidate_dir / root_name,
            f"candidate {root_name} root",
        )
    _require_regular_file(
        attempt.candidate_dir / "manifest.json",
        "candidate manifest",
        nonempty=True,
    )


def _action_class(action: object) -> str | None:
    if isinstance(action, str) and MODEL_COMPLETE_PATTERN.fullmatch(action):
        return "model_complete"
    return action if isinstance(action, str) else None


def _model_completion_action(
    baseline: dict[str, Any] | None,
    candidate: dict[str, Any],
) -> str:
    candidate_model = candidate.get("artifacts", {}).get("model")
    candidate_version = (
        candidate_model.get("version") if isinstance(candidate_model, dict) else None
    )
    candidate_match = (
        MODEL_VERSION_PATTERN.fullmatch(candidate_version)
        if isinstance(candidate_version, str)
        else None
    )
    if candidate_match is None:
        raise ValueError("model completion requires a candidate model version vN")
    candidate_number = int(candidate_match.group(1))

    baseline_model = (
        baseline.get("artifacts", {}).get("model")
        if isinstance(baseline, dict)
        else None
    )
    baseline_version = (
        baseline_model.get("version") if isinstance(baseline_model, dict) else None
    )
    baseline_done = (
        isinstance(baseline_model, dict)
        and baseline_model.get("status") == "done"
        and isinstance(baseline_version, str)
    )
    if baseline_done:
        baseline_match = MODEL_VERSION_PATTERN.fullmatch(baseline_version)
        if baseline_match is None:
            raise ValueError("baseline completed model version must match vN")
        expected_number = int(baseline_match.group(1)) + 1
        if candidate_number != expected_number:
            raise ValueError(
                "changed model/model-spec.json requires exactly one model version "
                f"increment, expected v{expected_number}, got {candidate_version!r}"
            )
    elif candidate_number != 1:
        raise ValueError(
            "first model completion must use version 'v1', got "
            f"{candidate_version!r}"
        )
    return f"model_complete_{candidate_version}"


def _expected_history_actions(
    baseline: dict[str, Any] | None,
    candidate: dict[str, Any],
    spec: ModeSpec,
    changed_paths: frozenset[str],
) -> frozenset[str]:
    idea_changed = any(path.startswith("idea/") for path in changed_paths)
    model_spec_changed = "model/model-spec.json" in changed_paths
    benchmarks_changed = "model/benchmarks.json" in changed_paths
    other_model_changed = any(
        path.startswith("model/")
        and path not in {"model/model-spec.json", "model/benchmarks.json"}
        for path in changed_paths
    )
    constraints_changed = any(
        path.startswith("constraints/") for path in changed_paths
    )
    literature_changed = any(
        path.startswith("literature/") for path in changed_paths
    )
    baseline_artifacts = (
        baseline.get("artifacts", {}) if isinstance(baseline, dict) else {}
    )
    baseline_model_done = (
        isinstance(baseline_artifacts.get("model"), dict)
        and baseline_artifacts["model"].get("status") == "done"
    )
    baseline_constraints_done = (
        isinstance(baseline_artifacts.get("constraints"), dict)
        and baseline_artifacts["constraints"].get("status") == "done"
    )
    baseline_literature_done = (
        isinstance(baseline_artifacts.get("literature"), dict)
        and baseline_artifacts["literature"].get("status") == "done"
    )

    expected: set[str] = set()
    if (spec.owner, spec.mode) == ("hep-idea", "initialize"):
        if idea_changed:
            expected.add("idea_complete")
        if model_spec_changed or other_model_changed or benchmarks_changed:
            expected.add(_model_completion_action(baseline, candidate))
        if constraints_changed:
            expected.add("constraints_complete")
    elif (spec.owner, spec.mode) == ("hep-idea", "revise"):
        if model_spec_changed:
            expected.add(_model_completion_action(baseline, candidate))
        elif other_model_changed:
            expected.add("model_updated")
        if benchmarks_changed:
            expected.add("benchmarks_updated")
        if constraints_changed:
            expected.add("constraints_updated")
    elif (spec.owner, spec.mode) == ("hep-idea", "direct"):
        if model_spec_changed:
            expected.add(_model_completion_action(baseline, candidate))
        elif other_model_changed:
            expected.add(
                "model_updated"
                if baseline_model_done
                else _model_completion_action(baseline, candidate)
            )
        if benchmarks_changed and baseline_model_done:
            expected.add("benchmarks_updated")
        elif benchmarks_changed and not any(
            action.startswith("model_complete_v") for action in expected
        ):
            expected.add(_model_completion_action(baseline, candidate))
        if constraints_changed:
            expected.add(
                "constraints_updated"
                if baseline_constraints_done
                else "constraints_complete"
            )
    elif (spec.owner, spec.mode) == ("hep-paper-formalize", "setup"):
        if literature_changed:
            expected.add(
                "literature_updated"
                if baseline_literature_done
                else "literature_complete"
            )
    elif (spec.owner, spec.mode) == ("hep-paper-formalize", "formalize"):
        if model_spec_changed or other_model_changed or benchmarks_changed:
            expected.add(_model_completion_action(baseline, candidate))
        if constraints_changed:
            expected.add("constraints_complete")
        if literature_changed:
            expected.add("literature_updated")
    else:  # pragma: no cover - mode_spec closes this set.
        raise ValueError(f"unsupported foundation action derivation {spec.owner}:{spec.mode}")
    return frozenset(expected)


def _validate_skill_manifest_scope(
    baseline: dict[str, Any] | None,
    candidate: dict[str, Any],
    spec: ModeSpec,
    *,
    project_name: str,
    changed_paths: frozenset[str],
) -> None:
    if candidate.get("project_name") != project_name:
        raise ValueError("candidate manifest project_name does not match project directory")
    if candidate.get("manifest_version") != 2:
        raise ValueError("foundation finalization requires manifest_version=2")
    artifacts = candidate.get("artifacts")
    history = candidate.get("history")
    if not isinstance(artifacts, dict) or not isinstance(history, list):
        raise ValueError("candidate manifest artifacts/history have invalid shapes")

    if baseline is None:
        for artifact_name, artifact in artifacts.items():
            if artifact_name in spec.artifact_fields or not isinstance(artifact, dict):
                continue
            if artifact.get("status") not in {"not_started", "skipped"}:
                raise ValueError(
                    f"fresh {spec.owner}:{spec.mode} candidate cannot publish "
                    f"unowned artifacts.{artifact_name} status={artifact.get('status')!r}"
                )
            for evidence_field in (
                "files",
                "analyses",
                "runs",
                "completed_tasks",
                "pending_tasks",
            ):
                evidence = artifact.get(evidence_field)
                if evidence is not None and evidence != []:
                    raise ValueError(
                        f"fresh unowned artifacts.{artifact_name}.{evidence_field} "
                        "must be empty"
                    )
            if (
                artifact.get("produced_by") is not None
                or artifact.get("timestamp") is not None
            ):
                raise ValueError(
                    f"fresh unowned artifacts.{artifact_name} must not claim a producer"
                )

    baseline_history: list[Any] = []
    if baseline is not None:
        for key, value in baseline.items():
            if key in {"last_updated", "active_model_version", "artifacts", "history"}:
                continue
            if candidate.get(key) != value:
                raise ValueError(
                    f"foundation candidate changed unrelated manifest field {key!r}"
                )
        baseline_artifacts = baseline.get("artifacts")
        if not isinstance(baseline_artifacts, dict):
            raise ValueError("baseline manifest artifacts must be an object")
        if set(artifacts) != set(baseline_artifacts):
            raise ValueError("foundation candidate changed the artifact key set")
        for name, value in baseline_artifacts.items():
            if name not in spec.artifact_fields and artifacts.get(name) != value:
                raise ValueError(
                    f"{spec.owner}:{spec.mode} changed unowned artifacts.{name} state"
                )
        baseline_history = baseline.get("history", [])
        if not isinstance(baseline_history, list):
            raise ValueError("baseline manifest history must be an array")
        if history[: len(baseline_history)] != baseline_history:
            raise ValueError("foundation candidate rewrote or reordered prior history")
        if "model" not in spec.artifact_fields and candidate.get(
            "active_model_version"
        ) != baseline.get("active_model_version"):
            raise ValueError("foundation candidate changed unowned active_model_version")

    appended = history[len(baseline_history) :]
    if not appended:
        raise ValueError("foundation candidate must append at least one owned history event")
    observed_classes: set[str] = set()
    observed_actions: list[str] = []
    for entry in appended:
        if not isinstance(entry, dict):
            raise ValueError("new foundation history entries must be objects")
        if entry.get("by") != spec.owner:
            raise ValueError("new foundation history entry has the wrong owner")
        action_class = _action_class(entry.get("action"))
        if action_class not in spec.allowed_actions:
            raise ValueError(
                f"{spec.owner}:{spec.mode} cannot emit history action "
                f"{entry.get('action')!r}"
            )
        observed_classes.add(str(action_class))
        observed_actions.append(str(entry.get("action")))
    missing = sorted(spec.required_actions - observed_classes)
    if missing:
        raise ValueError(
            f"{spec.owner}:{spec.mode} is missing required history actions {missing}"
        )
    if spec.require_model_completion and "model_complete" not in observed_classes:
        raise ValueError(
            f"{spec.owner}:{spec.mode} must append model_complete_vN"
        )
    if len(observed_actions) != len(set(observed_actions)):
        raise ValueError("foundation candidate contains duplicate history actions")
    expected_actions = _expected_history_actions(
        baseline,
        candidate,
        spec,
        changed_paths,
    )
    if frozenset(observed_actions) != expected_actions:
        raise ValueError(
            "foundation history actions do not match the actual changed file scope: "
            f"expected {sorted(expected_actions)}, got {sorted(observed_actions)}; "
            f"changed files are {sorted(changed_paths)}"
        )

    for artifact_name in spec.artifact_fields:
        artifact = artifacts.get(artifact_name)
        if not isinstance(artifact, dict):
            continue
        files = artifact.get("files")
        if isinstance(files, list):
            wrong_prefix = sorted(
                path
                for path in files
                if not isinstance(path, str)
                or not path.startswith(f"{artifact_name}/")
            )
            if wrong_prefix:
                raise ValueError(
                    f"artifacts.{artifact_name}.files contains paths outside its "
                    f"owner root: {wrong_prefix}"
                )
        if baseline is not None:
            baseline_artifact = baseline.get("artifacts", {}).get(artifact_name)
            if artifact == baseline_artifact:
                continue
        if (
            artifact.get("status") == "done"
            and artifact.get("produced_by") != spec.owner
        ):
            raise ValueError(
                f"done artifacts.{artifact_name} must be produced by {spec.owner}"
            )


def _load_manifest_helper(repo_root: Path) -> Any:
    path = (
        repo_root
        / ".agents"
        / "skills"
        / "hep-numerics"
        / "scripts"
        / "_manifest.py"
    )
    spec = importlib.util.spec_from_file_location("foundation_manifest_helper", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load manifest helper from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_workspace_validator(repo_root: Path) -> Any:
    path = repo_root / "scripts" / "validate_workspace_projects.py"
    spec = importlib.util.spec_from_file_location("foundation_workspace_validator", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load workspace validator from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _refresh_staleness(
    helper: Any,
    manifest: dict[str, Any],
    *,
    staged_roots: dict[str, Path],
    project_dir: Path,
    changed_paths: frozenset[str],
) -> dict[str, Any]:
    candidate = deepcopy(manifest)
    artifacts = candidate.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ValueError("candidate manifest artifacts must be an object")

    calculation_inputs_changed = bool(
        changed_paths
        & {
            "model/model-spec.json",
            "model/calc-tasks.json",
            "model/benchmarks.json",
        }
    )
    calculations = artifacts.get("calculations")
    if calculation_inputs_changed and isinstance(calculations, dict):
        calculation_status = calculations.get("status")
        if calculation_status == "in_progress":
            raise ValueError(
                "cannot publish load-bearing model changes while calculations "
                "status is in_progress"
            )
        completed_tasks = calculations.get("completed_tasks")
        if (
            calculation_status in {"done", "partial"}
            and isinstance(completed_tasks, list)
            and completed_tasks
        ):
            calculations["status"] = "stale"

    numerics = artifacts.get("numerics", {})
    analyses = numerics.get("analyses", []) if isinstance(numerics, dict) else []
    if not analyses:
        return candidate
    constraints_path = (
        staged_roots.get("constraints", project_dir / "constraints")
        / "constraints-data.json"
    )
    constraints_checksum = helper.file_sha256(constraints_path)
    model = candidate.get("artifacts", {}).get("model", {})
    if not isinstance(model, dict):
        raise ValueError("candidate manifest model artifact must be an object")
    candidate = helper.refresh_numerics_staleness_for_inputs(
        candidate,
        active_model=model,
        constraints_checksum=constraints_checksum,
    )
    calc_tasks_changed = "model/calc-tasks.json" in changed_paths
    benchmarks_changed = "model/benchmarks.json" in changed_paths
    if not calc_tasks_changed and not benchmarks_changed:
        return candidate

    refreshed_analyses: list[dict[str, Any]] = []
    for original in candidate["artifacts"]["numerics"]["analyses"]:
        analysis = deepcopy(original)
        dependencies = analysis.get("depends_on")
        calculation_dependency = (
            dependencies.get("calculations", {})
            if isinstance(dependencies, dict)
            else {}
        )
        consumes_calculations = bool(calculation_dependency.get("tasks", []))
        if analysis.get("status") in {"done", "partial"} and (
            calc_tasks_changed or (benchmarks_changed and consumes_calculations)
        ):
            analysis["status"] = "stale"
        refreshed_analyses.append(analysis)
    candidate["artifacts"]["numerics"] = helper.derive_numerics_artifact(
        refreshed_analyses
    )
    return candidate


def _build_foundation_overlay(
    transaction: PublicationTransaction,
    project_dir: Path,
    staged_roots: dict[str, Path],
    staged_manifest: Path,
) -> Path:
    overlay = transaction.stage_path("validation-overlay")
    overlay.mkdir()
    for root_name in FOUNDATION_ROOTS:
        source = staged_roots.get(root_name, project_dir / root_name)
        if not source.exists():
            continue
        if not source.is_dir() or source.is_symlink():
            raise ValueError(f"foundation overlay source must be a real directory: {source}")
        _copy_tree(
            source,
            overlay / root_name,
            hidden_policy="skip" if source == project_dir / root_name else "reject",
        )
    _copy_regular_file(staged_manifest, overlay / "manifest.json")
    return overlay


def _validate_foundation_overlay(repo_root: Path, overlay: Path) -> None:
    workspace = _load_workspace_validator(repo_root)
    validators = workspace.load_schema_validators(repo_root)
    loaded: dict[str, Any] = {}
    failures: list[str] = []
    for relpath, schema_name in workspace.ARTIFACT_SCHEMA_BY_RELATIVE_PATH.items():
        path = overlay / relpath
        if not path.exists():
            continue
        payload = load_json(path)
        loaded[relpath] = payload
        errors = workspace.validate_json_data(payload, validators[schema_name])
        failures.extend(f"{relpath}: {error}" for error in errors)
    if failures:
        raise ValueError(
            "foundation candidate schema validation failed: " + "; ".join(failures)
        )
    diagnostics = io.StringIO()
    with redirect_stdout(diagnostics):
        semantic_failures, _ = workspace.validate_manifest_and_global_identities(
            overlay,
            loaded,
            scope="foundation",
        )
        literature_failures, _ = workspace.validate_literature_manifest_files(
            overlay,
            loaded,
        )
        reference_failures, _ = workspace.validate_reproduction_reference_evidence(
            overlay,
            loaded,
        )
    if semantic_failures or literature_failures or reference_failures:
        detail = "; ".join(diagnostics.getvalue().splitlines())
        raise ValueError(
            "foundation candidate failed authoritative scoped workspace validation"
            + (f": {detail}" if detail else "")
        )


def _validate_intrinsic_numerics_evidence(
    helper: Any,
    project_dir: Path,
    manifest: dict[str, Any],
) -> None:
    numerics = manifest.get("artifacts", {}).get("numerics", {})
    analyses = numerics.get("analyses", []) if isinstance(numerics, dict) else []
    for analysis in analyses:
        if not isinstance(analysis, dict) or analysis.get("status") not in {
            "done",
            "partial",
            "stale",
        }:
            continue
        analysis_id = analysis.get("analysis_id")
        if not isinstance(analysis_id, str):
            raise ValueError("numerics analysis is missing analysis_id")
        metadata_path = (
            project_dir
            / "numerics"
            / "scan-results"
            / analysis_id
            / "scan.meta.json"
        )
        metadata = helper.load_json(metadata_path)
        snapshot = (
            metadata.get("scan_config_snapshot")
            if isinstance(metadata, dict)
            else None
        )
        if not isinstance(snapshot, dict):
            raise ValueError(
                f"{analysis_id} lacks an intrinsic scan_config_snapshot for "
                "foundation publication"
            )
        issues = helper.validate_scan_artifact_pair(
            project_dir,
            analysis_id,
            historical_scan_config_snapshot=snapshot,
        )
        if issues:
            raise ValueError(
                f"{analysis_id} intrinsic historical evidence is invalid: "
                + "; ".join(issues)
            )


def _validate_intrinsic_calculation_evidence(
    repo_root: Path,
    project_dir: Path,
    manifest: dict[str, Any],
) -> None:
    calculations = manifest.get("artifacts", {}).get("calculations", {})
    if not isinstance(calculations, dict) or calculations.get("status") != "stale":
        return
    workspace = _load_workspace_validator(repo_root)
    validators = workspace.load_schema_validators(repo_root)
    loaded = {"manifest.json": manifest}
    diagnostics = io.StringIO()
    with redirect_stdout(diagnostics):
        artifact_failures, _ = workspace.validate_calculations_artifact(
            project_dir,
            loaded,
        )
        output_failures, _ = workspace.validate_calculation_outputs(
            project_dir,
            validators,
            loaded,
        )
    if artifact_failures or output_failures:
        detail = "; ".join(diagnostics.getvalue().splitlines())
        raise ValueError(
            "stale calculation historical evidence is intrinsically invalid"
            + (f": {detail}" if detail else "")
        )


def _candidate_identities(attempt: Attempt, spec: ModeSpec) -> dict[str, PathIdentity]:
    return {
        relative: capture_identity(attempt.candidate_dir / relative)
        for relative in (*spec.roots, "manifest.json")
    }


def _regular_tree_files(
    root: Path,
    *,
    hidden_policy: str,
) -> dict[Path, Path]:
    if hidden_policy not in {"reject", "skip"}:
        raise ValueError(f"unknown hidden-path policy: {hidden_policy!r}")
    identity = capture_identity(root)
    if identity.kind == "absent":
        return {}
    if identity.kind != "directory":
        raise ValueError(f"foundation root must be a directory: {root}")
    files: dict[Path, Path] = {}
    for entry in sorted(
        root.rglob("*"),
        key=lambda path: os.fsencode(path.relative_to(root)),
    ):
        relative = entry.relative_to(root)
        if any(part.startswith(".") for part in relative.parts):
            if hidden_policy == "skip":
                continue
            raise ValueError(f"hidden foundation candidate path is not publishable: {relative}")
        metadata = entry.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise ValueError(f"foundation tree path must not be a symlink: {entry}")
        if stat.S_ISDIR(metadata.st_mode):
            continue
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError(f"foundation tree contains a special object: {entry}")
        files[relative] = entry
    return files


def _changed_owner_files(
    project_dir: Path,
    staged_roots: dict[str, Path],
    spec: ModeSpec,
) -> frozenset[str]:
    changed: set[str] = set()
    for root_name in spec.roots:
        live_files = _regular_tree_files(
            project_dir / root_name,
            hidden_policy="skip",
        )
        staged_files = _regular_tree_files(
            staged_roots[root_name],
            hidden_policy="reject",
        )
        for relative, staged_file in staged_files.items():
            live_file = live_files.get(relative)
            if live_file is None or _semantic_identity(
                capture_identity(staged_file)
            ) != _semantic_identity(capture_identity(live_file)):
                changed.add((Path(root_name) / relative).as_posix())
    return frozenset(changed)


def _first_absent_parent(
    project_dir: Path,
    relative_file: Path,
) -> Path | None:
    parts = relative_file.parts[:-1]
    for index in range(1, len(parts) + 1):
        relative = Path(*parts[:index])
        identity = capture_identity(project_dir / relative)
        if identity.kind == "absent":
            return relative
        if identity.kind != "directory":
            raise ValueError(
                f"foundation publication parent is not a directory: {project_dir / relative}"
            )
    return None


def _publication_entries(
    project_dir: Path,
    staged_roots: dict[str, Path],
    spec: ModeSpec,
) -> list[PublicationEntry]:
    entries: list[PublicationEntry] = []
    for root_name in spec.roots:
        staged_root = staged_roots[root_name]
        live_root = project_dir / root_name
        live_identity = capture_identity(live_root)
        if live_identity.kind == "absent":
            entries.append(
                PublicationEntry(
                    relative=root_name,
                    staged=staged_root,
                    destination=live_root,
                    expected_before=live_identity,
                    mode="create_only",
                )
            )
            continue
        if live_identity.kind != "directory":
            raise ValueError(f"foundation root must be a directory: {live_root}")

        live_files = _regular_tree_files(live_root, hidden_policy="skip")
        candidate_files = _regular_tree_files(staged_root, hidden_policy="reject")
        removed = sorted(
            path.as_posix() for path in set(live_files) - set(candidate_files)
        )
        if removed:
            raise ValueError(
                f"foundation publication does not support implicit deletion from {root_name}: "
                + ", ".join(removed)
            )

        new_directories: set[Path] = set()
        for relative in candidate_files:
            project_relative = Path(root_name) / relative
            absent_parent = _first_absent_parent(project_dir, project_relative)
            if absent_parent is not None:
                new_directories.add(absent_parent)

        top_level_new_directories = {
            candidate
            for candidate in new_directories
            if not any(
                other != candidate and other in candidate.parents
                for other in new_directories
            )
        }
        for relative_dir in sorted(
            top_level_new_directories,
            key=lambda path: path.as_posix(),
        ):
            staged_dir = staged_root / relative_dir.relative_to(root_name)
            _require_real_directory(staged_dir, "new foundation candidate directory")
            entries.append(
                PublicationEntry(
                    relative=relative_dir.as_posix(),
                    staged=staged_dir,
                    destination=project_dir / relative_dir,
                    expected_before=PathIdentity(kind="absent"),
                    mode="create_only",
                )
            )

        for relative, staged_file in sorted(
            candidate_files.items(),
            key=lambda item: item[0].as_posix(),
        ):
            project_relative = Path(root_name) / relative
            if any(
                parent == project_relative or parent in project_relative.parents
                for parent in top_level_new_directories
            ):
                continue
            destination = project_dir / project_relative
            expected = capture_identity(destination)
            candidate_identity = capture_identity(staged_file)
            if _semantic_identity(candidate_identity) == _semantic_identity(expected):
                continue
            entries.append(
                PublicationEntry(
                    relative=project_relative.as_posix(),
                    staged=staged_file,
                    destination=destination,
                    expected_before=expected,
                    mode="create_only" if expected.kind == "absent" else "replace",
                )
            )
    return sorted(entries, key=lambda entry: entry.relative)


def _check_baseline(project_dir: Path, reservation: dict[str, Any], spec: ModeSpec) -> None:
    baseline = reservation["baseline"]
    for relative in (*spec.roots, "manifest.json"):
        expected = _identity_from_payload(baseline[relative], f"baseline {relative}")
        current = capture_identity(project_dir / relative)
        if current != expected:
            raise ValueError(
                f"authoritative {relative} changed after foundation attempt allocation"
            )


def _published_attempt_is_current(
    attempt: Attempt,
    reservation: dict[str, Any],
    spec: ModeSpec,
) -> bool:
    published = reservation.get("published_identities")
    if not isinstance(published, dict) or not published:
        raise ValueError("published foundation reservation lacks identities")
    for relative, identity in published.items():
        if not isinstance(relative, str):
            raise ValueError("published foundation identity path is invalid")
        canonical = PurePosixPath(relative)
        if (
            canonical.is_absolute()
            or canonical.as_posix() != relative
            or not canonical.parts
            or any(part in {"", ".", ".."} for part in canonical.parts)
            or (
                relative != "manifest.json"
                and canonical.parts[0] not in spec.roots
            )
        ):
            raise ValueError("published foundation identity path is invalid")
        if not _semantic_identity_matches(
            attempt.project_dir.joinpath(*canonical.parts),
            identity,
        ):
            return False
    return True


def _failure_injector() -> Callable[[Path, int], None] | None:
    raw = os.environ.get(TEST_FAILURE_ENV)
    if raw is None:
        return None
    try:
        boundary = int(raw)
    except ValueError as exc:
        raise ValueError(f"{TEST_FAILURE_ENV} must be a positive integer") from exc
    if boundary < 1:
        raise ValueError(f"{TEST_FAILURE_ENV} must be a positive integer")

    def inject(destination: Path, index: int) -> None:
        if index == boundary:
            raise RuntimeError(
                f"injected foundation finalization failure after {destination}"
            )

    return inject


def finalize_attempt(
    repo_root: Path,
    project_dir: Path,
    attempt_dir: Path,
    *,
    owner: str,
    mode: str,
    attempt_id: str,
) -> FinalizationResult:
    spec = mode_spec(owner, mode)
    repo_root = repo_root.resolve(strict=True)
    project_dir = project_dir.resolve(strict=True)
    attempt_dir = attempt_dir.resolve(strict=True)
    attempt = _canonical_attempt(
        project_dir,
        attempt_dir,
        owner=owner,
        mode=mode,
        attempt_id=attempt_id,
    )
    helper = _load_manifest_helper(repo_root)

    with publication_lock(
        project_dir,
        "foundation-finalize",
        blocking=True,
    ) as lock:
        reservation = _load_reservation(attempt, spec)
        if reservation["status"] == "published":
            if not _published_attempt_is_current(attempt, reservation, spec):
                raise ValueError(
                    "published foundation attempt no longer matches authoritative state"
                )
            return FinalizationResult(status="already_published", attempt=attempt)

        _check_baseline(project_dir, reservation, spec)
        _validate_candidate_layout(attempt, spec)
        source_identities = _candidate_identities(attempt, spec)
        skill_manifest = load_json(attempt.candidate_dir / "manifest.json")
        if not isinstance(skill_manifest, dict):
            raise ValueError("candidate manifest must contain an object")
        baseline_manifest_identity = _identity_from_payload(
            reservation["baseline"]["manifest.json"],
            "manifest baseline",
        )
        baseline_manifest = (
            load_json(project_dir / "manifest.json")
            if baseline_manifest_identity.kind == "file"
            else None
        )
        if baseline_manifest is not None and not isinstance(baseline_manifest, dict):
            raise ValueError("baseline manifest must contain an object")
        manifest_before = capture_identity(project_dir / "manifest.json")
        reservation_before = capture_identity(attempt.attempt_dir / RESERVATION_NAME)
        with PublicationTransaction.begin(
            project_dir,
            f"foundation-{owner}-{mode}",
            lock=lock,
        ) as transaction:
            staged_roots: dict[str, Path] = {}
            for root_name in spec.roots:
                staged = transaction.stage_path(root_name)
                _copy_tree(attempt.candidate_dir / root_name, staged)
                staged_roots[root_name] = staged
            if _candidate_identities(attempt, spec) != source_identities:
                raise ValueError("foundation candidate changed while it was staged")

            publish_entries = _publication_entries(
                project_dir,
                staged_roots,
                spec,
            )
            if not publish_entries:
                raise ValueError(
                    "foundation candidate appends completion/update history but "
                    "does not change any owner artifact file"
                )
            changed_paths = _changed_owner_files(
                project_dir,
                staged_roots,
                spec,
            )
            _validate_skill_manifest_scope(
                baseline_manifest,
                skill_manifest,
                spec,
                project_name=project_dir.name,
                changed_paths=changed_paths,
            )

            manifest_candidate = _refresh_staleness(
                helper,
                deepcopy(skill_manifest),
                staged_roots=staged_roots,
                project_dir=project_dir,
                changed_paths=changed_paths,
            )
            staged_manifest = transaction.stage_path("manifest.json")
            _write_json(staged_manifest, manifest_candidate)
            overlay = _build_foundation_overlay(
                transaction,
                project_dir,
                staged_roots,
                staged_manifest,
            )
            _validate_foundation_overlay(repo_root, overlay)
            _validate_intrinsic_numerics_evidence(
                helper,
                project_dir,
                manifest_candidate,
            )
            _validate_intrinsic_calculation_evidence(
                repo_root,
                project_dir,
                manifest_candidate,
            )

            manifest_mode = "create_only" if manifest_before.kind == "absent" else "replace"
            published_identities = {
                entry.relative: _semantic_identity(capture_identity(entry.staged))
                for entry in publish_entries
            }
            published_identities["manifest.json"] = _semantic_identity(
                capture_identity(staged_manifest)
            )
            published_reservation = dict(reservation)
            published_reservation["status"] = "published"
            published_reservation["published_at"] = _utc_timestamp()
            published_reservation["published_identities"] = published_identities
            staged_reservation = transaction.stage_path(
                f"{ATTEMPT_ROOT_NAME}/{attempt.attempt_dir.name}/{RESERVATION_NAME}"
            )
            _write_json(staged_reservation, published_reservation)
            transaction.add(
                staged_reservation,
                attempt.attempt_dir / RESERVATION_NAME,
                mode="replace",
                expected_before=reservation_before,
            )
            for entry in publish_entries:
                transaction.add(
                    entry.staged,
                    entry.destination,
                    mode=entry.mode,
                    expected_before=entry.expected_before,
                )
            transaction.add(
                staged_manifest,
                project_dir / "manifest.json",
                mode=manifest_mode,
                expected_before=manifest_before,
            )

            def verify_candidate() -> None:
                if _candidate_identities(attempt, spec) != source_identities:
                    raise ValueError("foundation candidate changed before publication")
                _validate_foundation_overlay(repo_root, overlay)
                _validate_intrinsic_numerics_evidence(
                    helper,
                    project_dir,
                    manifest_candidate,
                )
                _validate_intrinsic_calculation_evidence(
                    repo_root,
                    project_dir,
                    manifest_candidate,
                )

            def verify_inputs() -> None:
                _check_baseline(project_dir, reservation, spec)

            def verify_published() -> None:
                published_manifest = load_json(project_dir / "manifest.json")
                if published_manifest != manifest_candidate:
                    raise ValueError("published manifest differs from foundation candidate")
                current_reservation = _load_reservation(attempt, spec)
                if current_reservation != published_reservation:
                    raise ValueError("published foundation reservation differs from candidate")
                if not _published_attempt_is_current(
                    attempt,
                    current_reservation,
                    spec,
                ):
                    raise ValueError("published foundation paths do not match candidate identities")

            transaction.commit(
                validate_candidate=verify_candidate,
                pre_publish_check=verify_inputs,
                post_publish_check=verify_published,
                after_publish_entry=_failure_injector(),
            )

    return FinalizationResult(status="published", attempt=attempt)


def finalize_with_cleanup_status(
    repo_root: Path,
    project_dir: Path,
    attempt_dir: Path,
    *,
    owner: str,
    mode: str,
    attempt_id: str,
) -> FinalizationResult:
    try:
        return finalize_attempt(
            repo_root,
            project_dir,
            attempt_dir,
            owner=owner,
            mode=mode,
            attempt_id=attempt_id,
        )
    except TransactionCommittedCleanupError as exc:
        project_dir = Path(project_dir).resolve(strict=True)
        attempt_dir = Path(attempt_dir).resolve(strict=True)
        attempt = _canonical_attempt(
            project_dir,
            attempt_dir,
            owner=owner,
            mode=mode,
            attempt_id=attempt_id,
        )
        print(
            "warning: foundation publication committed successfully, but private "
            f"cleanup is pending for transaction {exc.transaction_id}: "
            f"{exc.cleanup_error}. Do not retry generation; use "
            "recover_publication_transactions.py for the same project.",
            file=sys.stderr,
        )
        return FinalizationResult(
            status="published",
            attempt=attempt,
            cleanup_pending=True,
        )


__all__ = [
    "ATTEMPT_ROOT_NAME",
    "Attempt",
    "FinalizationResult",
    "MODE_SPECS",
    "finalize_with_cleanup_status",
    "initialize_attempt",
    "mode_spec",
]
