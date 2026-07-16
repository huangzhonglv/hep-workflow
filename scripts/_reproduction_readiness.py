"""Deterministic, read-only readiness derivation for reproduction targets.

The readiness report is an ephemeral routing input.  It is never persisted in
``manifest.json`` and never treats mutable manifest status as scientific
evidence.  Each required stage is derived from current schema-valid artifacts,
exact dependency graphs, and the selected target kind.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Callable

try:
    from _dependency_graph import sha256_file, verify_dependency_graph
    from _identity import resolve_contained, validate_analysis_id
    from _scan_artifact_validation import validate_scan_artifact_pair
    from _strict_json import StrictJSONError, load_json
    from _workflow_dependencies import (
        calculation_dependency_specs,
        scan_dependency_specs,
        scan_producer_from_graph,
        verify_frozen_scan_dependency_graph,
    )
except ModuleNotFoundError:  # Imported as scripts._reproduction_readiness.
    from scripts._dependency_graph import sha256_file, verify_dependency_graph
    from scripts._identity import resolve_contained, validate_analysis_id
    from scripts._scan_artifact_validation import validate_scan_artifact_pair
    from scripts._strict_json import StrictJSONError, load_json
    from scripts._workflow_dependencies import (
        calculation_dependency_specs,
        scan_dependency_specs,
        scan_producer_from_graph,
        verify_frozen_scan_dependency_graph,
    )


REPO_ROOT = Path(__file__).resolve().parent.parent
FORMULA_KIND = "formula"
NOT_READY_STAGE_STATUSES = {"missing", "invalid", "stale"}

ReferenceValidator = Callable[[Path, dict[str, Any], str], None]


def _schema_errors(
    schema_name: str,
    payload: Any,
    *,
    repo_root: Path,
) -> list[str]:
    try:
        from jsonschema import Draft202012Validator
    except ImportError as exc:  # pragma: no cover - dependency precondition
        return [f"jsonschema is not installed: {exc}"]

    schema = load_json(repo_root / "schemas" / schema_name)
    validator = Draft202012Validator(schema)
    messages: list[str] = []
    for error in sorted(
        validator.iter_errors(payload),
        key=lambda item: [str(part) for part in item.absolute_path],
    ):
        path = ".".join(str(part) for part in error.absolute_path) or "<root>"
        messages.append(f"{path}: {error.message}")
    return messages


def _project_relative(project_dir: Path, path: Path) -> str:
    try:
        return path.relative_to(project_dir).as_posix()
    except ValueError:
        return str(path)


def _issue(
    code: str,
    detail: str,
    *,
    path: str | None = None,
    fields: list[str] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"code": code, "detail": detail}
    if path is not None:
        payload["path"] = path
    if fields:
        payload["fields"] = sorted(set(fields))
    return payload


def _stage(
    *,
    required: bool,
    status: str,
    issues: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "required": required,
        "status": status,
        "issues": sorted(
            issues or [],
            key=lambda item: (
                str(item.get("code", "")),
                str(item.get("path", "")),
                str(item.get("detail", "")),
            ),
        ),
    }


def _calculation_stage(
    *,
    required: bool,
    status: str,
    task_ids: list[str] | None = None,
    issues: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    payload = _stage(required=required, status=status, issues=issues)
    payload["task_ids"] = sorted(set(task_ids or []))
    return payload


def _not_applicable_stage() -> dict[str, Any]:
    return _stage(required=False, status="not_applicable")


def _not_applicable_calculation_stage() -> dict[str, Any]:
    return _calculation_stage(
        required=False,
        status="not_applicable",
        task_ids=[],
    )


def _load_required_object(
    path: Path,
    schema_name: str,
    *,
    repo_root: Path,
    label: str,
) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        raise ValueError(f"missing {label}: {path}")
    try:
        payload = load_json(path)
    except (OSError, StrictJSONError) as exc:
        raise ValueError(f"cannot strict-load {label}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must contain a JSON object")
    errors = _schema_errors(schema_name, payload, repo_root=repo_root)
    if errors:
        raise ValueError(f"{label} failed schema validation: {'; '.join(errors)}")
    return payload


def task_catalog(calc_tasks: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return the task catalog after callers validate duplicate task IDs."""

    return {
        str(task.get("task_id")): task
        for task in calc_tasks.get("tasks", [])
        if isinstance(task, dict) and task.get("task_id")
    }


def result_meta_paths(project_dir: Path) -> dict[str, Path]:
    """Return canonical result metadata paths without trusting manifest lists."""

    paths: dict[str, Path] = {}
    calculations_root = project_dir / "calculations"
    if not calculations_root.exists():
        return paths
    for path in sorted(calculations_root.glob("task-*/result-meta.json")):
        paths[path.parent.name] = path
    return paths


def resolve_tasks_for_target(
    target: dict[str, Any],
    calc_task_by_id: dict[str, dict[str, Any]],
    meta_paths: dict[str, Path],
) -> tuple[list[str], list[str]]:
    """Resolve target observables to calculation tasks deterministically."""

    observables = sorted({str(item) for item in target.get("observables", [])})
    task_ids: set[str] = set()

    meta_by_task: dict[str, dict[str, Any]] = {}
    for task_id, path in meta_paths.items():
        try:
            metadata = load_json(path)
        except (OSError, StrictJSONError):
            continue
        if isinstance(metadata, dict):
            meta_by_task[task_id] = metadata

    unmatched: list[str] = []
    for observable in observables:
        matches = {
            task_id
            for task_id, task in calc_task_by_id.items()
            if task_id == observable or str(task.get("target_quantity")) == observable
        }
        matches.update(
            task_id
            for task_id, metadata in meta_by_task.items()
            if task_id in calc_task_by_id
            and (
                task_id == observable
                or str(metadata.get("observable")) == observable
            )
        )
        if not matches:
            unmatched.append(observable)
        task_ids.update(matches)

    return sorted(task_ids), unmatched


