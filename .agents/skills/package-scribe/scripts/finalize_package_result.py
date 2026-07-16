#!/usr/bin/env python3
"""Validate and atomically publish one owned Package-Scribe batch attempt."""

from __future__ import annotations

import argparse
import ast
from copy import deepcopy
from dataclasses import asdict, dataclass
import json
import os
import re
import shutil
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from _publication_transaction import (
    PathIdentity,
    PublicationTransaction,
    PublicationTransactionError,
    TransactionCommittedCleanupError,
    capture_identity,
    publication_lock,
)


RESERVATION_FILENAME = ".reservation.json"
BATCH_ATTEMPT_ROOT = ".hep-workflow-package-attempts"
ATTEMPT_ID_PATTERN = re.compile(r"^[a-f0-9]{32}$")
TASK_ID_PATTERN = re.compile(r"^task-[0-9]{3}$")
PLACEHOLDER_PATTERN = re.compile(r"\{\{\s*[A-Za-z0-9_]+\s*\}\}")
TEST_FAILURE_ENV = "HEP_WORKFLOW_TEST_FAIL_PACKAGE_FINALIZE_AFTER"
REQUIRED_FILES = (
    "request.md",
    "result-summary.md",
    "result.wl",
    "result-python.py",
    "result-meta.json",
    "run-instructions.md",
)
GRAPH_BOUND_RESULT_FILES = (
    "request.md",
    "result-summary.md",
)


@dataclass(frozen=True)
class FinalizationResult:
    status: str
    task_dir: Path
    attempt_dir: Path
    history_action: str
    cleanup_pending: bool = False


class ExitOneArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(1, f"{self.prog}: error: {message}\n")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = ExitOneArgumentParser(
        description=(
            "Validate an owned Package-Scribe batch attempt and atomically "
            "publish its task directory plus manifest update."
        )
    )
    parser.add_argument("--task-dir", required=True, help="Canonical calculations/task-NNN path.")
    parser.add_argument("--attempt-dir", required=True, help="Owned batch attempt directory.")
    parser.add_argument("--attempt-id", required=True, help="Attempt ownership token.")
    parser.add_argument(
        "--repo-root",
        help="Repository root; normally discovered from this script.",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
    )
    return parser.parse_args(argv)


def _discover_repo_root() -> Path:
    for candidate in Path(__file__).resolve().parents:
        if (
            (candidate / "schemas" / "result-meta.schema.json").is_file()
            and (candidate / "scripts" / "_dependency_graph.py").is_file()
        ):
            return candidate
    raise FileNotFoundError("cannot locate the hep-workflow repository root")


def _load_repo_modules(repo_root: Path) -> tuple[Any, Any, Any, Any, Any, Any]:
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from jsonschema import Draft202012Validator
    from scripts._calculation_provenance import (
        derivation_artifact_errors,
        python_function_interface_errors,
    )
    from scripts._dependency_graph import (
        build_dependency_graph,
        sha256_file,
        verify_dependency_graph,
    )
    from scripts._strict_json import load_json
    from scripts._workflow_dependencies import calculation_dependency_specs

    return (
        Draft202012Validator,
        derivation_artifact_errors,
        python_function_interface_errors,
        (build_dependency_graph, sha256_file, verify_dependency_graph),
        load_json,
        calculation_dependency_specs,
    )


def _require_mirror_invariants(repo_root: Path) -> None:
    from scripts.sync_skill_mirrors import compare_shared_helpers, compare_skill_trees

    failures = [
        *compare_skill_trees(
            repo_root / ".claude" / "skills",
            repo_root / ".agents" / "skills",
        ),
        *compare_shared_helpers(repo_root),
    ]
    if failures:
        raise ValueError("skill mirror invariant failed: " + "; ".join(failures))


def _resolve_cli_path(value: str, *, must_exist: bool, label: str) -> Path:
    expanded = Path(value).expanduser()
    lexical = expanded if expanded.is_absolute() else Path.cwd() / expanded
    if lexical.is_symlink():
        raise ValueError(f"{label} must not be a symlink: {lexical}")
    parent = lexical.parent.resolve(strict=must_exist)
    resolved = parent / lexical.name
    if must_exist:
        _require_real_directory(resolved, label)
    elif resolved.exists():
        _require_real_directory(resolved, label)
    return resolved


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


