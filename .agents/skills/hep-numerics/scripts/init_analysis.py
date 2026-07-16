#!/usr/bin/env python3
"""Generate an initial hep-numerics scan-config draft for a project."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import socket
import stat
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from _strict_json import load_json as strict_load_json
from _identity import (
    validate_analysis_id,
    validate_canonical_identifier,
    validate_named_json_path,
)
from _dependency_graph import sha256_file
from _publication_transaction import (
    PublicationLock,
    PublicationTransaction,
    TransactionCommittedCleanupError,
    atomic_rename_no_replace,
    assert_no_active_transactions,
    capture_identity,
    publication_lock,
)


ANALYSIS_ID_PATTERN = re.compile(r"^analysis-([0-9]{3})$")
ATTEMPT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
DRAFT_DESCRIPTION_PREFIX = "Draft scan-config for "
RESERVATIONS_DIRNAME = ".reservations"
RESERVATION_FILENAME = "reservation.json"
RELEASE_MARKER_FILENAME = "release.json"
RELEASED_DIRNAME = ".released"
RESERVATION_KIND = "hep-numerics-analysis-init"
TEST_FAILURE_ENV = "HEP_WORKFLOW_TEST_FAIL_ANALYSIS_INIT_AFTER"
TEST_RELEASE_FAILURE_ENV = "HEP_WORKFLOW_TEST_FAIL_ANALYSIS_RELEASE_AFTER"


def load_run_scan_module() -> Any:
    """Load the sibling run_scan implementation so helpers stay aligned."""

    script_path = Path(__file__).resolve()
    target = script_path.parent / "run_scan.py"
    spec = importlib.util.spec_from_file_location("hep_numerics_init_run_scan_helpers", target)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load run_scan helpers from {target}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


RUN_SCAN = load_run_scan_module()
CUSTOM_OBSERVABLES = RUN_SCAN.CUSTOM_OBSERVABLES


def build_parser() -> argparse.ArgumentParser:
    """Construct the CLI argument parser."""

    parser = argparse.ArgumentParser(
        description=(
            "Generate a draft scan-config JSON and custom observable skeleton "
            "for a workspace project."
        )
    )
    parser.add_argument(
        "--project-dir",
        type=Path,
        required=True,
        help="Path to the workspace project directory.",
    )
    parser.add_argument(
        "--analysis-id",
        help="Optional explicit analysis identifier, for example analysis-001.",
    )
    parser.add_argument(
        "--resume-attempt",
        help=(
            "Attempt token for explicitly resuming a failed or interrupted "
            "analysis reservation. Requires --analysis-id."
        ),
    )
    parser.add_argument(
        "--reuse-draft",
        action="store_true",
        help=(
            "Explicitly open and update the newest unexecuted initializer draft. "
            "Without this flag, allocation always reserves a new analysis ID."
        ),
    )
    parser.add_argument(
        "--allow-formula-fallback",
        action="store_true",
        help=(
            "Explicitly opt a newly generated draft into usable formula-fallback "
            "task backends. The safe default remains false."
        ),
    )
    return parser


def load_json(path: Path) -> Any:
    """Load JSON from disk."""

    return strict_load_json(path)


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


def _require_real_directory(path: Path, label: str) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"{label} does not exist: {path}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise ValueError(f"{label} must be a real directory, not a symlink: {path}")


def _regular_file_exists(path: Path, label: str) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"{label} must be a regular file, not a symlink: {path}")
    return True


def _stage_bytes(path: Path, content: bytes) -> Path:
    fd, raw_path = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(raw_path)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        if temporary.exists():
            temporary.unlink()
        raise
    return temporary


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    temporary = _stage_bytes(path, content)
    try:
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        if temporary.exists():
            temporary.unlink()


def _atomic_create_bytes(path: Path, content: bytes) -> None:
    temporary = _stage_bytes(path, content)
    try:
        atomic_rename_no_replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        if temporary.exists():
            temporary.unlink()


def _encoded_json(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


@contextmanager
def _project_init_lock(project_dir: Path) -> Iterator[None]:
    """Serialize config/custom-observable publication for one project."""

    lock_path = project_dir / "numerics" / CUSTOM_OBSERVABLES.LOCK_FILENAME
    with CUSTOM_OBSERVABLES.custom_observables_lock(
        project_dir / "numerics" / "custom_observables.py"
    ):
        # Keep this assertion near the shared helper: both entry points must
        # resolve to the same persistent kernel-lock path.
        if lock_path.name != CUSTOM_OBSERVABLES.LOCK_FILENAME:
            raise RuntimeError("custom-observable lock path mismatch")
        yield


def sanitize_identifier(name: str) -> str:
    """Return an already-canonical observable name without silent rewriting."""

    return validate_canonical_identifier(name, "observable")


def formula_fallback_entry_for_task(project_dir: Path, task_id: str) -> dict[str, Any] | None:
    """Return fallback provenance metadata for a task, if it uses formula fallback."""

    result_meta_path = project_dir / "calculations" / task_id / "result-meta.json"
    try:
        result_meta = load_json(result_meta_path)
    except Exception:
        return None
    if not isinstance(result_meta, dict):
        return None
    provenance = result_meta.get("calculation_provenance")
    if provenance not in RUN_SCAN.FORMULA_FALLBACK_PROVENANCES:
        return None
    return {
        "task_id": task_id,
        "observable": result_meta.get("observable"),
        "calculation_provenance": provenance,
        "benchmark_used_as_input": result_meta.get("benchmark_used_as_input"),
    }


def choose_scale(parameter: dict[str, Any]) -> str:
    """Infer a plotting/scan scale from the suggested range."""

    suggested_range = parameter.get("suggested_range")
    if not isinstance(suggested_range, list) or len(suggested_range) != 2:
        return "linear"
    start, stop = suggested_range
    if (
        isinstance(start, (int, float))
        and isinstance(stop, (int, float))
        and start > 0
        and stop > 0
        and stop / start >= 100
    ):
        return "log"
    return "linear"


def default_parameter_value(parameter: dict[str, Any]) -> float:
    """Choose a default numeric value for a non-scanned parameter."""

    if "value" in parameter and isinstance(parameter["value"], (int, float)):
        return float(parameter["value"])

    suggested_range = parameter.get("suggested_range")
    if not isinstance(suggested_range, list) or len(suggested_range) != 2:
        return 0.0

    start, stop = suggested_range
    if not isinstance(start, (int, float)) or not isinstance(stop, (int, float)):
        return 0.0
    if start <= 0 <= stop:
        return 0.0
    if start > 0 and stop > 0 and stop / start >= 100:
        return float((start * stop) ** 0.5)
    return float((start + stop) / 2.0)


def _reservation_dir(scan_configs_dir: Path, analysis_id: str) -> Path:
    return scan_configs_dir / RESERVATIONS_DIRNAME / analysis_id


def _reservation_metadata_path(scan_configs_dir: Path, analysis_id: str) -> Path:
    return _reservation_dir(scan_configs_dir, analysis_id) / RESERVATION_FILENAME


def _new_reservation_payload(analysis_id: str, attempt_id: str) -> dict[str, Any]:
    return {
        "version": 1,
        "kind": RESERVATION_KIND,
        "resource_id": analysis_id,
        "attempt_id": attempt_id,
        "owner": {
            "hostname": socket.gethostname(),
            "pid": os.getpid(),
        },
        "created_at": datetime.now(timezone.utc).isoformat(),
        "state": "reserved",
    }


def _claim_analysis_reservation(
    scan_configs_dir: Path,
    analysis_id: str,
    *,
    attempt_id: str,
) -> dict[str, Any]:
    """Atomically claim one analysis ID and persist ownership metadata."""

    reservations_root = scan_configs_dir / RESERVATIONS_DIRNAME
    _require_real_directory(scan_configs_dir, "scan-config directory")
    if reservations_root.exists() or reservations_root.is_symlink():
        _require_real_directory(reservations_root, "analysis reservation root")
    else:
        reservations_root.mkdir()
        _fsync_directory(scan_configs_dir)
    reservation_dir = _reservation_dir(scan_configs_dir, analysis_id)
    reservation_dir.mkdir(exist_ok=False)
    payload = _new_reservation_payload(analysis_id, attempt_id)
    # If metadata publication fails, deliberately leave the typed reservation
    # directory occupied. Missing/corrupt metadata never authorizes recycling.
    _atomic_create_bytes(
        reservation_dir / RESERVATION_FILENAME,
        _encoded_json(payload),
    )
    _fsync_directory(reservations_root)
    return payload


def _validate_reservation_payload(
    payload: Any,
    *,
    analysis_id: str,
    attempt_id: str,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("analysis reservation metadata must be an object")
    if payload.get("version") != 1 or payload.get("kind") != RESERVATION_KIND:
        raise ValueError("analysis reservation metadata has an unsupported type/version")
    if payload.get("resource_id") != analysis_id:
        raise ValueError("analysis reservation resource_id does not match requested ID")
    if payload.get("attempt_id") != attempt_id:
        raise PermissionError("analysis reservation attempt token does not match")
    if payload.get("state") not in {"reserved", "failed", "publishing"}:
        raise FileExistsError(
            f"analysis reservation for {analysis_id} is already {payload.get('state')!r}"
        )
    return payload


def _load_owned_reservation(
    scan_configs_dir: Path,
    analysis_id: str,
    attempt_id: str,
) -> dict[str, Any]:
    metadata_path = _reservation_metadata_path(scan_configs_dir, analysis_id)
    if not _regular_file_exists(metadata_path, "analysis reservation metadata"):
        raise FileNotFoundError(
            f"reservation metadata not found for {analysis_id}; cannot authenticate resume"
        )
    return _validate_reservation_payload(
        load_json(metadata_path),
        analysis_id=analysis_id,
        attempt_id=attempt_id,
    )


def _set_reservation_state(
    scan_configs_dir: Path,
    analysis_id: str,
    attempt_id: str,
    state: str,
    **details: Any,
) -> dict[str, Any]:
    metadata_path = _reservation_metadata_path(scan_configs_dir, analysis_id)
    if not _regular_file_exists(metadata_path, "analysis reservation metadata"):
        raise FileNotFoundError(metadata_path)
    payload = load_json(metadata_path)
    if not isinstance(payload, dict):
        raise ValueError("analysis reservation metadata must be an object")
    if payload.get("kind") != RESERVATION_KIND or payload.get("resource_id") != analysis_id:
        raise ValueError("analysis reservation identity mismatch")
    if payload.get("attempt_id") != attempt_id:
        raise PermissionError("analysis reservation attempt token does not match")
    current_state = payload.get("state")
    allowed_transitions = {
        "reserved": {"reserved", "publishing", "failed"},
        "failed": {"reserved", "failed"},
        "publishing": {"reserved", "published", "failed"},
        "published": {"published"},
    }
    if state not in allowed_transitions.get(str(current_state), set()):
        raise ValueError(
            f"unsafe analysis reservation transition {current_state!r} -> {state!r}"
        )
    payload["state"] = state
    payload.update(details)
    _atomic_write_bytes(metadata_path, _encoded_json(payload))
    return payload


def _release_published_reservation(
    scan_configs_dir: Path,
    analysis_id: str,
    attempt_id: str,
) -> None:
    """Idempotently archive a published attempt and release its draft claim."""

    reservations_root = scan_configs_dir / RESERVATIONS_DIRNAME
    _require_real_directory(reservations_root, "analysis reservation root")
    with CUSTOM_OBSERVABLES.custom_observables_lock(
        reservations_root / "release"
    ):
        _release_published_reservation_unlocked(
            scan_configs_dir,
            analysis_id,
            attempt_id,
        )


def _release_published_reservation_unlocked(
    scan_configs_dir: Path,
    analysis_id: str,
    attempt_id: str,
) -> None:
    reservation_dir = _reservation_dir(scan_configs_dir, analysis_id)
    metadata_path = reservation_dir / RESERVATION_FILENAME
    release_marker_path = reservation_dir / RELEASE_MARKER_FILENAME
    history_dir = scan_configs_dir / RESERVATIONS_DIRNAME / ".history"
    reservations_root = scan_configs_dir / RESERVATIONS_DIRNAME
    _require_real_directory(reservations_root, "analysis reservation root")
    if reservation_dir.exists() or reservation_dir.is_symlink():
        _require_real_directory(reservation_dir, "analysis reservation directory")
    if history_dir.exists() or history_dir.is_symlink():
        _require_real_directory(history_dir, "analysis reservation history")
    else:
        history_dir.mkdir()
        _fsync_directory(reservations_root)
    history_path = history_dir / f"{analysis_id}-{attempt_id}.json"

    payload: dict[str, Any] | None = None
    if _regular_file_exists(metadata_path, "analysis reservation metadata"):
        loaded = load_json(metadata_path)
        if not isinstance(loaded, dict):
            raise ValueError("analysis reservation metadata must be an object")
        payload = loaded
        if payload.get("attempt_id") != attempt_id or payload.get("state") != "published":
            raise PermissionError(
                "only the published owning attempt may release a reservation"
            )
        if _regular_file_exists(history_path, "analysis reservation history entry"):
            if load_json(history_path) != payload:
                raise ValueError(
                    f"reservation history conflicts with live metadata: {history_path}"
                )
        else:
            _atomic_create_bytes(history_path, _encoded_json(payload))
        if os.environ.get(TEST_RELEASE_FAILURE_ENV) == "history":
            raise OSError("injected analysis reservation release failure after history")
        if release_marker_path.exists() or release_marker_path.is_symlink():
            raise FileExistsError(
                f"reservation release marker already exists: {release_marker_path}"
            )
        atomic_rename_no_replace(metadata_path, release_marker_path)
        _fsync_directory(reservation_dir)
        if os.environ.get(TEST_RELEASE_FAILURE_ENV) == "metadata":
            raise OSError("injected analysis reservation release failure after metadata")
    elif _regular_file_exists(release_marker_path, "analysis release marker"):
        marker = load_json(release_marker_path)
        if (
            not isinstance(marker, dict)
            or marker.get("attempt_id") != attempt_id
            or marker.get("state") != "published"
        ):
            raise ValueError(
                f"invalid reservation release marker: {release_marker_path}"
            )
        if (
            not _regular_file_exists(
                history_path,
                "analysis reservation history entry",
            )
            or load_json(history_path) != marker
        ):
            raise ValueError(
                f"release marker is not backed by matching history: {history_path}"
            )
    elif not _regular_file_exists(
        history_path,
        "analysis reservation history entry",
    ):
        raise FileNotFoundError(
            f"neither live nor archived reservation metadata exists for {analysis_id}"
        )

    released_root = scan_configs_dir / RESERVATIONS_DIRNAME / RELEASED_DIRNAME
    if released_root.exists() or released_root.is_symlink():
        _require_real_directory(released_root, "released reservation root")
    else:
        released_root.mkdir()
        _fsync_directory(reservations_root)
    released_dir = released_root / f"{analysis_id}-{attempt_id}"
    if reservation_dir.exists() or reservation_dir.is_symlink():
        _require_real_directory(reservation_dir, "analysis reservation directory")
        if released_dir.exists() or released_dir.is_symlink():
            raise FileExistsError(
                f"released reservation quarantine already exists: {released_dir}"
            )
        atomic_rename_no_replace(reservation_dir, released_dir)
        _fsync_directory(reservation_dir.parent)
        _fsync_directory(released_root)
    elif not (released_dir.exists() or released_dir.is_symlink()):
        # A prior invocation already completed cleanup. Matching immutable
        # history is sufficient to make this idempotent.
        return
    if os.environ.get(TEST_RELEASE_FAILURE_ENV) == "directory":
        raise OSError("injected analysis reservation release failure after directory move")

    released_marker = released_dir / RELEASE_MARKER_FILENAME
    _require_real_directory(released_dir, "released reservation quarantine")
    if _regular_file_exists(released_marker, "released reservation marker"):
        marker = load_json(released_marker)
        if marker != load_json(history_path):
            raise ValueError(
                f"released reservation marker conflicts with history: {released_dir}"
            )
    released_lock = released_dir / CUSTOM_OBSERVABLES.LOCK_FILENAME
    if _regular_file_exists(released_lock, "released reservation lock"):
        released_lock.unlink()
    released_marker.unlink(missing_ok=True)
    released_dir.rmdir()
    _fsync_directory(released_root)
    _fsync_directory(history_dir)
    _fsync_directory(reservation_dir.parent)


def _recover_published_reservations(scan_configs_dir: Path) -> None:
    """Finish only releases backed by unambiguous published metadata/history."""

    reservations_root = scan_configs_dir / RESERVATIONS_DIRNAME
    if not (reservations_root.exists() or reservations_root.is_symlink()):
        return
    _require_real_directory(reservations_root, "analysis reservation root")
    with CUSTOM_OBSERVABLES.custom_observables_lock(
        reservations_root / "recovery"
    ):
        released_root = reservations_root / RELEASED_DIRNAME
        if released_root.exists() or released_root.is_symlink():
            _require_real_directory(released_root, "released reservation root")
            for released_dir in sorted(released_root.iterdir()):
                _require_real_directory(
                    released_dir,
                    "released reservation quarantine entry",
                )
                marker_path = released_dir / RELEASE_MARKER_FILENAME
                if _regular_file_exists(marker_path, "released reservation marker"):
                    marker = load_json(marker_path)
                    if not isinstance(marker, dict):
                        raise ValueError(f"invalid release marker: {marker_path}")
                    analysis_id = validate_analysis_id(str(marker.get("resource_id")))
                    attempt_id = marker.get("attempt_id")
                    if not isinstance(attempt_id, str):
                        raise ValueError(f"invalid release attempt: {marker_path}")
                    history_path = (
                        reservations_root
                        / ".history"
                        / f"{analysis_id}-{attempt_id}.json"
                    )
                    if (
                        not _regular_file_exists(
                            history_path,
                            "analysis reservation history entry",
                        )
                        or load_json(history_path) != marker
                    ):
                        raise ValueError(
                            f"released reservation lacks matching history: {released_dir}"
                        )
                released_lock = released_dir / CUSTOM_OBSERVABLES.LOCK_FILENAME
                if _regular_file_exists(released_lock, "released reservation lock"):
                    released_lock.unlink()
                marker_path.unlink(missing_ok=True)
                released_dir.rmdir()
            _fsync_directory(released_root)
        for reservation_dir in sorted(reservations_root.glob("analysis-*")):
            _require_real_directory(
                reservation_dir,
                "analysis reservation directory",
            )
            analysis_id = validate_analysis_id(reservation_dir.name)
            metadata_path = reservation_dir / RESERVATION_FILENAME
            release_marker_path = reservation_dir / RELEASE_MARKER_FILENAME
            if _regular_file_exists(metadata_path, "analysis reservation metadata"):
                payload = load_json(metadata_path)
                if not isinstance(payload, dict) or payload.get("state") != "published":
                    continue
                attempt_id = payload.get("attempt_id")
                if not isinstance(attempt_id, str):
                    raise ValueError(
                        f"published reservation lacks attempt_id: {metadata_path}"
                    )
            elif _regular_file_exists(release_marker_path, "analysis release marker"):
                marker = load_json(release_marker_path)
                if (
                    not isinstance(marker, dict)
                    or marker.get("resource_id") != analysis_id
                    or marker.get("state") != "published"
                    or not isinstance(marker.get("attempt_id"), str)
                ):
                    raise ValueError(
                        f"invalid reservation release marker: {release_marker_path}"
                    )
                attempt_id = marker["attempt_id"]
            else:
                # A crash immediately after atomic mkdir deliberately leaves
                # an opaque, occupied reservation. History from an older
                # attempt never authenticates or reclaims this new live claim.
                continue
            _release_published_reservation_unlocked(
                scan_configs_dir,
                analysis_id,
                attempt_id,
            )


@contextmanager
def _analysis_attempt_lock(
    scan_configs_dir: Path,
    analysis_id: str,
) -> Iterator[None]:
    reservation_dir = _reservation_dir(scan_configs_dir, analysis_id)
    _require_real_directory(reservation_dir, "analysis reservation directory")
    with CUSTOM_OBSERVABLES.custom_observables_lock(reservation_dir / "attempt"):
        yield


def _analysis_id_is_occupied(project_dir: Path, analysis_id: str) -> bool:
    scan_configs_dir = project_dir / "numerics" / "scan-configs"
    occupied = any(
        os.path.lexists(path)
        for path in (
            scan_configs_dir / f"{analysis_id}.json",
            _reservation_dir(scan_configs_dir, analysis_id),
            project_dir / "numerics" / "scan-results" / analysis_id,
            project_dir / "numerics" / "figures" / analysis_id,
            project_dir / "numerics" / f"analysis-summary-{analysis_id}.md",
        )
    )
    history_root = scan_configs_dir / RESERVATIONS_DIRNAME / ".history"
    if history_root.exists() or history_root.is_symlink():
        _require_real_directory(history_root, "analysis reservation history")
        occupied = occupied or any(
            os.path.lexists(path)
            for path in history_root.glob(f"{analysis_id}-*.json")
        )
    return occupied or _manifest_owns_analysis(project_dir, analysis_id)


def _manifest_owns_analysis(project_dir: Path, analysis_id: str) -> bool:
    manifest_path = project_dir / "manifest.json"
    manifest = load_json(manifest_path)
    analyses = (
        manifest.get("artifacts", {})
        .get("numerics", {})
        .get("analyses", [])
    )
    if not isinstance(analyses, list):
        raise ValueError("manifest numerics analyses must be an array")
    return any(
        entry == analysis_id
        or (isinstance(entry, dict) and entry.get("analysis_id") == analysis_id)
        for entry in analyses
    )


def iter_analysis_config_paths(scan_configs_dir: Path) -> list[Path]:
    """Return all analysis-NNN scan-config paths sorted by descending numeric suffix."""

    candidates: list[tuple[int, Path]] = []
    if not scan_configs_dir.exists():
        return []
    for path in scan_configs_dir.glob("analysis-*.json"):
        match = ANALYSIS_ID_PATTERN.fullmatch(path.stem)
        if match:
            candidates.append((int(match.group(1)), path))
    return [path for _, path in sorted(candidates, key=lambda item: item[0], reverse=True)]


def has_execution_outputs(project_dir: Path, analysis_id: str) -> bool:
    """Return whether an analysis appears to have been executed already."""

    scan_results_dir = project_dir / "numerics" / "scan-results" / analysis_id
    if scan_results_dir.exists():
        return True
    figures_dir = project_dir / "numerics" / "figures" / analysis_id
    if figures_dir.exists():
        return True
    return False


def is_reusable_draft_scan_config(project_dir: Path, path: Path) -> bool:
    """Return whether a scan-config is an auto-generated draft that was never executed."""

    try:
        scan_config = load_json(path)
    except Exception:
        return False

    analysis_id = scan_config.get("analysis_id")
    description = scan_config.get("description")
    if not isinstance(analysis_id, str) or analysis_id != path.stem:
        return False
    if not isinstance(description, str) or not description.startswith(DRAFT_DESCRIPTION_PREFIX):
        return False
    return not has_execution_outputs(project_dir, analysis_id)


def custom_observable_canonical_unit(constraint: dict[str, Any]) -> str:
    observable = constraint.get("observable")
    candidates: list[str] = []
    declared = constraint.get("unit")
    if isinstance(declared, str) and declared.strip():
        candidates.append(declared)
    interpolation = constraint.get("interpolation")
    if (
        isinstance(interpolation, dict)
        and interpolation.get("y_quantity") == observable
        and isinstance(interpolation.get("y_unit"), str)
        and interpolation["y_unit"].strip()
    ):
        candidates.append(interpolation["y_unit"])
    unique = sorted(set(candidates))
    if len(unique) != 1:
        raise ValueError(
            f"custom observable {observable!r} requires exactly one authoritative "
            "canonical unit from constraint.unit or interpolation.y_unit"
        )
    return unique[0]


def resolve_target_analysis(
    project_dir: Path,
    *,
    requested_analysis_id: str | None,
    resume_attempt_id: str | None = None,
    reuse_existing_draft: bool = False,
) -> tuple[str, Path, bool, dict[str, Any]]:
    """Serialize recovery plus select-and-reserve, then return owned state."""

    with publication_lock(
        project_dir,
        "analysis-allocation",
        blocking=True,
    ):
        assert_no_active_transactions(project_dir)
        return _resolve_target_analysis_locked(
            project_dir,
            requested_analysis_id=requested_analysis_id,
            resume_attempt_id=resume_attempt_id,
            reuse_existing_draft=reuse_existing_draft,
        )


def _resolve_target_analysis_locked(
    project_dir: Path,
    *,
    requested_analysis_id: str | None,
    resume_attempt_id: str | None = None,
    reuse_existing_draft: bool = False,
) -> tuple[str, Path, bool, dict[str, Any]]:
    """Atomically reserve an analysis ID or authenticate an interrupted attempt."""

    scan_configs_dir = project_dir / "numerics" / "scan-configs"
    scan_configs_dir.mkdir(parents=True, exist_ok=True)
    _recover_published_reservations(scan_configs_dir)
    if resume_attempt_id is not None:
        if requested_analysis_id is None:
            raise ValueError("--resume-attempt requires --analysis-id")
        if ATTEMPT_ID_PATTERN.fullmatch(resume_attempt_id) is None:
            raise ValueError("invalid resume attempt token")
        analysis_id = validate_analysis_id(requested_analysis_id)
        target_path = validate_named_json_path(
            scan_configs_dir / f"{analysis_id}.json",
            scan_configs_dir,
            analysis_id,
            "scan-config",
        )
        reservation = _load_owned_reservation(
            scan_configs_dir,
            analysis_id,
            resume_attempt_id,
        )
        if has_execution_outputs(project_dir, analysis_id):
            raise FileExistsError(
                f"cannot resume initialization after execution outputs exist for {analysis_id}"
            )
        if target_path.exists() and not is_reusable_draft_scan_config(project_dir, target_path):
            raise FileExistsError(
                f"existing scan-config is not a reusable initializer draft: {target_path}"
            )
        return analysis_id, target_path, target_path.exists(), reservation

    if requested_analysis_id is not None:
        analysis_id = validate_analysis_id(requested_analysis_id)
        target_path = validate_named_json_path(
            scan_configs_dir / f"{analysis_id}.json",
            scan_configs_dir,
            analysis_id,
            "scan-config",
        )
        if _analysis_id_is_occupied(project_dir, analysis_id):
            raise FileExistsError(
                f"analysis ID is already occupied; use its attempt token to resume: {analysis_id}"
            )
        attempt_id = uuid.uuid4().hex
        reservation = _claim_analysis_reservation(
            scan_configs_dir,
            analysis_id,
            attempt_id=attempt_id,
        )
        return analysis_id, target_path, False, reservation

    attempt_id = uuid.uuid4().hex
    if reuse_existing_draft:
        for reusable_path in iter_analysis_config_paths(scan_configs_dir):
            if not is_reusable_draft_scan_config(project_dir, reusable_path):
                continue
            analysis_id = validate_analysis_id(reusable_path.stem)
            try:
                reservation = _claim_analysis_reservation(
                    scan_configs_dir,
                    analysis_id,
                    attempt_id=attempt_id,
                )
            except FileExistsError:
                # Another initializer owns this reusable draft; never alias it.
                continue
            return analysis_id, reusable_path, True, reservation
        raise FileNotFoundError("no reusable unexecuted initializer draft exists")

    for number in range(1, 1000):
        analysis_id = validate_analysis_id(f"analysis-{number:03d}")
        if _analysis_id_is_occupied(project_dir, analysis_id):
            continue
        target_path = validate_named_json_path(
            scan_configs_dir / f"{analysis_id}.json",
            scan_configs_dir,
            analysis_id,
            "scan-config",
        )
        try:
            reservation = _claim_analysis_reservation(
                scan_configs_dir,
                analysis_id,
                attempt_id=attempt_id,
            )
        except FileExistsError:
            continue
        return analysis_id, target_path, False, reservation

    raise RuntimeError(
        "no free analysis identifier remains in the supported "
        "analysis-001..analysis-999 range"
    )


def build_draft_config(
    project_dir: Path,
    analysis_id: str,
    *,
    allow_formula_fallback: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build the scan-config draft and any custom-observable side effects."""

    manifest = load_json(project_dir / "manifest.json")
    model_spec = load_json(project_dir / "model" / "model-spec.json")
    constraints_data = load_json(project_dir / "constraints" / "constraints-data.json")
    actual_model_checksum = sha256_file(project_dir / "model" / "model-spec.json")
    declared_model_checksum = manifest.get("artifacts", {}).get("model", {}).get("checksum")
    if declared_model_checksum != actual_model_checksum:
        raise ValueError(
            "manifest model checksum does not match the exact bytes of model/model-spec.json"
        )

    model_parameters = model_spec.get("parameters", [])
    scan_candidates = [parameter for parameter in model_parameters if parameter.get("role") == "scan"]
    if not scan_candidates:
        raise ValueError("model-spec.json does not define any role='scan' parameters")

    selected_scan_parameters = scan_candidates[:2]
    selected_scan_names = [parameter["name"] for parameter in selected_scan_parameters]

    scan_parameters = []
    for parameter in selected_scan_parameters:
        suggested_range = parameter.get("suggested_range")
        if not isinstance(suggested_range, list) or len(suggested_range) != 2:
            raise ValueError(
                f"scan parameter {parameter['name']!r} is missing a usable suggested_range"
            )
        scan_parameters.append(
            {
                "canonical_name": parameter["name"],
                "range": [float(suggested_range[0]), float(suggested_range[1])],
                "grid": 60,
                "scale": choose_scale(parameter),
            }
        )

    fixed_parameters = []
    for parameter in model_parameters:
        name = parameter["name"]
        if name in selected_scan_names:
            continue
        if parameter.get("role") == "derived":
            continue
        fixed_parameters.append(
            {
                "canonical_name": name,
                "value": default_parameter_value(parameter),
            }
        )

    selected_constraints = [
        constraint
        for constraint in constraints_data.get("constraints", [])
        if constraint.get("implementation_status") in {"direct", "interpolated"}
    ]

    observables: list[dict[str, Any]] = []
    depends_on_tasks: set[str] = set()
    custom_observable_specs: list[dict[str, Any]] = []
    formula_parse_failures: list[str] = []

    for constraint in selected_constraints:
        computed_by = constraint.get("computed_by", {})
        observable = constraint.get("observable")
        if computed_by.get("type") == "task":
            task_id = computed_by["task_id"]
            depends_on_tasks.add(task_id)
            if observable not in {entry["observable"] for entry in observables}:
                observables.append(
                    {
                        "observable": observable,
                        "source": {
                            "type": "task",
                            "task_id": task_id,
                        },
                    }
                )
        elif computed_by.get("type") == "derived":
            function_name = sanitize_identifier(observable)
            custom_observable_specs.append(
                {
                    "constraint": constraint,
                    "function_name": function_name,
                    "needs_task_outputs": True,
                }
            )
            depends_on_tasks.update(computed_by.get("depends_on_tasks", []))
            observables.append(
                {
                    "observable": observable,
                    "source": {
                        "type": "custom",
                        "function": function_name,
                        "canonical_unit": custom_observable_canonical_unit(constraint),
                        "task_ids": sorted(computed_by.get("depends_on_tasks", [])),
                        "note": computed_by.get("derivation_note", ""),
                    },
                }
            )
        elif computed_by.get("type") == "parameter_combination":
            try:
                RUN_SCAN.compile_parameter_combination(computed_by["formula"])
            except Exception:
                function_name = sanitize_identifier(observable)
                formula_parse_failures.append(constraint["id"])
                custom_observable_specs.append(
                    {
                        "constraint": constraint,
                        "function_name": function_name,
                        "needs_task_outputs": False,
                    }
                )
                observables.append(
                    {
                        "observable": observable,
                        "source": {
                            "type": "custom",
                            "function": function_name,
                            "canonical_unit": custom_observable_canonical_unit(constraint),
                            "note": "Generated fallback because the formula needs manual implementation.",
                        },
                    }
                )

    figures: list[dict[str, Any]] = []
    if len(scan_parameters) >= 2 and selected_constraints:
        figures.append(
            {
                "kind": "exclusion_2d",
                "x": scan_parameters[0]["canonical_name"],
                "y": scan_parameters[1]["canonical_name"],
                "constraints": [constraint["id"] for constraint in selected_constraints],
                "show_allowed_region": True,
                "title": f"{model_spec['model_name']} exclusion overview",
            }
        )
    if scan_parameters:
        x_name = scan_parameters[0]["canonical_name"]
        for observable in observables:
            figures.append(
                {
                    "kind": "scan_1d",
                    "x": x_name,
                    "observables": [observable["observable"]],
                    "fixed": {
                        parameter["canonical_name"]: parameter["range"][0]
                        for parameter in scan_parameters
                        if parameter["canonical_name"] != x_name
                    },
                    "overlay_constraint_bands": True,
                    "title": f"{observable['observable']} vs {x_name}",
                }
            )

    formula_fallback_tasks = [
        entry
        for task_id in sorted(depends_on_tasks)
        for entry in [formula_fallback_entry_for_task(project_dir, task_id)]
        if entry is not None
    ]

    scan_config = {
        "analysis_id": analysis_id,
        "model_name": model_spec["model_name"],
        "description": f"Draft scan-config for {model_spec['model_name']}",
        "depends_on": {
            "model_version": manifest.get("active_model_version"),
            "model_checksum": manifest.get("artifacts", {}).get("model", {}).get("checksum"),
            "task_ids": sorted(depends_on_tasks),
        },
        "scan_parameters": scan_parameters,
        "fixed_parameters": fixed_parameters,
        "observables": observables,
        "constraints_used": [constraint["id"] for constraint in selected_constraints],
        "figures": figures,
        "allow_formula_fallback": bool(
            allow_formula_fallback and formula_fallback_tasks
        ),
        "seed": 0,
        "parallelism": 1,
    }

    return scan_config, {
        "custom_observable_specs": custom_observable_specs,
        "formula_parse_failures": formula_parse_failures,
        "formula_fallback_tasks": formula_fallback_tasks,
        "model_parameter_names": [parameter["name"] for parameter in model_parameters],
    }