def _same_json_scalar(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left is right
    if isinstance(left, (int, float)) or isinstance(right, (int, float)):
        return (
            isinstance(left, (int, float))
            and not isinstance(left, bool)
            and isinstance(right, (int, float))
            and not isinstance(right, bool)
            and left == right
        )
    return type(left) is type(right) and left == right


def validate_target_scan_parameters(
    project_dir: Path,
    analysis_id: str,
    targets: list[dict[str, Any]],
    blocked_targets: set[str],
    *,
    repo_root: Path = REPO_ROOT,
) -> None:
    """Validate that one analysis is the exact declared slice for its targets."""

    scan_targets = [
        target
        for target in targets
        if target.get("kind") != FORMULA_KIND
        and str(target.get("id")) not in blocked_targets
    ]
    if not scan_targets:
        return
    config_path = project_dir / "numerics" / "scan-configs" / f"{analysis_id}.json"
    if not config_path.exists():
        raise ValueError(
            f"cannot verify target scan_parameters: missing scan config {config_path}"
        )
    scan_config = load_json(config_path)
    config_errors = _schema_errors(
        "scan-config.schema.json", scan_config, repo_root=repo_root
    )
    if config_errors:
        details = "\n  - ".join(config_errors)
        raise ValueError(f"scan config failed schema validation:\n  - {details}")
    if scan_config.get("analysis_id") != analysis_id:
        raise ValueError(
            f"scan config analysis_id {scan_config.get('analysis_id')!r} does not "
            f"match CLI analysis id {analysis_id!r}"
        )
    model_spec_path = project_dir / "model" / "model-spec.json"
    if not model_spec_path.exists():
        raise ValueError(
            f"cannot verify scan column units: missing model spec {model_spec_path}"
        )
    model_spec = load_json(model_spec_path)
    model_errors = _schema_errors(
        "model-spec.schema.json", model_spec, repo_root=repo_root
    )
    if model_errors:
        raise ValueError(
            "cannot verify scan column units: model-spec failed schema validation: "
            + "; ".join(model_errors)
        )
    config_dependency = scan_config.get("depends_on", {})
    manifest = load_json(project_dir / "manifest.json")
    manifest_model = manifest.get("artifacts", {}).get("model", {})
    actual_model_checksum = sha256_file(model_spec_path)
    if model_spec.get("version") != config_dependency.get("model_version"):
        raise ValueError("model-spec version does not match the analysis scan-config")
    if (
        manifest.get("active_model_version")
        != config_dependency.get("model_version")
        or manifest_model.get("version") != config_dependency.get("model_version")
        or manifest_model.get("checksum") != config_dependency.get("model_checksum")
    ):
        raise ValueError(
            "manifest active model dependency does not match the analysis scan-config"
        )
    if manifest_model.get("checksum") != actual_model_checksum:
        raise ValueError(
            "manifest/scan model checksum does not match the exact bytes of model-spec.json"
        )
    model_parameters = model_spec.get("parameters")
    if not isinstance(model_parameters, list):
        raise ValueError("model-spec parameters must be an array for unit validation")
    model_parameter_units: dict[str, str] = {}
    for parameter in model_parameters:
        if not isinstance(parameter, dict):
            raise ValueError("model-spec parameter entries must be objects")
        name = parameter.get("name")
        unit = parameter.get("unit")
        if not isinstance(name, str) or not isinstance(unit, str) or not unit.strip():
            raise ValueError(
                "model-spec parameters require canonical names and units"
            )
        if name in model_parameter_units:
            raise ValueError(f"model-spec contains duplicate parameter name {name!r}")
        model_parameter_units[name] = unit
    scan_parameter_names = [
        str(item.get("canonical_name"))
        for item in scan_config.get("scan_parameters", [])
        if isinstance(item, dict)
    ]
    if len(scan_parameter_names) != len(set(scan_parameter_names)):
        raise ValueError("scan config contains duplicate scan parameter names")
    actual = set(scan_parameter_names)
    fixed_items = [
        item
        for item in scan_config.get("fixed_parameters", [])
        if isinstance(item, dict)
    ]
    fixed_names = [str(item.get("canonical_name")) for item in fixed_items]
    if len(fixed_names) != len(set(fixed_names)):
        raise ValueError("scan config contains duplicate fixed parameter names")
    if actual & set(fixed_names):
        raise ValueError("scan config declares parameters as both scanned and fixed")
    observable_names = [
        str(item.get("observable"))
        for item in scan_config.get("observables", [])
        if isinstance(item, dict)
    ]
    if len(observable_names) != len(set(observable_names)):
        raise ValueError("scan config contains duplicate observable bindings")
    observable_bindings = {
        str(item.get("observable")): item.get("source")
        for item in scan_config.get("observables", [])
        if isinstance(item, dict)
    }
    result_meta_cache: dict[str, dict[str, Any]] = {}
    config_fixed: dict[str, Any] = {
        str(item.get("canonical_name")): item.get("value")
        for item in fixed_items
        if item.get("canonical_name") is not None
    }
    for target in scan_targets:
        declared = {str(item) for item in target.get("scan_parameters", [])}
        if declared != actual:
            raise ValueError(
                f"target {target.get('id')!r} scan_parameters {sorted(declared)} do "
                f"not match analysis {analysis_id} scan parameters {sorted(actual)}"
            )
        target_fixed = target.get("fixed", {})
        if not isinstance(target_fixed, dict):
            raise ValueError(f"target {target.get('id')!r} fixed must be an object")
        missing_config_fixed = sorted(set(config_fixed) - set(target_fixed))
        if missing_config_fixed:
            raise ValueError(
                f"target {target.get('id')!r} omits analysis fixed parameters "
                f"{missing_config_fixed}"
            )
        mismatched_fixed = sorted(
            name
            for name, value in config_fixed.items()
            if not _same_json_scalar(target_fixed.get(name), value)
        )
        if mismatched_fixed:
            raise ValueError(
                f"target {target.get('id')!r} fixed values do not exactly match "
                f"analysis config for {mismatched_fixed}"
            )
        unknown_fixed = sorted(set(target_fixed) - actual - set(config_fixed))
        if unknown_fixed:
            raise ValueError(
                f"target {target.get('id')!r} fixed parameters are absent from the "
                f"analysis configuration: {unknown_fixed}"
            )
        normalization = target.get("normalization")
        if not isinstance(normalization, dict):
            raise ValueError(
                f"target {target.get('id')!r} lacks normalization metadata"
            )
        canonical_units = normalization.get("canonical_units")
        fixed_normalization = normalization.get("fixed_parameters")
        if not isinstance(canonical_units, dict) or not isinstance(
            fixed_normalization, dict
        ):
            raise ValueError(
                f"target {target.get('id')!r} normalization lacks canonical units"
            )
        unit_bound_parameters = declared | set(config_fixed)
        for name in sorted(unit_bound_parameters):
            model_unit = model_parameter_units.get(name)
            if model_unit is None:
                raise ValueError(
                    f"target {target.get('id')!r} scan parameter {name!r} is absent "
                    "from model-spec"
                )
            if name in target_fixed:
                record = fixed_normalization.get(name)
                target_unit = (
                    record.get("canonical_unit")
                    if isinstance(record, dict)
                    else None
                )
            else:
                target_unit = canonical_units.get(name)
            if target_unit != model_unit:
                raise ValueError(
                    f"target {target.get('id')!r} canonical unit for scan parameter "
                    f"{name!r} is {target_unit!r}; model-spec requires {model_unit!r}"
                )

        kind = target.get("kind")
        if kind == "figure_curve":
            comparison_observables = {str(target.get("y_param"))}
        elif kind == "parametric_curve":
            parameter = str(target.get("curve_parameter"))
            coordinates = {
                str(target.get("x_param")),
                str(target.get("y_param")),
            }
            declared_observables = {
                str(item) for item in target.get("observables", [])
            }
            if parameter not in declared:
                raise ValueError(
                    f"target {target.get('id')!r} curve_parameter must be a scan "
                    "parameter"
                )
            invalid_coordinates = sorted(
                coordinate
                for coordinate in coordinates
                if coordinate != parameter and coordinate not in declared_observables
            )
            if invalid_coordinates:
                raise ValueError(
                    f"target {target.get('id')!r} parametric coordinates lack "
                    f"observable bindings: {invalid_coordinates}"
                )
            projected_scan_coordinates = sorted(
                coordinate
                for coordinate in coordinates
                if coordinate in declared and coordinate != parameter
            )
            if projected_scan_coordinates:
                raise ValueError(
                    f"target {target.get('id')!r} parametric curve would project "
                    f"varying scan coordinates: {projected_scan_coordinates}"
                )
            comparison_observables = coordinates & declared_observables
        elif kind in {"benchmark_point", "keyed_benchmark_set", "scan_table"}:
            comparison_observables = {
                str(item) for item in target.get("observables", [])
            }
        elif kind == "exclusion_region" and target.get("boundary", {}).get(
            "mode"
        ) == "observable_threshold":
            comparison_observables = {
                str(target.get("boundary", {}).get("observable"))
            }
        else:
            comparison_observables = set()
        comparison_observables.discard("None")
        for observable in sorted(comparison_observables):
            source = observable_bindings.get(observable)
            if not isinstance(source, dict):
                raise ValueError(
                    f"target {target.get('id')!r} observable {observable!r} lacks "
                    "an analysis observable binding"
                )
            source_type = source.get("type")
            if source_type == "task":
                task_id = source.get("task_id")
                if not isinstance(task_id, str):
                    raise ValueError(
                        f"observable {observable!r} has an invalid task binding"
                    )
                if task_id not in result_meta_cache:
                    meta_path = (
                        project_dir / "calculations" / task_id / "result-meta.json"
                    )
                    if not meta_path.exists():
                        raise ValueError(
                            f"cannot verify observable unit: missing {meta_path}"
                        )
                    metadata = load_json(meta_path)
                    meta_errors = _schema_errors(
                        "result-meta.schema.json", metadata, repo_root=repo_root
                    )
                    if meta_errors:
                        raise ValueError(
                            f"cannot verify observable unit: {task_id} result-meta "
                            f"is invalid: {'; '.join(meta_errors)}"
                        )
                    result_meta_cache[task_id] = metadata
                metadata = result_meta_cache[task_id]
                if metadata.get("task_id") != task_id:
                    raise ValueError(
                        f"result-meta task_id {metadata.get('task_id')!r} does not "
                        f"match observable binding {task_id!r}"
                    )
                metadata_dependency = metadata.get("depends_on", {})
                if (
                    metadata_dependency.get("model_version")
                    != config_dependency.get("model_version")
                    or metadata_dependency.get("model_checksum")
                    != config_dependency.get("model_checksum")
                ):
                    raise ValueError(
                        f"task {task_id} result-meta model dependency does not match "
                        "the analysis scan-config"
                    )
                if metadata.get("observable") != observable or metadata.get(
                    "return_value", {}
                ).get("name") != observable:
                    raise ValueError(
                        f"task {task_id} result-meta is not bound to observable "
                        f"{observable!r}"
                    )
                source_unit = metadata.get("return_value", {}).get("unit")
                task_parameters = [
                    item
                    for item in metadata.get("parameters", [])
                    if isinstance(item, dict)
                ]
                task_parameter_names = [
                    str(item.get("canonical_name")) for item in task_parameters
                ]
                duplicate_task_parameters = sorted(
                    {
                        name
                        for name in task_parameter_names
                        if task_parameter_names.count(name) > 1
                    }
                )
                if duplicate_task_parameters:
                    raise ValueError(
                        f"task {task_id} result-meta contains duplicate parameters "
                        f"{duplicate_task_parameters}"
                    )
                task_parameter_units = {
                    str(item.get("canonical_name")): item.get("unit")
                    for item in task_parameters
                }
                mismatched_task_parameters = sorted(
                    name
                    for name, unit in task_parameter_units.items()
                    if name not in model_parameter_units
                    or unit != model_parameter_units[name]
                )
                if mismatched_task_parameters:
                    raise ValueError(
                        f"task {task_id} parameter units disagree with model-spec for "
                        f"{mismatched_task_parameters}"
                    )
            elif source_type == "custom":
                source_unit = source.get("canonical_unit")
            else:
                raise ValueError(
                    f"observable {observable!r} has an unsupported binding"
                )
            target_unit = canonical_units.get(observable)
            if not isinstance(source_unit, str) or not source_unit.strip():
                raise ValueError(
                    f"observable {observable!r} source lacks a canonical unit"
                )
            if target_unit != source_unit:
                raise ValueError(
                    f"target {target.get('id')!r} canonical unit for observable "
                    f"{observable!r} is {target_unit!r}; scan source emits "
                    f"{source_unit!r}"
                )


def require_consumable_manifest_analysis(
    manifest: dict[str, Any],
    analysis_id: str,
) -> dict[str, Any]:
    """Return the unique current ``done`` owner for a comparison analysis."""

    if manifest.get("manifest_version") != 2:
        raise ValueError(
            "manifest.json must use manifest_version 2 before a scan can support "
            "a reproduction comparison"
        )
    artifacts = manifest.get("artifacts")
    numerics = artifacts.get("numerics") if isinstance(artifacts, dict) else None
    analyses = numerics.get("analyses") if isinstance(numerics, dict) else None
    if not isinstance(analyses, list) or not all(
        isinstance(item, dict) for item in analyses
    ):
        raise ValueError(
            "manifest.json artifacts.numerics.analyses must be an object array"
        )
    matches = [item for item in analyses if item.get("analysis_id") == analysis_id]
    if len(matches) != 1:
        raise ValueError(
            f"analysis {analysis_id!r} must have exactly one manifest numerics owner "
            f"before comparison; found {len(matches)}"
        )
    analysis = matches[0]
    if analysis.get("status") != "done":
        raise ValueError(
            f"analysis {analysis_id!r} is not consumable for reproduction: manifest "
            f"status is {analysis.get('status')!r}, expected 'done'"
        )
    return analysis


def _model_readiness(
    project_dir: Path,
    manifest: dict[str, Any],
    *,
    repo_root: Path,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    issues: list[dict[str, Any]] = []
    model_path = project_dir / "model" / "model-spec.json"
    tasks_path = project_dir / "model" / "calc-tasks.json"
    loaded: dict[str, dict[str, Any]] = {}
    for path, schema_name, label in (
        (model_path, "model-spec.schema.json", "model-spec"),
        (tasks_path, "calc-tasks.schema.json", "calc-tasks"),
    ):
        relpath = _project_relative(project_dir, path)
        if not path.exists() or not path.is_file():
            issues.append(
                _issue(
                    "artifact_missing",
                    f"required {label} artifact is missing",
                    path=relpath,
                )
            )
            continue
        try:
            payload = load_json(path)
        except (OSError, StrictJSONError) as exc:
            issues.append(
                _issue(
                    "artifact_invalid",
                    f"cannot strict-load {label}: {exc}",
                    path=relpath,
                )
            )
            continue
        if not isinstance(payload, dict):
            issues.append(
                _issue(
                    "artifact_invalid",
                    f"{label} must contain a JSON object",
                    path=relpath,
                )
            )
            continue
        schema_issues = _schema_errors(schema_name, payload, repo_root=repo_root)
        if schema_issues:
            issues.append(
                _issue(
                    "schema_invalid",
                    f"{label} failed schema validation: {'; '.join(schema_issues)}",
                    path=relpath,
                )
            )
            continue
        loaded[label] = payload

    calc_tasks = loaded.get("calc-tasks")
    if isinstance(calc_tasks, dict):
        task_ids = [
            task.get("task_id")
            for task in calc_tasks.get("tasks", [])
            if isinstance(task, dict)
        ]
        duplicates = sorted(
            str(task_id)
            for task_id, count in Counter(task_ids).items()
            if count > 1
        )
        if duplicates:
            issues.append(
                _issue(
                    "duplicate_identifier",
                    f"calc-tasks contains duplicate task ids: {duplicates}",
                    path="model/calc-tasks.json",
                )
            )

    model_spec = loaded.get("model-spec")
    if isinstance(model_spec, dict) and isinstance(calc_tasks, dict):
        model_version = model_spec.get("version")
        artifacts = manifest.get("artifacts")
        manifest_model = (
            artifacts.get("model") if isinstance(artifacts, dict) else None
        )
        actual_checksum = sha256_file(model_path)
        mismatches: list[str] = []
        if calc_tasks.get("model_version") != model_version:
            mismatches.append("calc-tasks.model_version")
        if manifest.get("active_model_version") != model_version:
            mismatches.append("manifest.active_model_version")
        if not isinstance(manifest_model, dict):
            mismatches.append("manifest.artifacts.model")
        else:
            if manifest_model.get("version") != model_version:
                mismatches.append("manifest.artifacts.model.version")
            if manifest_model.get("checksum") != actual_checksum:
                mismatches.append("manifest.artifacts.model.checksum")
        if mismatches:
            issues.append(
                _issue(
                    "model_identity_mismatch",
                    "current model identity disagrees across exact artifacts: "
                    + ", ".join(mismatches),
                    path="model/model-spec.json",
                )
            )

    if not issues:
        return _stage(required=True, status="ready"), calc_tasks
    if any(issue["code"] == "artifact_missing" for issue in issues):
        status = "missing"
    else:
        status = "invalid"
    return _stage(required=True, status=status, issues=issues), calc_tasks


def _target_calculation_readiness(
    project_dir: Path,
    target: dict[str, Any],
    calc_tasks: dict[str, Any] | None,
    *,
    repo_root: Path,
) -> dict[str, Any]:
    if not isinstance(calc_tasks, dict):
        return _calculation_stage(
            required=True,
            status="missing",
            task_ids=[],
            issues=[
                _issue(
                    "artifact_missing",
                    "calculation task catalog is unavailable",
                    path="model/calc-tasks.json",
                )
            ],
        )

    catalog = task_catalog(calc_tasks)
    meta_paths = result_meta_paths(project_dir)
    task_ids, unmatched = resolve_tasks_for_target(target, catalog, meta_paths)
    model_spec_path = project_dir / "model" / "model-spec.json"
    try:
        current_model = load_json(model_spec_path)
        current_model_checksum = sha256_file(model_spec_path)
    except (OSError, StrictJSONError, ValueError):
        current_model = None
        current_model_checksum = None
    issues: list[dict[str, Any]] = [
        _issue(
            "observable_task_unmatched",
            f"target observable {observable!r} has no calculation task",
            path="model/calc-tasks.json",
            fields=[observable],
        )
        for observable in unmatched
    ]
    for task_id in task_ids:
        meta_path = project_dir / "calculations" / task_id / "result-meta.json"
        relpath = _project_relative(project_dir, meta_path)
        if not meta_path.exists() or not meta_path.is_file():
            issues.append(
                _issue(
                    "calculation_result_missing",
                    f"required calculation result is missing for {task_id}",
                    path=relpath,
                )
            )
            continue
        try:
            metadata = load_json(meta_path)
        except (OSError, StrictJSONError) as exc:
            issues.append(
                _issue(
                    "calculation_result_invalid",
                    f"cannot strict-load {task_id} result-meta: {exc}",
                    path=relpath,
                )
            )
            continue
        if not isinstance(metadata, dict):
            issues.append(
                _issue(
                    "calculation_result_invalid",
                    f"{task_id} result-meta must contain a JSON object",
                    path=relpath,
                )
            )
            continue
        schema_issues = _schema_errors(
            "result-meta.schema.json", metadata, repo_root=repo_root
        )
        if schema_issues:
            issues.append(
                _issue(
                    "calculation_result_invalid",
                    f"{task_id} result-meta failed schema validation: "
                    + "; ".join(schema_issues),
                    path=relpath,
                )
            )
            continue
        if metadata.get("task_id") != task_id:
            issues.append(
                _issue(
                    "calculation_result_invalid",
                    f"result-meta task_id {metadata.get('task_id')!r} does not match "
                    f"directory {task_id!r}",
                    path=relpath,
                )
            )
            continue
        task = catalog.get(task_id, {})
        expected_observable = task.get("target_quantity")
        return_value = metadata.get("return_value", {})
        if (
            metadata.get("observable") != expected_observable
            or not isinstance(return_value, dict)
            or return_value.get("name") != expected_observable
        ):
            issues.append(
                _issue(
                    "calculation_result_invalid",
                    f"{task_id} result observable does not match its calc-task "
                    f"target_quantity {expected_observable!r}",
                    path=relpath,
                )
            )
            continue
        model_dependency = metadata.get("depends_on", {})
        if (
            not isinstance(current_model, dict)
            or model_dependency.get("model_version")
            != current_model.get("version")
            or model_dependency.get("model_checksum") != current_model_checksum
        ):
            issues.append(
                _issue(
                    "calculation_result_invalid",
                    f"{task_id} model dependency does not match the current exact "
                    "model identity",
                    path=relpath,
                )
            )
            continue
        try:
            expected = calculation_dependency_specs(
                project_dir,
                repo_root,
                task_id,
                metadata,
            )
        except (OSError, ValueError, TypeError) as exc:
            issues.append(
                _issue(
                    "calculation_result_invalid",
                    f"cannot derive {task_id} dependency coverage: {exc}",
                    path=relpath,
                )
            )
            continue
        dependency_issues = verify_dependency_graph(
            metadata.get("input_provenance"),
            project_dir,
            repo_root,
            expected_specs=expected,
        )
        if dependency_issues:
            issues.append(
                _issue(
                    "calculation_dependency_stale",
                    f"{task_id} dependency graph is stale or incomplete: "
                    + "; ".join(dependency_issues),
                    path=relpath,
                )
            )

    if not issues:
        return _calculation_stage(
            required=True,
            status="ready",
            task_ids=task_ids,
        )
    codes = {str(issue["code"]) for issue in issues}
    if "calculation_result_invalid" in codes:
        status = "invalid"
    elif "calculation_dependency_stale" in codes:
        status = "stale"
    else:
        status = "missing"
    return _calculation_stage(
        required=True,
        status=status,
        task_ids=task_ids,
        issues=issues,
    )


def _scan_hint_map(paper_extract: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw_hints = paper_extract.get("scan_config_hints")
    if not isinstance(raw_hints, list):
        raise ValueError("paper-extract.scan_config_hints must be an array")
    hints: dict[str, dict[str, Any]] = {}
    for index, raw_hint in enumerate(raw_hints):
        if not isinstance(raw_hint, dict):
            raise ValueError(f"paper-extract scan hint {index} must be an object")
        target_id = raw_hint.get("target_id")
        if not isinstance(target_id, str) or not target_id:
            raise ValueError(
                f"paper-extract scan hint {index} requires a nonempty target_id"
            )
        if target_id in hints:
            raise ValueError(
                f"paper-extract contains duplicate scan hints for {target_id!r}"
            )
        missing_fields = raw_hint.get("missing_fields")
        if not isinstance(missing_fields, list):
            raise ValueError(
                f"paper-extract scan hint {target_id!r} missing_fields must be an array"
            )
        hints[target_id] = raw_hint
    return hints


def _manifest_analysis_integrity_issues(
    project_dir: Path,
    manifest: dict[str, Any],
    analysis: dict[str, Any],
    analysis_id: str,
    scan_config: dict[str, Any],
    scan_meta: dict[str, Any],
) -> list[dict[str, Any]]:
    """Verify selected per-analysis ownership and dependency projections."""

    issues: list[dict[str, Any]] = []
    artifacts = manifest.get("artifacts", {})
    numerics = artifacts.get("numerics", {}) if isinstance(artifacts, dict) else {}
    analyses = numerics.get("analyses", []) if isinstance(numerics, dict) else []
    analysis_files = analysis.get("files", [])
    if not isinstance(analysis_files, list) or not all(
        isinstance(item, str) for item in analysis_files
    ):
        return [
            _issue(
                "manifest_analysis_invalid",
                f"analysis {analysis_id!r} files must be a string array",
                path="manifest.json",
            )
        ]

    if analysis_files != sorted(analysis_files):
        issues.append(
            _issue(
                "manifest_analysis_invalid",
                f"analysis {analysis_id!r} files must be sorted",
                path="manifest.json",
            )
        )
    required_relpaths = {
        f"numerics/scan-configs/{analysis_id}.json",
        f"numerics/scan-results/{analysis_id}/scan.csv",
        f"numerics/scan-results/{analysis_id}/scan.meta.json",
        f"numerics/analysis-summary-{analysis_id}.md",
    }
    missing_owned = sorted(required_relpaths - set(analysis_files))
    if missing_owned:
        issues.append(
            _issue(
                "manifest_analysis_invalid",
                f"analysis {analysis_id!r} does not own required files {missing_owned}",
                path="manifest.json",
                fields=missing_owned,
            )
        )
    for relpath in sorted(set(analysis_files)):
        try:
            owned_path = resolve_contained(
                project_dir,
                relpath,
                f"analysis {analysis_id!r} owned file",
            )
        except ValueError as exc:
            issues.append(
                _issue(
                    "manifest_analysis_invalid",
                    str(exc),
                    path="manifest.json",
                    fields=[relpath],
                )
            )
            continue
        if not owned_path.exists() or not owned_path.is_file():
            issues.append(
                _issue(
                    "manifest_analysis_invalid",
                    f"analysis {analysis_id!r} owns missing file {relpath!r}",
                    path="manifest.json",
                    fields=[relpath],
                )
            )

    if isinstance(analyses, list):
        expected_aggregate = sorted(
            {
                relpath
                for item in analyses
                if isinstance(item, dict)
                for relpath in item.get("files", [])
                if isinstance(relpath, str)
            }
        )
        if numerics.get("files") != expected_aggregate:
            issues.append(
                _issue(
                    "manifest_analysis_invalid",
                    "manifest numerics aggregate files do not equal the sorted "
                    "union of per-analysis ownership",
                    path="manifest.json",
                )
            )

    snapshot = scan_meta.get("scan_config_snapshot")
    snapshot_dependencies = (
        snapshot.get("depends_on", {}) if isinstance(snapshot, dict) else {}
    )
    declared_dependencies = analysis.get("depends_on", {})
    if not isinstance(declared_dependencies, dict):
        declared_dependencies = {}
    expected_model = {
        "version": snapshot_dependencies.get("model_version"),
        "checksum": snapshot_dependencies.get("model_checksum"),
    }
    if declared_dependencies.get("model") != expected_model:
        issues.append(
            _issue(
                "manifest_analysis_invalid",
                f"analysis {analysis_id!r} model ownership does not match its "
                "immutable scan snapshot",
                path="manifest.json",
            )
        )
    snapshot_tasks = snapshot_dependencies.get("task_ids", [])
    expected_calculations = {
        "tasks": sorted(set(snapshot_tasks))
        if isinstance(snapshot_tasks, list)
        and all(isinstance(item, str) for item in snapshot_tasks)
        else None,
        "model_version": snapshot_dependencies.get("model_version"),
    }
    if (
        expected_calculations["tasks"] is None
        or declared_dependencies.get("calculations") != expected_calculations
    ):
        issues.append(
            _issue(
                "manifest_analysis_invalid",
                f"analysis {analysis_id!r} calculation ownership does not match "
                "its immutable scan snapshot",
                path="manifest.json",
            )
        )

    graph = scan_meta.get("input_provenance", {})
    entries = graph.get("entries", []) if isinstance(graph, dict) else []
    recorded_constraints = [
        entry.get("sha256")
        for entry in entries
        if isinstance(entry, dict)
        and entry.get("scope") == "project"
        and entry.get("role") == "constraints-data"
        and entry.get("path") == "constraints/constraints-data.json"
    ]
    declared_constraints = declared_dependencies.get("constraints")
    declared_checksum = (
        declared_constraints.get("checksum")
        if isinstance(declared_constraints, dict)
        else None
    )
    if len(recorded_constraints) != 1 or declared_checksum != recorded_constraints[0]:
        issues.append(
            _issue(
                "manifest_analysis_invalid",
                f"analysis {analysis_id!r} constraints ownership does not "
                "match its recorded scan graph",
                path="manifest.json",
            )
        )

    if scan_config.get("analysis_id") != analysis_id:
        issues.append(
            _issue(
                "manifest_analysis_invalid",
                f"analysis {analysis_id!r} owns a scan config with a different id",
                path="manifest.json",
            )
        )
    return issues


def _target_numerics_readiness(
    project_dir: Path,
    manifest: dict[str, Any],
    target: dict[str, Any],
    hint: dict[str, Any] | None,
    analysis_id: str,
    *,
    repo_root: Path,
) -> dict[str, Any]:
    target_id = str(target.get("id"))
    if hint is None:
        return _stage(
            required=True,
            status="blocked",
            issues=[
                _issue(
                    "scan_hint_missing",
                    "paper-extract has no scan_config_hint for this numeric target",
                    path="literature/paper-extract.json",
                )
            ],
        )
    missing_fields = [str(item) for item in hint.get("missing_fields", [])]
    if missing_fields:
        return _stage(
            required=True,
            status="blocked",
            issues=[
                _issue(
                    "scan_hint_incomplete",
                    "paper-extract scan hint is incomplete",
                    path="literature/paper-extract.json",
                    fields=missing_fields,
                )
            ],
        )

    config_path = project_dir / "numerics" / "scan-configs" / f"{analysis_id}.json"
    result_dir = project_dir / "numerics" / "scan-results" / analysis_id
    csv_path = result_dir / "scan.csv"
    meta_path = result_dir / "scan.meta.json"
    summary_path = project_dir / "numerics" / f"analysis-summary-{analysis_id}.md"
    required_paths = (
        (config_path, "scan config"),
        (csv_path, "scan CSV"),
        (meta_path, "scan metadata"),
        (summary_path, "analysis summary"),
    )
    missing_issues = [
        _issue(
            "artifact_missing",
            f"required {label} is missing",
            path=_project_relative(project_dir, path),
        )
        for path, label in required_paths
        if not path.exists() or not path.is_file()
    ]
    if missing_issues:
        return _stage(
            required=True,
            status="missing",
            issues=missing_issues,
        )

    artifacts = manifest.get("artifacts")
    numerics = artifacts.get("numerics") if isinstance(artifacts, dict) else None
    analyses = numerics.get("analyses") if isinstance(numerics, dict) else None
    matches = (
        [item for item in analyses if item.get("analysis_id") == analysis_id]
        if isinstance(analyses, list)
        and all(isinstance(item, dict) for item in analyses)
        else []
    )
    if not matches:
        return _stage(
            required=True,
            status="missing",
            issues=[
                _issue(
                    "manifest_analysis_missing",
                    f"analysis {analysis_id!r} has no manifest numerics owner",
                    path="manifest.json",
                )
            ],
        )
    if len(matches) != 1:
        return _stage(
            required=True,
            status="invalid",
            issues=[
                _issue(
                    "manifest_analysis_invalid",
                    f"analysis {analysis_id!r} has {len(matches)} manifest owners",
                    path="manifest.json",
                )
            ],
        )
    analysis = matches[0]
    if analysis.get("status") != "done":
        status = "stale" if analysis.get("status") == "stale" else "missing"
        return _stage(
            required=True,
            status=status,
            issues=[
                _issue(
                    "manifest_analysis_not_done",
                    f"analysis {analysis_id!r} manifest status is "
                    f"{analysis.get('status')!r}, expected 'done'",
                    path="manifest.json",
                )
            ],
        )

    try:
        scan_config = load_json(config_path)
        scan_meta = load_json(meta_path)
    except (OSError, StrictJSONError) as exc:
        return _stage(
            required=True,
            status="invalid",
            issues=[
                _issue(
                    "scan_artifact_invalid",
                    f"cannot strict-load scan artifacts: {exc}",
                )
            ],
        )
    schema_issues: list[str] = []
    schema_issues.extend(
        "scan-config: " + message
        for message in _schema_errors(
            "scan-config.schema.json", scan_config, repo_root=repo_root
        )
    )
    schema_issues.extend(
        "scan-meta: " + message
        for message in _schema_errors(
            "scan-meta.schema.json", scan_meta, repo_root=repo_root
        )
    )
    if schema_issues:
        return _stage(
            required=True,
            status="invalid",
            issues=[
                _issue(
                    "schema_invalid",
                    "scan artifacts failed schema validation: "
                    + "; ".join(schema_issues),
                )
            ],
        )

    ownership_issues = _manifest_analysis_integrity_issues(
        project_dir,
        manifest,
        analysis,
        analysis_id,
        scan_config,
        scan_meta,
    )
    if ownership_issues:
        return _stage(
            required=True,
            status="invalid",
            issues=ownership_issues,
        )

    try:
        validate_target_scan_parameters(
            project_dir,
            analysis_id,
            [target],
            set(),
            repo_root=repo_root,
        )
    except (OSError, ValueError, TypeError, StrictJSONError) as exc:
        return _stage(
            required=True,
            status="invalid",
            issues=[
                _issue(
                    "scan_target_mismatch",
                    f"analysis does not satisfy target {target_id!r}: {exc}",
                    path=_project_relative(project_dir, config_path),
                )
            ],
        )

    pair_issues = validate_scan_artifact_pair(
        project_dir,
        analysis_id,
        config_path,
        repo_root,
    )
    if pair_issues:
        return _stage(
            required=True,
            status="invalid",
            issues=[
                _issue(
                    "scan_artifact_invalid",
                    "scan artifact pair is invalid: " + "; ".join(pair_issues),
                )
            ],
        )
    try:
        producer_script = scan_producer_from_graph(
            scan_meta.get("input_provenance", {}),
            repo_root,
        )
        scan_snapshot = scan_meta.get("scan_config_snapshot")
        scan_config_source = scan_meta.get("scan_config_source")
        if not isinstance(scan_snapshot, dict) or not isinstance(
            scan_config_source, str
        ):
            raise ValueError(
                "scan metadata lacks immutable config source/snapshot provenance"
            )
        expected_scan_dependencies = scan_dependency_specs(
            project_dir,
            repo_root,
            config_path,
            scan_snapshot,
            producer_script=producer_script,
        )
        provenance_issues = verify_frozen_scan_dependency_graph(
            scan_meta.get("input_provenance"),
            project_dir,
            repo_root,
            expected_scan_dependencies,
            scan_config_source=scan_config_source,
        )
    except (OSError, ValueError, TypeError) as exc:
        provenance_issues = [str(exc)]
    if provenance_issues:
        return _stage(
            required=True,
            status="stale",
            issues=[
                _issue(
                    "scan_dependency_stale",
                    "scan dependency graph is stale or incomplete: "
                    + "; ".join(provenance_issues),
                    path=_project_relative(project_dir, meta_path),
                )
            ],
        )
    return _stage(required=True, status="ready")


def readiness_semantic_errors(payload: dict[str, Any]) -> list[str]:
    """Return cross-field errors not expressible compactly in JSON Schema."""

    errors: list[str] = []
    targets = payload.get("targets")
    if not isinstance(targets, list):
        return ["targets must be an array"]
    target_ids = [
        item.get("target_id") for item in targets if isinstance(item, dict)
    ]
    duplicates = sorted(
        str(target_id)
        for target_id, count in Counter(target_ids).items()
        if count > 1
    )
    if duplicates:
        errors.append(f"targets contain duplicate target_id values: {duplicates}")

    counts = Counter(
        str(item.get("disposition"))
        for item in targets
        if isinstance(item, dict)
    )
    expected_summary = {
        "total": len(targets),
        "ready": counts["ready"],
        "blocked": counts["blocked"],
        "not_ready": counts["not_ready"],
    }
    if payload.get("summary") != expected_summary:
        errors.append("summary does not exactly match target dispositions")
    expected_workflow_state = (
        "not_ready" if counts["not_ready"] else "routable"
    )
    if payload.get("workflow_state") != expected_workflow_state:
        errors.append(
            "workflow_state does not match the presence of not_ready targets"
        )

    for index, target in enumerate(targets):
        if not isinstance(target, dict):
            continue
        prefix = f"targets.{index}"
        requirements = target.get("requirements")
        if not isinstance(requirements, dict):
            continue
        kind = target.get("kind")
        literature = requirements.get("literature")
        model = requirements.get("model")
        calculations = requirements.get("calculations")
        numerics = requirements.get("numerics")
        stages = [literature, model, calculations, numerics]
        if not all(isinstance(stage, dict) for stage in stages):
            continue
        assert isinstance(literature, dict)
        assert isinstance(model, dict)
        assert isinstance(calculations, dict)
        assert isinstance(numerics, dict)
        if literature.get("required") is not True:
            errors.append(f"{prefix}.literature must always be required")
        if literature.get("status") == "not_applicable":
            errors.append(f"{prefix}.literature cannot be not_applicable")
        if kind == FORMULA_KIND:
            for name, stage in (
                ("model", model),
                ("calculations", calculations),
                ("numerics", numerics),
            ):
                if stage.get("required") is not False or stage.get(
                    "status"
                ) != "not_applicable":
                    errors.append(
                        f"{prefix}.{name} must be not_applicable for formula targets"
                    )
        else:
            for name, stage in (
                ("model", model),
                ("calculations", calculations),
                ("numerics", numerics),
            ):
                if stage.get("required") is not True:
                    errors.append(
                        f"{prefix}.{name} must be required for numeric targets"
                    )
                if stage.get("status") == "not_applicable":
                    errors.append(
                        f"{prefix}.{name} cannot be not_applicable for numeric targets"
                    )
            if calculations.get("status") == "ready" and not calculations.get(
                "task_ids"
            ):
                errors.append(
                    f"{prefix}.calculations ready requires at least one task_id"
                )

        required_statuses = [
            str(stage.get("status"))
            for stage in stages
            if stage.get("required") is True
        ]
        if any(status in NOT_READY_STAGE_STATUSES for status in required_statuses):
            expected_disposition = "not_ready"
        elif numerics.get("status") == "blocked":
            expected_disposition = "blocked"
        else:
            expected_disposition = "ready"
        if target.get("disposition") != expected_disposition:
            errors.append(
                f"{prefix}.disposition does not match required stage statuses"
            )
        for name, stage in (
            ("literature", literature),
            ("model", model),
            ("calculations", calculations),
        ):
            if stage.get("status") == "blocked":
                errors.append(f"{prefix}.{name} cannot use blocked status")
    return errors


def readiness_validation_errors(
    payload: dict[str, Any],
    *,
    repo_root: Path = REPO_ROOT,
) -> list[str]:
    return [
        *_schema_errors(
            "reproduction-readiness.schema.json",
            payload,
            repo_root=repo_root,
        ),
        *readiness_semantic_errors(payload),
    ]


def derive_reproduction_readiness(
    project_dir: str | Path,
    analysis_id: str,
    *,
    target_id: str | None = None,
    repo_root: str | Path = REPO_ROOT,
    reference_validator: ReferenceValidator,
) -> dict[str, Any]:
    """Derive one deterministic readiness report without filesystem writes."""

    repository = Path(repo_root).resolve()
    project = Path(project_dir).resolve()
    if not project.exists() or not project.is_dir():
        raise ValueError(f"project directory does not exist: {project}")
    canonical_analysis_id = validate_analysis_id(analysis_id, "analysis-id")
    manifest = _load_required_object(
        project / "manifest.json",
        "manifest.schema.json",
        repo_root=repository,
        label="manifest.json",
    )
    repro_targets = _load_required_object(
        project / "literature" / "repro-targets.json",
        "repro-targets.schema.json",
        repo_root=repository,
        label="literature/repro-targets.json",
    )
    paper_extract = _load_required_object(
        project / "literature" / "paper-extract.json",
        "paper-extract.schema.json",
        repo_root=repository,
        label="literature/paper-extract.json",
    )
    if repro_targets.get("paper_id") != paper_extract.get("paper_id"):
        raise ValueError(
            "literature/repro-targets.json paper_id does not match "
            "literature/paper-extract.json"
        )
    raw_targets = [
        item for item in repro_targets.get("targets", []) if isinstance(item, dict)
    ]
    ids = [target.get("id") for target in raw_targets]
    duplicates = sorted(
        str(item)
        for item, count in Counter(ids).items()
        if count > 1
    )
    if duplicates:
        raise ValueError(f"repro-targets contains duplicate target ids: {duplicates}")
    targets = sorted(raw_targets, key=lambda item: str(item.get("id", "")))
    if target_id is not None:
        targets = [target for target in targets if target.get("id") == target_id]
        if not targets:
            raise ValueError(
                f"target-id not found in literature/repro-targets.json: {target_id}"
            )
    hints = _scan_hint_map(paper_extract)
    numeric_targets = [
        target for target in targets if target.get("kind") != FORMULA_KIND
    ]
    if numeric_targets:
        model_stage, calc_tasks = _model_readiness(
            project,
            manifest,
            repo_root=repository,
        )
    else:
        model_stage = _not_applicable_stage()
        calc_tasks = None

    target_reports: list[dict[str, Any]] = []
    for target in targets:
        reference_issues: list[dict[str, Any]] = []
        try:
            reference_validator(
                project,
                target,
                str(repro_targets.get("paper_id", "")),
            )
        except (OSError, ValueError, TypeError, StrictJSONError) as exc:
            reference_issues.append(
                _issue(
                    "reference_evidence_invalid",
                    str(exc),
                    path=str(target.get("data_file", "literature/repro-targets.json")),
                )
            )
        literature_stage = (
            _stage(required=True, status="ready")
            if not reference_issues
            else _stage(
                required=True,
                status="invalid",
                issues=reference_issues,
            )
        )

        kind = str(target.get("kind"))
        if kind == FORMULA_KIND:
            requirements = {
                "literature": literature_stage,
                "model": _not_applicable_stage(),
                "calculations": _not_applicable_calculation_stage(),
                "numerics": _not_applicable_stage(),
            }
        else:
            calculations_stage = _target_calculation_readiness(
                project,
                target,
                calc_tasks,
                repo_root=repository,
            )
            numerics_stage = _target_numerics_readiness(
                project,
                manifest,
                target,
                hints.get(str(target.get("id"))),
                canonical_analysis_id,
                repo_root=repository,
            )
            requirements = {
                "literature": literature_stage,
                "model": model_stage,
                "calculations": calculations_stage,
                "numerics": numerics_stage,
            }

        required_statuses = [
            str(stage["status"])
            for stage in requirements.values()
            if stage["required"] is True
        ]
        if any(status in NOT_READY_STAGE_STATUSES for status in required_statuses):
            disposition = "not_ready"
        elif requirements["numerics"]["status"] == "blocked":
            disposition = "blocked"
        else:
            disposition = "ready"
        target_reports.append(
            {
                "target_id": str(target["id"]),
                "kind": kind,
                "disposition": disposition,
                "requirements": requirements,
            }
        )

    counts = Counter(item["disposition"] for item in target_reports)
    report = {
        "schema_version": 1,
        "analysis_id": canonical_analysis_id,
        "workflow_state": "not_ready" if counts["not_ready"] else "routable",
        "summary": {
            "total": len(target_reports),
            "ready": counts["ready"],
            "blocked": counts["blocked"],
            "not_ready": counts["not_ready"],
        },
        "targets": target_reports,
    }
    output_errors = readiness_validation_errors(report, repo_root=repository)
    if output_errors:
        raise ValueError(
            "derived reproduction readiness failed its own contract: "
            + "; ".join(output_errors)
        )
    return report


__all__ = [
    "_same_json_scalar",
    "derive_reproduction_readiness",
    "readiness_semantic_errors",
    "readiness_validation_errors",
    "require_consumable_manifest_analysis",
    "resolve_tasks_for_target",
    "result_meta_paths",
    "task_catalog",
    "validate_target_scan_parameters",
]