def _project_for_task(task_dir: Path) -> Path:
    if (
        TASK_ID_PATTERN.fullmatch(task_dir.name) is None
        or task_dir.parent.name != "calculations"
    ):
        raise ValueError("task directory must be <project>/calculations/task-NNN")
    _require_real_directory(task_dir.parent, "calculations directory")
    project_dir = task_dir.parent.parent
    _require_real_directory(project_dir, "workspace project")
    _require_regular_file(project_dir / "manifest.json", "workspace manifest", nonempty=True)
    return project_dir


def _identity_from_payload(payload: object, label: str) -> PathIdentity:
    expected_fields = {"kind", "sha256", "size", "mode", "device", "inode"}
    if not isinstance(payload, dict) or set(payload) != expected_fields:
        raise ValueError(f"{label} must contain exactly {sorted(expected_fields)}")
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
        raise ValueError(f"{label} has an invalid kind")
    if (
        not isinstance(identity.sha256, str)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", identity.sha256)
    ):
        raise ValueError(f"{label} present identity requires a valid SHA-256")
    if any(
        not isinstance(value, int) or isinstance(value, bool) or value < 0
        for value in (identity.size, identity.mode, identity.device, identity.inode)
    ):
        raise ValueError(f"{label} contains invalid filesystem metadata")
    return identity


def _json_bytes(payload: dict[str, Any]) -> bytes:
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


def _write_private_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_bytes(_json_bytes(payload))


def _load_reservation(
    attempt_dir: Path,
    task_dir: Path,
    project_dir: Path,
    attempt_id: str,
    load_json: Any,
) -> tuple[dict[str, Any], PathIdentity]:
    if ATTEMPT_ID_PATTERN.fullmatch(attempt_id) is None:
        raise ValueError("attempt id must be 32 lowercase hexadecimal characters")
    expected_attempt = (
        project_dir
        / BATCH_ATTEMPT_ROOT
        / f"{task_dir.name}--{attempt_id}"
    )
    if attempt_dir != expected_attempt:
        raise PermissionError(
            f"attempt directory is not the canonical path for this token: {attempt_dir}"
        )
    _require_real_directory(project_dir / BATCH_ATTEMPT_ROOT, "package attempt root")
    reservation_path = attempt_dir / RESERVATION_FILENAME
    _require_regular_file(reservation_path, "attempt reservation", nonempty=True)
    payload = load_json(reservation_path)
    if not isinstance(payload, dict):
        raise ValueError("attempt reservation must be a JSON object")
    if payload.get("version") != 1 or payload.get("kind") != "package-scribe-batch-attempt":
        raise ValueError("unexpected package-scribe attempt reservation type/version")
    if payload.get("task_id") != task_dir.name:
        raise ValueError("attempt reservation task_id does not match --task-dir")
    if payload.get("attempt_id") != attempt_id:
        raise PermissionError("attempt ownership token does not match reservation")
    if payload.get("final_task_path") != task_dir.relative_to(project_dir).as_posix():
        raise ValueError("attempt reservation final task path does not match")
    if payload.get("blocked") is not False:
        raise ValueError("blocked/incomplete attempts are diagnostic-only and cannot publish")
    history_event_id = payload.get("history_event_id")
    if (
        not isinstance(history_event_id, str)
        or ATTEMPT_ID_PATTERN.fullmatch(history_event_id) is None
    ):
        raise ValueError("attempt reservation has an invalid history_event_id")
    baseline = _identity_from_payload(
        payload.get("baseline_identity"),
        "attempt baseline_identity",
    )
    return payload, baseline


def _copy_regular_file(source: Path, destination: Path) -> None:
    _require_regular_file(source, "candidate file")
    source_metadata = source.lstat()
    destination.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(source, flags)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError(f"candidate changed to a non-regular file: {source}")
        with os.fdopen(descriptor, "rb") as reader, destination.open("xb") as writer:
            descriptor = -1
            shutil.copyfileobj(reader, writer)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    destination.chmod(stat.S_IMODE(source_metadata.st_mode))