def validate_draft_config(scan_config: dict[str, Any], repo_root: Path) -> None:
    """Validate the staged draft's schema and emitted namespace before publication."""

    from jsonschema import Draft202012Validator

    schema = load_json(repo_root / "schemas" / "scan-config.schema.json")
    errors = sorted(
        Draft202012Validator(schema).iter_errors(scan_config),
        key=lambda issue: list(issue.absolute_path),
    )
    if errors:
        rendered = "; ".join(
            f"{'.'.join(str(part) for part in issue.absolute_path) or '<root>'}: {issue.message}"
            for issue in errors
        )
        raise ValueError(f"generated scan-config draft failed schema validation: {rendered}")
    namespace_issues = RUN_SCAN.validate_scan_config_namespace(scan_config)
    if namespace_issues:
        raise ValueError(
            "generated scan-config draft has an invalid output namespace: "
            + "; ".join(namespace_issues)
        )


def _publish_initialized_analysis(
    project_dir: Path,
    *,
    analysis_id: str,
    attempt_id: str,
    target_path: Path,
    reusing_existing_draft: bool,
    scan_config: dict[str, Any],
    metadata: dict[str, Any],
    publication: PublicationLock,
) -> tuple[Path, bool, list[str]]:
    """Publish config/custom-observable changes as one rollback-capable unit."""

    scan_configs_dir = project_dir / "numerics" / "scan-configs"
    custom_path = project_dir / "numerics" / "custom_observables.py"
    custom_path.parent.mkdir(parents=True, exist_ok=True)

    with _project_init_lock(project_dir):
        if target_path.exists():
            if not reusing_existing_draft:
                raise FileExistsError(
                    f"scan-config appeared after reservation for {analysis_id}: {target_path}"
                )
            if not is_reusable_draft_scan_config(project_dir, target_path):
                raise FileExistsError(
                    f"existing scan-config is not a reusable initializer draft: {target_path}"
                )

        _set_reservation_state(
            scan_configs_dir,
            analysis_id,
            attempt_id,
            "publishing",
            publishing_at=datetime.now(timezone.utc).isoformat(),
        )

        original_custom = custom_path.read_bytes() if custom_path.exists() else None
        reservation_path = _reservation_metadata_path(scan_configs_dir, analysis_id)
        published_reservation = load_json(reservation_path)
        if not isinstance(published_reservation, dict):
            raise ValueError("analysis reservation metadata must be an object")
        if published_reservation.get("attempt_id") != attempt_id:
            raise PermissionError("analysis reservation attempt token does not match")
        published_reservation["state"] = "published"
        published_reservation["published_at"] = datetime.now(timezone.utc).isoformat()

        appended_functions: list[str] = []
        custom_required = bool(metadata["custom_observable_specs"])
        created_custom_header = original_custom is None and custom_required
        with PublicationTransaction.begin(
            project_dir,
            f"analysis-init-{analysis_id}",
            lock=publication,
        ) as transaction:
            if original_custom is not None or custom_required:
                staged_custom = transaction.stage_path(
                    "numerics/custom_observables.py"
                )
                if original_custom is None:
                    staged_custom.write_text(
                        CUSTOM_OBSERVABLES.render_custom_observables_template(
                            project_dir.name
                        ),
                        encoding="utf-8",
                    )
                else:
                    staged_custom.write_bytes(original_custom)
                for spec in metadata["custom_observable_specs"]:
                    appended = CUSTOM_OBSERVABLES.append_custom_observable_stub(
                        staged_custom,
                        function_name=spec["function_name"],
                        parameter_names=metadata["model_parameter_names"],
                        constraint=spec["constraint"],
                        needs_task_outputs=spec["needs_task_outputs"],
                        acquire_lock=False,
                    )
                    if appended:
                        appended_functions.append(spec["function_name"])
                custom_candidate = staged_custom.read_bytes()

                if original_custom != custom_candidate:
                    transaction.add(
                        staged_custom,
                        custom_path,
                        mode="replace",
                        expected_before=capture_identity(custom_path),
                    )

            staged_config = transaction.stage_path(
                f"numerics/scan-configs/{analysis_id}.json"
            )
            staged_config.write_bytes(_encoded_json(scan_config))
            transaction.add(
                staged_config,
                target_path,
                mode="replace",
                expected_before=capture_identity(target_path),
            )

            staged_reservation = transaction.stage_path(
                f"numerics/reservations/{analysis_id}.json"
            )
            staged_reservation.write_bytes(_encoded_json(published_reservation))
            transaction.add(
                staged_reservation,
                reservation_path,
                mode="replace",
                expected_before=capture_identity(reservation_path),
            )

            failure_target = os.environ.get(TEST_FAILURE_ENV)

            def after_publish(destination: Path, index: int) -> None:
                if failure_target in {str(index), destination.name}:
                    raise OSError(
                        "injected analysis initializer failure after "
                        f"{destination.name}"
                    )

            transaction.commit(after_publish_entry=after_publish)
        return custom_path, created_custom_header, appended_functions