def _copy_candidate_tree(source: Path, destination: Path) -> None:
    _require_real_directory(source, "attempt directory")
    destination.mkdir(mode=0o755)
    for entry in sorted(source.rglob("*"), key=lambda item: os.fsencode(item.relative_to(source))):
        relative = entry.relative_to(source)
        if relative == Path(RESERVATION_FILENAME):
            continue
        if any(component.startswith(".") for component in relative.parts):
            raise ValueError(f"hidden candidate path is not publishable: {relative}")
        metadata = entry.lstat()
        target = destination / relative
        if stat.S_ISLNK(metadata.st_mode):
            raise ValueError(f"candidate path must not be a symlink: {entry}")
        if stat.S_ISDIR(metadata.st_mode):
            target.mkdir(exist_ok=False)
            target.chmod(stat.S_IMODE(metadata.st_mode))
        elif stat.S_ISREG(metadata.st_mode):
            _copy_regular_file(entry, target)
        else:
            raise ValueError(f"candidate contains a special filesystem object: {entry}")


def _candidate_required_checks(
    candidate_dir: Path,
    *,
    allow_provenance_sentinel: bool = False,
) -> None:
    for name in REQUIRED_FILES:
        _require_regular_file(candidate_dir / name, f"required result {name}", nonempty=True)
    for name in (
        "request.md",
        "result-summary.md",
        "run-instructions.md",
        "result-python.py",
        "result-meta.json",
    ):
        text = (candidate_dir / name).read_text(encoding="utf-8")
        if allow_provenance_sentinel and name == "result-meta.json":
            text = text.replace("{{input_provenance_status}}", "verified")
        if PLACEHOLDER_PATTERN.search(text):
            raise ValueError(f"unresolved template placeholder in {name}")
    summary = (candidate_dir / "result-summary.md").read_text(encoding="utf-8")
    if "## Benchmark Verification" not in summary:
        raise ValueError("result-summary.md is missing the Benchmark Verification section")


def _find_task(calc_tasks: dict[str, Any], task_id: str) -> dict[str, Any]:
    matches = [
        task
        for task in calc_tasks.get("tasks", [])
        if isinstance(task, dict) and task.get("task_id") == task_id
    ]
    if len(matches) != 1:
        raise ValueError(f"calc-tasks.json must contain exactly one {task_id!r}")
    return matches[0]


def _validate_schema(
    payload: object,
    schema_path: Path,
    Draft202012Validator: Any,
    label: str,
) -> None:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    errors = sorted(
        Draft202012Validator(schema).iter_errors(payload),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if errors:
        rendered = "; ".join(
            f"{'.'.join(str(part) for part in error.absolute_path) or '<root>'}: {error.message}"
            for error in errors
        )
        raise ValueError(f"{label} schema validation failed: {rendered}")


def _validate_result_cross_file(
    *,
    candidate_dir: Path,
    metadata: dict[str, Any],
    task: dict[str, Any],
    model_spec: dict[str, Any],
    python_function_interface_errors: Any,
) -> None:
    parameters = metadata.get("parameters", [])
    parameter_names = [
        item.get("canonical_name")
        for item in parameters
        if isinstance(item, dict)
    ]
    if len(parameter_names) != len(set(parameter_names)):
        raise ValueError("result-meta parameters contain duplicate canonical_name values")
    model_parameters = {
        item.get("name"): item
        for item in model_spec.get("parameters", [])
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    if len(model_parameters) != len(model_spec.get("parameters", [])):
        raise ValueError("model-spec parameters must have unique string names")
    allowed_names = set(model_parameters)
    unknown = sorted(
        name for name in parameter_names if not isinstance(name, str) or name not in allowed_names
    )
    if unknown:
        raise ValueError(f"result-meta contains unknown canonical parameters: {unknown}")
    for parameter in parameters:
        if not isinstance(parameter, dict):  # pragma: no cover - schema precedes this check
            raise ValueError("result-meta parameter entries must be objects")
        name = str(parameter["canonical_name"])
        model_parameter = model_parameters[name]
        for field in ("role", "unit"):
            if parameter.get(field) != model_parameter.get(field):
                raise ValueError(
                    f"result-meta parameter {name!r} {field} "
                    f"{parameter.get(field)!r} does not match model-spec "
                    f"{model_parameter.get(field)!r}"
                )
    observable = metadata.get("observable")
    if metadata.get("return_value", {}).get("name") != observable:
        raise ValueError("result-meta return_value.name does not match observable")
    if task.get("target_quantity") != observable:
        raise ValueError("result-meta observable does not match calc-tasks target_quantity")

    python_file = candidate_dir / str(metadata.get("python_file", ""))
    try:
        tree = ast.parse(python_file.read_text(encoding="utf-8"), filename=str(python_file))
    except (OSError, UnicodeError, SyntaxError) as exc:
        raise ValueError(f"result Python backend does not parse: {exc}") from exc
    interface_issues = python_function_interface_errors(
        tree,
        metadata.get("python_function"),
        parameter_names,
    )
    if interface_issues:
        raise ValueError("result Python backend interface is invalid: " + "; ".join(interface_issues))


def _build_candidate_graph(
    *,
    candidate_dir: Path,
    overlay_project: Path,
    project_dir: Path,
    repo_root: Path,
    task_id: str,
    metadata: dict[str, Any],
    build_dependency_graph: Any,
    verify_dependency_graph: Any,
    calculation_dependency_specs: Any,
) -> dict[str, Any]:
    for relative in ("model/model-spec.json", "model/calc-tasks.json"):
        _copy_regular_file(project_dir / relative, overlay_project / relative)
    benchmarks = project_dir / "model" / "benchmarks.json"
    if benchmarks.exists():
        _copy_regular_file(benchmarks, overlay_project / "model" / "benchmarks.json")

    overlay_task = overlay_project / "calculations" / task_id
    source_wl = metadata.get("source_wl")
    python_file = metadata.get("python_file")
    if not isinstance(source_wl, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*\.wl", source_wl):
        raise ValueError("result-meta source_wl is not a safe task-local .wl filename")
    if not isinstance(python_file, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*\.py", python_file):
        raise ValueError("result-meta python_file is not a safe task-local .py filename")
    for filename in (*GRAPH_BOUND_RESULT_FILES, source_wl, python_file):
        _copy_regular_file(candidate_dir / filename, overlay_task / filename)

    specs = calculation_dependency_specs(
        overlay_project,
        repo_root,
        task_id,
        result_meta=metadata,
    )
    graph = build_dependency_graph(overlay_project, repo_root, specs)
    errors = verify_dependency_graph(
        graph,
        overlay_project,
        repo_root,
        expected_specs=calculation_dependency_specs(
            overlay_project,
            repo_root,
            task_id,
            result_meta=metadata,
        ),
        allow_legacy=False,
    )
    if errors:
        raise ValueError("candidate dependency graph failed verification: " + "; ".join(errors))
    return graph


def _verify_graph_against_sources(
    *,
    graph: dict[str, Any],
    candidate_dir: Path,
    project_dir: Path,
    repo_root: Path,
    task_id: str,
    sha256_file: Any,
) -> None:
    candidate_prefix = f"calculations/{task_id}/"
    for entry in graph.get("entries", []):
        scope = entry.get("scope")
        relative = entry.get("path")
        declared = entry.get("sha256")
        if not isinstance(relative, str):
            raise ValueError("dependency graph contains a non-string path")
        if scope == "project" and relative.startswith(candidate_prefix):
            source = candidate_dir / relative.removeprefix(candidate_prefix)
        elif scope == "project":
            source = project_dir / relative
        elif scope == "repository":
            source = repo_root / relative
        else:
            raise ValueError(f"dependency graph contains invalid scope {scope!r}")
        if sha256_file(source) != declared:
            raise ValueError(f"dependency changed before publication: {scope}:{relative}")


def _derive_numerics(analyses: Iterable[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted((deepcopy(item) for item in analyses), key=lambda item: str(item["analysis_id"]))
    ids = [str(item["analysis_id"]) for item in ordered]
    if len(ids) != len(set(ids)):
        raise ValueError("manifest contains duplicate numerics analysis_id values")
    if not ordered:
        return {
            "status": "not_started",
            "files": [],
            "analyses": [],
            "produced_by": None,
            "timestamp": None,
        }
    statuses = [str(item.get("status")) for item in ordered]
    if "failed" in statuses:
        status = "failed"
    elif "blocked" in statuses:
        status = "blocked"
    elif "stale" in statuses:
        status = "stale"
    elif all(value == "done" for value in statuses):
        status = "done"
    else:
        status = "partial"
    latest = max(
        ordered,
        key=lambda item: (str(item.get("timestamp", "")), str(item.get("analysis_id", ""))),
    )
    return {
        "status": status,
        "files": sorted(
            {
                path
                for item in ordered
                for path in item.get("files", [])
                if isinstance(path, str)
            }
        ),
        "analyses": ordered,
        "produced_by": latest["produced_by"],
        "timestamp": latest["timestamp"],
    }


def _build_manifest_candidate(
    *,
    manifest: dict[str, Any],
    calc_tasks: dict[str, Any],
    metadata: dict[str, Any],
    task_id: str,
    timestamp: str,
    event_id: str,
    result_changed: bool,
) -> tuple[dict[str, Any], str]:
    if manifest.get("manifest_version") != 2:
        raise ValueError("package publication requires manifest_version=2")
    candidate = deepcopy(manifest)
    artifacts = candidate.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ValueError("manifest artifacts must be an object")
    model = artifacts.get("model")
    calculations = artifacts.get("calculations")
    if not isinstance(model, dict) or not isinstance(calculations, dict):
        raise ValueError("manifest model/calculations artifacts must be objects")
    active_version = candidate.get("active_model_version")
    if active_version != model.get("version"):
        raise ValueError("manifest active_model_version disagrees with model.version")
    depends_on = metadata.get("depends_on")
    if not isinstance(depends_on, dict) or (
        depends_on.get("model_version") != model.get("version")
        or depends_on.get("model_checksum") != model.get("checksum")
    ):
        raise ValueError("result-meta model dependency is not the active manifest model")

    declared = sorted(
        str(task["task_id"])
        for task in calc_tasks.get("tasks", [])
        if isinstance(task, dict) and isinstance(task.get("task_id"), str)
    )
    if len(declared) != len(set(declared)) or task_id not in declared:
        raise ValueError("calc-tasks.json contains duplicate tasks or omits the target task")
    completed = calculations.get("completed_tasks")
    pending = calculations.get("pending_tasks")
    if not isinstance(completed, list) or not all(isinstance(item, str) for item in completed):
        raise ValueError("manifest calculations.completed_tasks must be a string array")
    if not isinstance(pending, list) or not all(isinstance(item, str) for item in pending):
        raise ValueError("manifest calculations.pending_tasks must be a string array")
    if len(completed) != len(set(completed)) or len(pending) != len(set(pending)):
        raise ValueError("manifest calculation task arrays contain duplicates")
    if set(completed) & set(pending):
        raise ValueError("manifest completed/pending calculation tasks overlap")

    calculations_status = calculations.get("status")
    started_from_stale = calculations_status == "stale"
    if not started_from_stale and set(completed) | set(pending) != set(declared):
        raise ValueError("manifest completed/pending calculation tasks do not partition calc-tasks")
    aggregate_model_dependency = calculations.get("depends_on", {}).get("model")
    if (
        not started_from_stale
        and completed
        and (
            not isinstance(aggregate_model_dependency, dict)
            or aggregate_model_dependency.get("version") != model.get("version")
            or aggregate_model_dependency.get("checksum") != model.get("checksum")
        )
    ):
        raise ValueError(
            "current calculations dependency is not the active manifest model; "
            "publish the mechanical stale transition before rerunning a task"
        )

    history_action = (
        f"calc_task_{task_id}_revised"
        if task_id in completed
        else f"calc_task_{task_id}_complete"
    )
    if started_from_stale:
        completed = []
    new_completed = sorted({*completed, task_id})
    new_pending = sorted(set(declared) - set(new_completed))
    calculations.update(
        {
            "status": "done" if not new_pending else "partial",
            "completed_tasks": new_completed,
            "pending_tasks": new_pending,
            "depends_on": {
                "model": {
                    "version": model.get("version"),
                    "checksum": model.get("checksum"),
                }
            },
            "produced_by": "package-scribe",
            "timestamp": timestamp,
        }
    )

    numerics = artifacts.get("numerics")
    if result_changed and isinstance(numerics, dict):
        analyses = numerics.get("analyses")
        if not isinstance(analyses, list) or not all(isinstance(item, dict) for item in analyses):
            raise ValueError("manifest numerics analyses must be an object array")
        refreshed: list[dict[str, Any]] = []
        for original in analyses:
            analysis = deepcopy(original)
            dependencies = analysis.get("depends_on")
            calculations_dependency = (
                dependencies.get("calculations", {})
                if isinstance(dependencies, dict)
                else {}
            )
            tasks = calculations_dependency.get("tasks", [])
            if analysis.get("status") in {"done", "partial"} and task_id in tasks:
                analysis["status"] = "stale"
            refreshed.append(analysis)
        artifacts["numerics"] = _derive_numerics(refreshed)

    history = candidate.get("history")
    if not isinstance(history, list) or not all(isinstance(item, dict) for item in history):
        raise ValueError("manifest history must be an object array")
    existing_event_ids = {
        item.get("event_id")
        for item in history
        if isinstance(item.get("event_id"), str)
    }
    if event_id in existing_event_ids:
        raise ValueError(f"manifest history already contains event_id {event_id}")
    history.append(
        {
            "action": history_action,
            "event_id": event_id,
            "timestamp": timestamp,
            "by": "package-scribe",
            "note": f"task_id={task_id}; atomically published validated attempt",
        }
    )
    candidate["last_updated"] = timestamp[:10]
    return candidate, history_action


def _validate_complete_candidate(
    *,
    candidate_dir: Path,
    project_dir: Path,
    repo_root: Path,
    task_id: str,
    metadata: dict[str, Any],
    task: dict[str, Any],
    model_spec: dict[str, Any],
    graph: dict[str, Any],
    Draft202012Validator: Any,
    derivation_artifact_errors: Any,
    python_function_interface_errors: Any,
    sha256_file: Any,
) -> None:
    _candidate_required_checks(candidate_dir)
    _validate_schema(
        metadata,
        repo_root / "schemas" / "result-meta.schema.json",
        Draft202012Validator,
        "result-meta.json",
    )
    if metadata.get("task_id") != task_id:
        raise ValueError("result-meta task_id does not match the reserved task")
    if metadata.get("calculation_provenance") == "blocked" or metadata.get(
        "translation_status"
    ) != "complete":
        raise ValueError(
            "blocked/partial/failed generation is diagnostic-only and cannot "
            "replace the canonical task result or enter completed_tasks"
        )
    _validate_result_cross_file(
        candidate_dir=candidate_dir,
        metadata=metadata,
        task=task,
        model_spec=model_spec,
        python_function_interface_errors=python_function_interface_errors,
    )
    issues = derivation_artifact_errors(candidate_dir, task_id, task, metadata)
    if issues:
        raise ValueError("calculation derivation evidence is invalid: " + "; ".join(issues))
    _verify_graph_against_sources(
        graph=graph,
        candidate_dir=candidate_dir,
        project_dir=project_dir,
        repo_root=repo_root,
        task_id=task_id,
        sha256_file=sha256_file,
    )


def finalize_attempt(
    *,
    task_dir: Path,
    attempt_dir: Path,
    attempt_id: str,
    repo_root: Path,
) -> FinalizationResult:
    (
        Draft202012Validator,
        derivation_artifact_errors,
        python_function_interface_errors,
        dependency_helpers,
        load_json,
        calculation_dependency_specs,
    ) = _load_repo_modules(repo_root)
    build_dependency_graph, sha256_file, verify_dependency_graph = dependency_helpers
    _require_mirror_invariants(repo_root)
    project_dir = _project_for_task(task_dir)

    with publication_lock(project_dir, "package-finalize", blocking=True) as lock:
        reservation, baseline = _load_reservation(
            attempt_dir,
            task_dir,
            project_dir,
            attempt_id,
            load_json,
        )
        state = reservation.get("state")
        if state == "published":
            published_identity = _identity_from_payload(
                reservation.get("published_identity"),
                "attempt published_identity",
            )
            manifest = load_json(project_dir / "manifest.json")
            _validate_schema(
                manifest,
                repo_root / "schemas" / "manifest.schema.json",
                Draft202012Validator,
                "manifest.json",
            )
            event_id = reservation.get("history_event_id")
            history_action = reservation.get("history_action")
            history = manifest.get("history", []) if isinstance(manifest, dict) else []
            if capture_identity(task_dir) != published_identity or not any(
                isinstance(entry, dict)
                and entry.get("event_id") == event_id
                and entry.get("action") == history_action
                for entry in history
            ):
                raise ValueError("published attempt no longer matches task/manifest state")
            return FinalizationResult(
                "already_published",
                task_dir,
                attempt_dir,
                str(history_action),
            )
        if state != "initialized":
            raise ValueError(f"attempt state must be 'initialized', got {state!r}")
        current_task_identity = capture_identity(task_dir)
        if current_task_identity != baseline:
            raise ValueError(
                "final task changed after this attempt was reserved; refusing to overwrite it"
            )
        source_attempt_identity = capture_identity(attempt_dir)
        manifest_path = project_dir / "manifest.json"
        manifest_identity = capture_identity(manifest_path)
        manifest = load_json(manifest_path)
        if not isinstance(manifest, dict):
            raise ValueError("manifest.json must contain an object")
        _validate_schema(
            manifest,
            repo_root / "schemas" / "manifest.schema.json",
            Draft202012Validator,
            "manifest.json",
        )
        calc_tasks = load_json(project_dir / "model" / "calc-tasks.json")
        if not isinstance(calc_tasks, dict):
            raise ValueError("calc-tasks.json must contain an object")
        model_spec = load_json(project_dir / "model" / "model-spec.json")
        if not isinstance(model_spec, dict):
            raise ValueError("model-spec.json must contain an object")
        _validate_schema(
            calc_tasks,
            repo_root / "schemas" / "calc-tasks.schema.json",
            Draft202012Validator,
            "calc-tasks.json",
        )
        _validate_schema(
            model_spec,
            repo_root / "schemas" / "model-spec.schema.json",
            Draft202012Validator,
            "model-spec.json",
        )
        task = _find_task(calc_tasks, task_dir.name)

        transaction = PublicationTransaction.begin(
            project_dir,
            "package-finalize",
            lock=lock,
        )
        cleanup_pending = False
        try:
            staged_task = transaction.stage_path(f"candidate/{task_dir.name}")
            _copy_candidate_tree(attempt_dir, staged_task)
            _candidate_required_checks(
                staged_task,
                allow_provenance_sentinel=True,
            )
            metadata = load_json(staged_task / "result-meta.json")
            if not isinstance(metadata, dict):
                raise ValueError("result-meta.json must contain an object")
            overlay_project = transaction.stage_path("validation-project")
            overlay_project.mkdir()
            graph = _build_candidate_graph(
                candidate_dir=staged_task,
                overlay_project=overlay_project,
                project_dir=project_dir,
                repo_root=repo_root,
                task_id=task_dir.name,
                metadata=metadata,
                build_dependency_graph=build_dependency_graph,
                verify_dependency_graph=verify_dependency_graph,
                calculation_dependency_specs=calculation_dependency_specs,
            )
            metadata["input_provenance"] = graph
            _write_private_json(staged_task / "result-meta.json", metadata)
            metadata = load_json(staged_task / "result-meta.json")
            if not isinstance(metadata, dict):  # pragma: no cover - defensive
                raise ValueError("finalized result-meta.json must contain an object")
            _validate_complete_candidate(
                candidate_dir=staged_task,
                project_dir=project_dir,
                repo_root=repo_root,
                task_id=task_dir.name,
                metadata=metadata,
                task=task,
                model_spec=model_spec,
                graph=graph,
                Draft202012Validator=Draft202012Validator,
                derivation_artifact_errors=derivation_artifact_errors,
                python_function_interface_errors=python_function_interface_errors,
                sha256_file=sha256_file,
            )

            candidate_identity = capture_identity(staged_task)
            timestamp = (
                datetime.now(timezone.utc)
                .replace(microsecond=0)
                .isoformat()
                .replace("+00:00", "Z")
            )
            manifest_candidate, history_action = _build_manifest_candidate(
                manifest=manifest,
                calc_tasks=calc_tasks,
                metadata=metadata,
                task_id=task_dir.name,
                timestamp=timestamp,
                event_id=str(reservation["history_event_id"]),
                result_changed=(
                    baseline.kind == "directory"
                    and baseline.sha256 != candidate_identity.sha256
                ),
            )
            _validate_schema(
                manifest_candidate,
                repo_root / "schemas" / "manifest.schema.json",
                Draft202012Validator,
                "manifest.json candidate",
            )

            published_reservation = deepcopy(reservation)
            published_reservation.update(
                {
                    "state": "published",
                    "published_at": timestamp,
                    "published_identity": dict(asdict(candidate_identity)),
                    "history_action": history_action,
                }
            )
            staged_reservation = transaction.stage_path("attempt-reservation.json")
            _write_private_json(staged_reservation, published_reservation)
            staged_manifest = transaction.stage_path("manifest.json")
            _write_private_json(staged_manifest, manifest_candidate)

            transaction.add(
                staged_task,
                task_dir,
                mode="replace",
                expected_before=baseline,
            )
            transaction.add(
                staged_reservation,
                attempt_dir / RESERVATION_FILENAME,
                mode="replace",
                expected_before=capture_identity(attempt_dir / RESERVATION_FILENAME),
            )
            # Manifest is deliberately the final authoritative publication entry.
            transaction.add(
                staged_manifest,
                manifest_path,
                mode="replace",
                expected_before=manifest_identity,
            )

            def pre_publish_check() -> None:
                if capture_identity(attempt_dir) != source_attempt_identity:
                    raise ValueError("attempt changed while it was being validated")
                _validate_complete_candidate(
                    candidate_dir=staged_task,
                    project_dir=project_dir,
                    repo_root=repo_root,
                    task_id=task_dir.name,
                    metadata=metadata,
                    task=task,
                    model_spec=model_spec,
                    graph=graph,
                    Draft202012Validator=Draft202012Validator,
                    derivation_artifact_errors=derivation_artifact_errors,
                    python_function_interface_errors=python_function_interface_errors,
                    sha256_file=sha256_file,
                )

            def post_publish_check() -> None:
                if capture_identity(task_dir) != candidate_identity:
                    raise ValueError("published task directory differs from validated candidate")
                published_manifest = load_json(manifest_path)
                if published_manifest != manifest_candidate:
                    raise ValueError("published manifest differs from validated candidate")
                _verify_graph_against_sources(
                    graph=graph,
                    candidate_dir=task_dir,
                    project_dir=project_dir,
                    repo_root=repo_root,
                    task_id=task_dir.name,
                    sha256_file=sha256_file,
                )

            failure_target = os.environ.get(TEST_FAILURE_ENV)

            def after_publish(destination: Path, index: int) -> None:
                if failure_target in {str(index), destination.name}:
                    raise OSError(
                        f"injected package finalization failure after {destination.name}"
                    )

            try:
                transaction.commit(
                    pre_publish_check=pre_publish_check,
                    post_publish_check=post_publish_check,
                    after_publish_entry=after_publish,
                )
            except TransactionCommittedCleanupError as exc:
                cleanup_pending = True
                print(
                    "warning: package result and manifest committed successfully, but "
                    f"private cleanup is pending for transaction {exc.transaction_id}: "
                    f"{exc.cleanup_error}. Do not retry; run the recovery command.",
                    file=sys.stderr,
                )
            return FinalizationResult(
                "published",
                task_dir,
                attempt_dir,
                history_action,
                cleanup_pending,
            )
        except BaseException:
            transaction.abort()
            raise


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        repo_root = (
            _resolve_cli_path(args.repo_root, must_exist=True, label="repository root")
            if args.repo_root
            else _discover_repo_root()
        )
        task_dir = _resolve_cli_path(args.task_dir, must_exist=False, label="task directory")
        attempt_dir = _resolve_cli_path(
            args.attempt_dir,
            must_exist=True,
            label="attempt directory",
        )
        result = finalize_attempt(
            task_dir=task_dir,
            attempt_dir=attempt_dir,
            attempt_id=args.attempt_id,
            repo_root=repo_root,
        )
    except (
        OSError,
        UnicodeError,
        ValueError,
        PermissionError,
        ImportError,
        json.JSONDecodeError,
        PublicationTransactionError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    payload = {
        "status": result.status,
        "task_dir": str(result.task_dir),
        "attempt_dir": str(result.attempt_dir),
        "history_action": result.history_action,
        "cleanup_pending": result.cleanup_pending,
    }
    if args.format == "json":
        print(json.dumps(payload, sort_keys=True))
    else:
        print(
            f"{result.status}: {result.task_dir} "
            f"({result.history_action}, attempt={result.attempt_dir})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