def main() -> int:
    """CLI entrypoint."""

    parser = build_parser()
    args = parser.parse_args()

    reservation: dict[str, Any] | None = None
    project_dir: Path | None = None
    scan_configs_dir: Path | None = None
    analysis_id: str | None = None
    attempt_id: str | None = None
    published = False
    failure_recorded = False
    try:
        project_dir = args.project_dir.resolve()
        if not (project_dir / "manifest.json").exists():
            raise FileNotFoundError(f"manifest.json not found under {project_dir}")

        if args.analysis_id is not None:
            validate_analysis_id(args.analysis_id)
        if args.resume_attempt is not None and args.analysis_id is None:
            raise ValueError("--resume-attempt requires --analysis-id")
        if args.reuse_draft and (
            args.analysis_id is not None or args.resume_attempt is not None
        ):
            raise ValueError(
                "--reuse-draft cannot be combined with --analysis-id or --resume-attempt"
            )
        scan_configs_dir = project_dir / "numerics" / "scan-configs"
        analysis_id, target_path, reusing_existing_draft, reservation = resolve_target_analysis(
            project_dir,
            requested_analysis_id=args.analysis_id,
            resume_attempt_id=args.resume_attempt,
            reuse_existing_draft=args.reuse_draft,
        )
        attempt_id = str(reservation["attempt_id"])
        print(f"Reserved {analysis_id}; attempt_id={attempt_id}")
        reservation_gate = os.environ.get("HEP_WORKFLOW_TEST_ANALYSIS_RESERVATION_GATE")
        if reservation_gate:
            deadline = time.monotonic() + 30.0
            gate_path = Path(reservation_gate)
            while not gate_path.exists():
                if time.monotonic() >= deadline:
                    raise TimeoutError("timed out waiting for analysis reservation test gate")
                time.sleep(0.01)

        with _analysis_attempt_lock(scan_configs_dir, analysis_id):
            try:
                _load_owned_reservation(scan_configs_dir, analysis_id, attempt_id)
                if args.resume_attempt is not None:
                    _set_reservation_state(
                        scan_configs_dir,
                        analysis_id,
                        attempt_id,
                        "reserved",
                        resumed_at=datetime.now(timezone.utc).isoformat(),
                        owner={"hostname": socket.gethostname(), "pid": os.getpid()},
                    )
                with publication_lock(
                    project_dir,
                    "numerics",
                    blocking=True,
                ) as publication:
                    scan_config, metadata = build_draft_config(
                        project_dir,
                        analysis_id,
                        allow_formula_fallback=args.allow_formula_fallback,
                    )
                    validate_draft_config(scan_config, RUN_SCAN.resolve_repo_root())
                    custom_path, created_custom_header, appended_functions = (
                        _publish_initialized_analysis(
                            project_dir,
                            analysis_id=analysis_id,
                            attempt_id=attempt_id,
                            target_path=target_path,
                            reusing_existing_draft=reusing_existing_draft,
                            scan_config=scan_config,
                            metadata=metadata,
                            publication=publication,
                        )
                    )
                published = True
            except TransactionCommittedCleanupError as exc:
                # All authoritative candidates are already durable. Preserve
                # their published ownership and do not invite a duplicate retry.
                published = True
                try:
                    _release_published_reservation(
                        scan_configs_dir,
                        analysis_id,
                        attempt_id,
                    )
                except Exception as release_exc:
                    print(
                        "warning: could not release published reservation: "
                        f"{release_exc}",
                        file=sys.stderr,
                    )
                print(
                    "warning: analysis initialization committed successfully, but "
                    f"private cleanup is pending for transaction {exc.transaction_id}: "
                    f"{exc.cleanup_error}. Do not retry this command; use "
                    "recover_publication_transactions.py for the same publication anchor.",
                    file=sys.stderr,
                )
                return 0
            except Exception as inner_exc:
                try:
                    _set_reservation_state(
                        scan_configs_dir,
                        analysis_id,
                        attempt_id,
                        "failed",
                        failed_at=datetime.now(timezone.utc).isoformat(),
                        failure_type=type(inner_exc).__name__,
                    )
                    failure_recorded = True
                except Exception:
                    pass
                raise

        try:
            _release_published_reservation(
                scan_configs_dir,
                analysis_id,
                attempt_id,
            )
        except Exception as exc:
            # A published reservation is safe to leave occupied. It blocks
            # implicit reuse but does not invalidate already-published files.
            print(f"warning: could not release published reservation: {exc}", file=sys.stderr)

        if reusing_existing_draft:
            print(f"Reused existing unexecuted draft scan-config: {target_path}")
        else:
            print(f"Wrote draft scan-config: {target_path}")
        if created_custom_header:
            print(f"Created custom observable skeleton: {custom_path}")
        if appended_functions:
            print(
                "Appended custom observable stubs: "
                + ", ".join(appended_functions)
            )
        if metadata["formula_parse_failures"]:
            print(
                "Parameter-combination constraints needing manual implementation: "
                + ", ".join(metadata["formula_parse_failures"])
            )
        if metadata["formula_fallback_tasks"]:
            tasks = ", ".join(
                f"{entry['task_id']} ({entry['calculation_provenance']})"
                for entry in metadata["formula_fallback_tasks"]
            )
            state = "true (explicit CLI opt-in)" if args.allow_formula_fallback else "false"
            print(
                "Formula fallback backends detected; draft keeps "
                f"allow_formula_fallback={state}: {tasks}"
            )

        print("")
        print("Next steps:")
        print(f"1. Review and edit {target_path.name} to confirm scan ranges, fixed values, and figures.")
        if custom_path.exists():
            print(
                f"2. Fill in {custom_path.name} for any generated custom observables before running numerics."
            )
        else:
            print("2. No custom-observable module is required by this draft.")
        print(
            "3. Once the required calculation tasks have complete result-meta/result-python outputs, run:"
        )
        print(
            f"   python3 .agents/skills/hep-numerics/scripts/validate_scan_config.py "
            f"--project-dir {project_dir} --analysis-id {analysis_id}"
        )
        print(
            f"   python3 .agents/skills/hep-numerics/scripts/run_scan.py "
            f"--project-dir {project_dir} --analysis-id {analysis_id}"
        )
        return 0
    except Exception as exc:
        if (
            not published
            and not failure_recorded
            and scan_configs_dir is not None
            and analysis_id is not None
            and attempt_id is not None
        ):
            try:
                _set_reservation_state(
                    scan_configs_dir,
                    analysis_id,
                    attempt_id,
                    "failed",
                    failed_at=datetime.now(timezone.utc).isoformat(),
                    failure_type=type(exc).__name__,
                )
            except Exception:
                # A malformed/incomplete reservation remains occupied and must
                # never be reclaimed merely because failure metadata is absent.
                pass
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
