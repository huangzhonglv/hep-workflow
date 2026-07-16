"""Semantic validation for persisted reproduction results.

JSON Schema validates the shape of these artifacts.  This module validates
cross-field honesty invariants and, when a project directory is supplied, the
declared generated-file evidence.
"""

from __future__ import annotations

import math
import hashlib
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from _dependency_graph import verify_dependency_graph
    from _workflow_dependencies import (
        reproduction_dependency_specs,
        reproduction_scan_required_target_ids,
    )
    from _strict_json import load_json
except ModuleNotFoundError:  # Imported as scripts._reproduction_result_validation.
    from scripts._dependency_graph import verify_dependency_graph
    from scripts._workflow_dependencies import (
        reproduction_dependency_specs,
        reproduction_scan_required_target_ids,
    )
    from scripts._strict_json import load_json


INDEPENDENCE_ORDER = {
    "independent": 0,
    "independent_manual": 1,
    "unknown": 2,
    "tainted": 3,
}

RELATIVE_EVIDENCE_FIELDS = {
    "n_zero_reference_values",
    "n_zero_reference_crossings",
    "relative_error_defined",
}

INDEPENDENT_ACQUISITION_TYPES = {
    "paper_figure",
    "paper_table",
    "supplemental_data",
    "author_data",
    "trusted_repository",
}


def expected_evidence_axes(target: dict[str, Any]) -> tuple[str, str]:
    """Derive the honesty evidence axes from one current repro target."""

    kind = target.get("kind")
    if kind == "formula":
        return "unverified", "requires_human_review"
    if not isinstance(kind, str):
        raise ValueError("repro target kind is missing or invalid")

    normalization = target.get("normalization")
    acquisition = (
        normalization.get("acquisition")
        if isinstance(normalization, dict)
        else None
    )
    source_type = acquisition.get("source_type") if isinstance(acquisition, dict) else None
    if source_type == "synthetic_fixture":
        reference_evidence = "synthetic"
    elif source_type in INDEPENDENT_ACQUISITION_TYPES:
        reference_evidence = "independent_snapshot"
    else:
        raise ValueError(
            f"repro target acquisition source_type is missing or invalid: {source_type!r}"
        )

    boundary_mode = target.get("boundary", {}).get("mode")
    comparison_evidence = (
        "requires_human_review"
        if kind == "exclusion_region"
        and boundary_mode in {
            "precomputed_boundary",
            "constraint_verdict_transition",
        }
        else "machine_verifiable"
    )
    return reference_evidence, comparison_evidence


def _sha256(path: Path) -> str:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return f"sha256:{digest}"


def _current_model_projection_errors(
    depends_on: dict[str, Any],
    scientific_root: Path,
) -> list[str]:
    """Verify the mutable manifest's active-model projection against exact bytes."""

    manifest_path = scientific_root / "manifest.json"
    model_path = scientific_root / "model" / "model-spec.json"
    try:
        manifest = load_json(manifest_path)
    except (OSError, ValueError) as exc:
        return [f"current model projection cannot strict-load manifest.json: {exc}"]
    try:
        model_spec = load_json(model_path)
    except (OSError, ValueError) as exc:
        return [f"current model projection cannot strict-load model-spec.json: {exc}"]
    if not isinstance(manifest, dict):
        return ["current model projection requires manifest.json to contain an object"]
    if not isinstance(model_spec, dict):
        return ["current model projection requires model-spec.json to contain an object"]

    model_version = model_spec.get("version")
    if not isinstance(model_version, str) or not model_version:
        return ["current model projection requires a nonempty model-spec.version"]
    model_checksum = _sha256(model_path)
    payload_model = depends_on.get("model")
    if not isinstance(payload_model, dict):
        payload_model = {}
    artifacts = manifest.get("artifacts")
    manifest_model = artifacts.get("model") if isinstance(artifacts, dict) else None
    if not isinstance(manifest_model, dict):
        manifest_model = {}

    checks = (
        ("depends_on.model.version", payload_model.get("version"), model_version),
        ("depends_on.model.checksum", payload_model.get("checksum"), model_checksum),
        ("manifest.active_model_version", manifest.get("active_model_version"), model_version),
        ("manifest.artifacts.model.version", manifest_model.get("version"), model_version),
        (
            "manifest.artifacts.model.checksum",
            manifest_model.get("checksum"),
            model_checksum,
        ),
    )
    return [
        f"{label} does not match current model-spec exact identity"
        for label, actual, expected in checks
        if actual != expected
    ]


def _format_signature_error(path: Path, extension: str) -> str | None:
    prefix = path.read_bytes()[:8]
    if extension == "pdf" and not prefix.startswith(b"%PDF-"):
        return "file does not have a PDF signature"
    if extension == "png" and prefix != b"\x89PNG\r\n\x1a\n":
        return "file does not have a PNG signature"
    return None


def _contained_file(
    project_dir: Path,
    relpath: object,
    allowed_root: Path,
) -> str | None:
    if not isinstance(relpath, str) or not relpath:
        return "path must be a non-empty string"
    candidate = Path(relpath)
    if candidate.is_absolute():
        return "path must be project-relative"
    project_root = project_dir.resolve()
    allowed = allowed_root.resolve()
    resolved = (project_root / candidate).resolve()
    try:
        allowed_label = allowed_root.relative_to(project_dir).as_posix()
    except ValueError:
        allowed_label = allowed_root.as_posix()
    if not allowed.is_relative_to(project_root):
        return f"allowed evidence root escapes the project: {allowed_label}"
    if not resolved.is_relative_to(allowed):
        return f"path escapes {allowed_label}"
    if not resolved.exists() or not resolved.is_file():
        return "declared file does not exist"
    if resolved.stat().st_size <= 0:
        return "declared file is empty"
    return None


def _metric_contract_errors(
    metrics: dict[str, Any],
    *,
    kind: object,
    verdict: object,
    prefix: str,
    tolerance: object = None,
) -> list[str]:
    errors: list[str] = []
    if kind == "formula":
        return [] if not metrics else [f"{prefix}.formula comparison metrics must be empty"]
    if verdict == "blocked" and not metrics:
        return []

    required_by_kind = {
        "benchmark_point": {
            "relative_error",
            "max_relative_error",
            "rms_relative_error",
            "n_points_compared",
            *RELATIVE_EVIDENCE_FIELDS,
        },
        "keyed_benchmark_set": {
            "max_relative_error",
            "rms_relative_error",
            "n_points_compared",
            "missing_rows",
            *RELATIVE_EVIDENCE_FIELDS,
        },
        "scan_table": {
            "max_relative_error",
            "rms_relative_error",
            "n_points_compared",
            "missing_rows",
            *RELATIVE_EVIDENCE_FIELDS,
        },
        "figure_curve": {
            "max_relative_error",
            "rms_relative_error",
            "max_absolute_error",
            "n_points_compared",
            "declared_x_min",
            "declared_x_max",
            "reference_x_min",
            "reference_x_max",
            "scan_x_min",
            "scan_x_max",
            "reference_domain_coverage",
            "scan_domain_coverage",
            "reference_node_count",
            "scan_node_count",
            *RELATIVE_EVIDENCE_FIELDS,
        },
        "parametric_curve": {
            "max_normalized_hausdorff_distance",
            "max_normalized_hausdorff_distance_lower_bound",
            "max_normalized_hausdorff_distance_upper_bound",
            "max_normalized_hausdorff_distance_uncertainty",
            "reference_to_predicted_max_normalized_distance",
            "reference_to_predicted_max_normalized_distance_lower_bound",
            "predicted_to_reference_max_normalized_distance",
            "predicted_to_reference_max_normalized_distance_lower_bound",
            "normalized_bbox_iou",
            "n_points_compared",
            "reference_node_count",
            "scan_node_count",
            "declared_parameter_min",
            "declared_parameter_max",
            "reference_parameter_min",
            "reference_parameter_max",
            "scan_parameter_min",
            "scan_parameter_max",
            "reference_domain_coverage",
            "scan_domain_coverage",
            "closed_topology_match",
            "distance_within_tolerance_proven",
            "distance_exceeds_tolerance_proven",
            "distance_decision_defined",
            "polyline_sampling_max_gap",
            "polyline_sampling_error_bound",
            "polyline_sample_count",
        },
        "exclusion_region": {
            "max_normalized_hausdorff_distance",
            "normalized_bbox_iou",
            "n_points_compared",
            "n_reference_boundary_points",
            "n_predicted_boundary_points",
            "reference_to_predicted_max_normalized_distance",
            "predicted_to_reference_max_normalized_distance",
            "reference_component_count",
            "predicted_component_count",
            "matched_component_count",
            "component_count_match",
            "closed_topology_match",
            "reference_face_count",
            "verified_face_probe_count",
            "face_assignment_defined",
            "face_parent_topology_match",
            "face_probe_coverage_ratio",
            "component_coverage_ratio",
            "excluded_probe_match",
            "max_component_normalized_hausdorff_distance",
            "max_normalized_hausdorff_distance_lower_bound",
            "max_normalized_hausdorff_distance_upper_bound",
            "max_normalized_hausdorff_distance_uncertainty",
            "reference_to_predicted_max_normalized_distance_lower_bound",
            "predicted_to_reference_max_normalized_distance_lower_bound",
            "distance_within_tolerance_proven",
            "distance_exceeds_tolerance_proven",
            "distance_decision_defined",
            "polyline_sampling_max_gap",
            "polyline_sampling_error_bound",
            "polyline_sample_count",
        },
    }
    required = required_by_kind.get(kind) if isinstance(kind, str) else None
    if required is None:
        return [f"{prefix} has unsupported comparison kind {kind!r}"]
    missing = sorted(required - set(metrics))
    if missing:
        errors.append(f"{prefix} is missing required metrics: {missing}")

    for key, value in metrics.items():
        metric_path = f"{prefix}.{key}"
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            errors.append(f"{metric_path} must be a finite numeric value")
        elif not math.isfinite(float(value)):
            errors.append(f"{metric_path} is non-finite")

    count_keys = {"n_points_compared"}
    if kind == "exclusion_region":
        count_keys.update(
            {
                "n_reference_boundary_points",
                "n_predicted_boundary_points",
                "reference_component_count",
                "predicted_component_count",
                "reference_face_count",
            }
        )
        count_keys.add("polyline_sample_count")
    if kind == "parametric_curve":
        count_keys.update(
            {"reference_node_count", "scan_node_count", "polyline_sample_count"}
        )
    if kind == "figure_curve":
        count_keys.update({"reference_node_count", "scan_node_count"})
    for key in sorted(count_keys & set(metrics)):
        value = metrics[key]
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or value <= 0
        ):
            errors.append(f"{prefix}.{key} must be a positive integer")
    if kind in {"scan_table", "keyed_benchmark_set"} and metrics.get(
        "missing_rows"
    ) != 0:
        errors.append(f"{prefix}.missing_rows must equal 0 for a non-blocked verdict")
    non_negative_keys = {
        key
        for key in metrics
        if "error" in key or "distance" in key
    }
    for key in sorted(non_negative_keys):
        value = metrics.get(key)
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and float(value) < 0
        ):
            errors.append(f"{prefix}.{key} must be non-negative")
    max_relative = metrics.get("max_relative_error")
    rms_relative = metrics.get("rms_relative_error")
    if all(
        isinstance(value, (int, float)) and not isinstance(value, bool)
        for value in (max_relative, rms_relative)
    ) and float(rms_relative) > float(max_relative):
        errors.append(f"{prefix}.rms_relative_error cannot exceed max_relative_error")
    if kind not in {"exclusion_region", "parametric_curve"}:
        zero_count = metrics.get("n_zero_reference_values")
        zero_crossings = metrics.get("n_zero_reference_crossings")
        relative_defined = metrics.get("relative_error_defined")
        n_points = metrics.get("n_points_compared")
        if (
            isinstance(zero_count, bool)
            or not isinstance(zero_count, int)
            or not isinstance(n_points, int)
            or not 0 <= zero_count <= n_points
        ):
            errors.append(
                f"{prefix}.n_zero_reference_values must be an integer in [0, n_points_compared]"
            )
        if (
            isinstance(zero_crossings, bool)
            or not isinstance(zero_crossings, int)
            or zero_crossings < 0
        ):
            errors.append(
                f"{prefix}.n_zero_reference_crossings must be a non-negative integer"
            )
        if relative_defined not in (0, 1):
            errors.append(f"{prefix}.relative_error_defined must equal 0 or 1")
        elif (
            isinstance(zero_count, int)
            and isinstance(zero_crossings, int)
            and relative_defined != int(zero_count == 0 and zero_crossings == 0)
        ):
            errors.append(
                f"{prefix}.relative_error_defined contradicts n_zero_reference_values"
            )
    if kind == "exclusion_region":
        distance = metrics.get("max_normalized_hausdorff_distance")
        if isinstance(distance, (int, float)) and not isinstance(distance, bool) and distance < 0:
            errors.append(
                f"{prefix}.max_normalized_hausdorff_distance must be non-negative"
            )
        iou = metrics.get("normalized_bbox_iou")
        if (
            isinstance(iou, (int, float))
            and not isinstance(iou, bool)
            and not 0 <= float(iou) <= 1
        ):
            errors.append(f"{prefix}.normalized_bbox_iou must be in [0, 1]")
        for key in (
            "component_count_match",
            "closed_topology_match",
            "face_assignment_defined",
            "face_parent_topology_match",
            "excluded_probe_match",
            "distance_within_tolerance_proven",
            "distance_exceeds_tolerance_proven",
            "distance_decision_defined",
        ):
            if metrics.get(key) not in (0, 1):
                errors.append(f"{prefix}.{key} must equal 0 or 1")
        within = metrics.get("distance_within_tolerance_proven")
        exceeds = metrics.get("distance_exceeds_tolerance_proven")
        decision = metrics.get("distance_decision_defined")
        if all(value in (0, 1) for value in (within, exceeds, decision)) and (
            within + exceeds != decision
        ):
            errors.append(f"{prefix}.distance decision flags are inconsistent")
        coverage = metrics.get("component_coverage_ratio")
        if (
            isinstance(coverage, (int, float))
            and not isinstance(coverage, bool)
            and not 0 <= float(coverage) <= 1
        ):
            errors.append(f"{prefix}.component_coverage_ratio must be in [0, 1]")
        face_coverage = metrics.get("face_probe_coverage_ratio")
        if (
            isinstance(face_coverage, (int, float))
            and not isinstance(face_coverage, bool)
            and not 0 <= float(face_coverage) <= 1
        ):
            errors.append(f"{prefix}.face_probe_coverage_ratio must be in [0, 1]")
        reference_faces = metrics.get("reference_face_count")
        verified_probes = metrics.get("verified_face_probe_count")
        if (
            isinstance(reference_faces, bool)
            or not isinstance(reference_faces, int)
            or reference_faces <= 0
            or isinstance(verified_probes, bool)
            or not isinstance(verified_probes, int)
            or not 0 <= verified_probes <= reference_faces
        ):
            errors.append(f"{prefix}.verified_face_probe_count is inconsistent")
        elif isinstance(face_coverage, (int, float)) and not isinstance(
            face_coverage, bool
        ) and float(face_coverage) != verified_probes / reference_faces:
            errors.append(f"{prefix}.face_probe_coverage_ratio is arithmetically inconsistent")
        reference_components = metrics.get("reference_component_count")
        predicted_components = metrics.get("predicted_component_count")
        matched_components = metrics.get("matched_component_count")
        component_counts_valid = (
            isinstance(reference_components, int)
            and not isinstance(reference_components, bool)
            and reference_components > 0
            and isinstance(predicted_components, int)
            and not isinstance(predicted_components, bool)
            and predicted_components > 0
        )
        if (
            isinstance(matched_components, bool)
            or not isinstance(matched_components, int)
            or not component_counts_valid
            or not 0 <= matched_components <= min(
                reference_components,
                predicted_components,
            )
        ):
            errors.append(f"{prefix}.matched_component_count is inconsistent")
        elif isinstance(coverage, (int, float)) and not isinstance(
            coverage, bool
        ) and float(coverage) != matched_components / reference_components:
            errors.append(f"{prefix}.component_coverage_ratio is arithmetically inconsistent")
        if component_counts_valid and metrics.get("component_count_match") != int(
            reference_components == predicted_components
        ):
            errors.append(f"{prefix}.component_count_match is arithmetically inconsistent")
        if (
            isinstance(reference_faces, int)
            and not isinstance(reference_faces, bool)
            and isinstance(reference_components, int)
            and not isinstance(reference_components, bool)
            and reference_faces != reference_components
        ):
            errors.append(
                f"{prefix}.reference_face_count must equal reference_component_count"
            )
        overall = metrics.get("max_normalized_hausdorff_distance")
        lower = metrics.get("max_normalized_hausdorff_distance_lower_bound")
        upper = metrics.get("max_normalized_hausdorff_distance_upper_bound")
        uncertainty = metrics.get("max_normalized_hausdorff_distance_uncertainty")
        component = metrics.get("max_component_normalized_hausdorff_distance")
        directed_values = [
            metrics.get("reference_to_predicted_max_normalized_distance"),
            metrics.get("predicted_to_reference_max_normalized_distance"),
            component,
        ]
        if all(
            isinstance(value, (int, float)) and not isinstance(value, bool)
            for value in [overall, *directed_values]
        ) and float(overall) != max(float(value) for value in directed_values):
            errors.append(
                f"{prefix}.max_normalized_hausdorff_distance is inconsistent"
            )
        if all(
            isinstance(value, (int, float)) and not isinstance(value, bool)
            for value in (overall, lower, upper, uncertainty)
        ) and (
            float(lower) > float(upper)
            or float(overall) != float(lower)
            or float(uncertainty) != float(upper) - float(lower)
        ):
            errors.append(f"{prefix}.polyline distance bounds are inconsistent")
        sampling_error = metrics.get("polyline_sampling_error_bound")
        sampling_gap = metrics.get("polyline_sampling_max_gap")
        if (
            not isinstance(sampling_error, (int, float))
            or isinstance(sampling_error, bool)
            or not 0 <= float(sampling_error) <= 1.0e-4 + 1.0e-15
        ):
            errors.append(
                f"{prefix}.polyline_sampling_error_bound exceeds the fixed 1e-4 bound"
            )
        if (
            not isinstance(sampling_gap, (int, float))
            or isinstance(sampling_gap, bool)
            or not 0 <= float(sampling_gap) <= 2.0e-4 + 1.0e-15
        ):
            errors.append(
                f"{prefix}.polyline_sampling_max_gap exceeds the fixed 2e-4 bound"
            )
    if kind == "parametric_curve":
        for key in (
            "closed_topology_match",
            "distance_within_tolerance_proven",
            "distance_exceeds_tolerance_proven",
            "distance_decision_defined",
        ):
            if metrics.get(key) not in (0, 1):
                errors.append(f"{prefix}.{key} must equal 0 or 1")
        if metrics.get("closed_topology_match") != 1:
            errors.append(f"{prefix}.closed_topology_match must equal 1")
        within = metrics.get("distance_within_tolerance_proven")
        exceeds = metrics.get("distance_exceeds_tolerance_proven")
        decision = metrics.get("distance_decision_defined")
        if all(value in (0, 1) for value in (within, exceeds, decision)) and (
            within + exceeds != decision
        ):
            errors.append(f"{prefix}.distance decision flags are inconsistent")
        iou = metrics.get("normalized_bbox_iou")
        if (
            isinstance(iou, (int, float))
            and not isinstance(iou, bool)
            and not 0 <= float(iou) <= 1
        ):
            errors.append(f"{prefix}.normalized_bbox_iou must be in [0, 1]")
        overall = metrics.get("max_normalized_hausdorff_distance")
        lower = metrics.get("max_normalized_hausdorff_distance_lower_bound")
        upper = metrics.get("max_normalized_hausdorff_distance_upper_bound")
        uncertainty = metrics.get("max_normalized_hausdorff_distance_uncertainty")
        directed = (
            metrics.get("reference_to_predicted_max_normalized_distance"),
            metrics.get("predicted_to_reference_max_normalized_distance"),
        )
        if all(
            isinstance(value, (int, float)) and not isinstance(value, bool)
            for value in (overall, *directed)
        ) and float(overall) != max(float(value) for value in directed):
            errors.append(
                f"{prefix}.max_normalized_hausdorff_distance is inconsistent"
            )
        if all(
            isinstance(value, (int, float)) and not isinstance(value, bool)
            for value in (overall, lower, upper, uncertainty)
        ) and (
            float(lower) > float(upper)
            or float(overall) != float(lower)
            or float(uncertainty) != float(upper) - float(lower)
        ):
            errors.append(f"{prefix}.polyline distance bounds are inconsistent")
        sampling_error = metrics.get("polyline_sampling_error_bound")
        sampling_gap = metrics.get("polyline_sampling_max_gap")
        if (
            not isinstance(sampling_error, (int, float))
            or isinstance(sampling_error, bool)
            or not 0 <= float(sampling_error) <= 1.0e-4 + 1.0e-15
        ):
            errors.append(
                f"{prefix}.polyline_sampling_error_bound exceeds the fixed 1e-4 bound"
            )
        if (
            not isinstance(sampling_gap, (int, float))
            or isinstance(sampling_gap, bool)
            or not 0 <= float(sampling_gap) <= 2.0e-4 + 1.0e-15
        ):
            errors.append(
                f"{prefix}.polyline_sampling_max_gap exceeds the fixed 2e-4 bound"
            )
        for key in ("reference_domain_coverage", "scan_domain_coverage"):
            if metrics.get(key) != 1.0:
                errors.append(f"{prefix}.{key} must equal 1.0")
        declared_min = metrics.get("declared_parameter_min")
        declared_max = metrics.get("declared_parameter_max")
        endpoints = (
            metrics.get("reference_parameter_min"),
            metrics.get("reference_parameter_max"),
            metrics.get("scan_parameter_min"),
            metrics.get("scan_parameter_max"),
        )
        if not all(
            isinstance(value, (int, float)) and not isinstance(value, bool)
            for value in (declared_min, declared_max, *endpoints)
        ) or not float(declared_min) < float(declared_max):
            errors.append(f"{prefix}.declared parameter domain is invalid")
        elif tuple(float(value) for value in endpoints) != (
            float(declared_min),
            float(declared_max),
            float(declared_min),
            float(declared_max),
        ):
            errors.append(
                f"{prefix}.parametric endpoints do not equal the declared domain"
            )
        node_counts = (
            metrics.get("reference_node_count"),
            metrics.get("scan_node_count"),
        )
        if all(
            isinstance(value, int) and not isinstance(value, bool)
            for value in node_counts
        ) and metrics.get("n_points_compared", 0) < max(node_counts):
            errors.append(f"{prefix}.n_points_compared omits declared curve nodes")
    if kind in {"parametric_curve", "exclusion_region"}:
        if metrics.get("n_points_compared") != metrics.get("polyline_sample_count"):
            errors.append(
                f"{prefix}.n_points_compared must equal polyline_sample_count"
            )
        uncertainty = metrics.get("max_normalized_hausdorff_distance_uncertainty")
        sampling_error = metrics.get("polyline_sampling_error_bound")
        if (
            isinstance(uncertainty, (int, float))
            and not isinstance(uncertainty, bool)
            and isinstance(sampling_error, (int, float))
            and not isinstance(sampling_error, bool)
            and not math.isclose(
                float(sampling_error),
                float(uncertainty),
                rel_tol=1.0e-12,
                abs_tol=1.0e-15,
            )
        ):
            errors.append(
                f"{prefix}.polyline_sampling_error_bound must equal distance uncertainty"
            )
        tolerance_value = tolerance.get("value") if isinstance(tolerance, dict) else None
        lower = metrics.get("max_normalized_hausdorff_distance_lower_bound")
        upper = metrics.get("max_normalized_hausdorff_distance_upper_bound")
        if all(
            isinstance(value, (int, float)) and not isinstance(value, bool)
            for value in (tolerance_value, lower, upper)
        ) and all(
            math.isfinite(float(value))
            for value in (tolerance_value, lower, upper)
        ):
            expected_within = int(float(upper) <= float(tolerance_value))
            expected_exceeds = int(float(lower) > float(tolerance_value))
            expected_decision = int(expected_within == 1 or expected_exceeds == 1)
            if (
                metrics.get("distance_within_tolerance_proven") != expected_within
                or metrics.get("distance_exceeds_tolerance_proven") != expected_exceeds
                or metrics.get("distance_decision_defined") != expected_decision
            ):
                errors.append(
                    f"{prefix}.distance decision flags contradict bounds and tolerance"
                )
    if kind == "figure_curve":
        for key in ("reference_domain_coverage", "scan_domain_coverage"):
            if metrics.get(key) != 1.0:
                errors.append(f"{prefix}.{key} must equal 1.0")
        declared_min = metrics.get("declared_x_min")
        declared_max = metrics.get("declared_x_max")
        endpoints = (
            metrics.get("reference_x_min"),
            metrics.get("reference_x_max"),
            metrics.get("scan_x_min"),
            metrics.get("scan_x_max"),
        )
        if not all(
            isinstance(value, (int, float)) and not isinstance(value, bool)
            for value in (declared_min, declared_max, *endpoints)
        ) or not float(declared_min) < float(declared_max):
            errors.append(f"{prefix}.declared comparison domain is invalid")
        elif tuple(float(value) for value in endpoints) != (
            float(declared_min),
            float(declared_max),
            float(declared_min),
            float(declared_max),
        ):
            errors.append(f"{prefix}.curve endpoints do not equal the declared domain")
        node_counts = (
            metrics.get("reference_node_count"),
            metrics.get("scan_node_count"),
        )
        if all(isinstance(value, int) and not isinstance(value, bool) for value in node_counts):
            if metrics.get("n_points_compared", 0) < max(node_counts):
                errors.append(f"{prefix}.n_points_compared omits declared curve nodes")
    return errors


def _selected_metric(metrics: dict[str, Any], tolerance_kind: object) -> float | None:
    if not isinstance(tolerance_kind, str):
        return None
    if tolerance_kind == "relative" and metrics.get("relative_error_defined") != 1:
        return None
    candidates = {
        "relative": ("max_relative_error", "relative_error"),
        "absolute": ("max_absolute_error", "absolute_error"),
        "normalized_distance": ("max_normalized_hausdorff_distance",),
    }.get(tolerance_kind, ())
    for key in candidates:
        value = metrics.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            numeric = float(value)
            if math.isfinite(numeric):
                return numeric
    return None


def _verdict_contract_error(result: dict[str, Any], prefix: str) -> str | None:
    verdict = result.get("verdict")
    if not isinstance(verdict, str):
        return f"{prefix}.verdict must be a string"
    comparison = result.get("comparison")
    if not isinstance(comparison, dict):
        return f"{prefix}.comparison must be an object"
    kind = comparison.get("kind")
    if not isinstance(kind, str):
        return f"{prefix}.comparison.kind must be a string"
    metrics = comparison.get("metrics")
    tolerance = result.get("tolerance")
    ceiling = result.get("verdict_ceiling")
    if result.get("derivation_independence") == "tainted":
        expected = "blocked"
    elif kind == "formula":
        expected = "needs_human_review"
    elif not isinstance(tolerance, dict):
        expected = "blocked"
    elif not isinstance(tolerance.get("kind"), str):
        expected = "blocked"
    elif tolerance.get("kind") == "qualitative":
        expected = "needs_human_review"
    elif not isinstance(metrics, dict):
        expected = "blocked"
    else:
        metric_value = _selected_metric(metrics, tolerance.get("kind"))
        tolerance_value = tolerance.get("value")
        if (
            metric_value is None
            or not isinstance(tolerance_value, (int, float))
            or isinstance(tolerance_value, bool)
            or not math.isfinite(float(tolerance_value))
            or float(tolerance_value) < 0
        ):
            expected = "blocked"
        elif kind in {"scan_table", "keyed_benchmark_set"} and (
            not isinstance(comparison.get("completeness"), dict)
            or comparison["completeness"].get("complete") is not True
        ):
            expected = "blocked"
        elif kind in {"parametric_curve", "exclusion_region"}:
            required_integrity = {"closed_topology_match": 1}
            if kind == "exclusion_region":
                required_integrity.update({
                    "component_count_match": 1,
                    "component_coverage_ratio": 1.0,
                    "face_assignment_defined": 1,
                    "face_parent_topology_match": 1,
                    "face_probe_coverage_ratio": 1.0,
                    "excluded_probe_match": 1,
                })
            integrity_failed = any(
                metrics.get(key) != expected_value
                for key, expected_value in required_integrity.items()
            )
            if integrity_failed or metrics.get("distance_exceeds_tolerance_proven") == 1:
                expected = "fail"
            elif metrics.get("distance_decision_defined") != 1:
                expected = "blocked"
            elif metric_value > float(tolerance_value):
                expected = "fail"
            else:
                expected = "pass" if ceiling == "pass" else "needs_human_review"
        elif metric_value > float(tolerance_value):
            expected = "fail"
        else:
            expected = "pass" if ceiling == "pass" else "needs_human_review"
    if verdict != expected:
        return f"{prefix}.verdict is {verdict!r}; fixed metrics/tolerance require {expected!r}"
    return None


def _completeness_errors(completeness: Any, prefix: str) -> list[str]:
    if not isinstance(completeness, dict):
        return []
    errors: list[str] = []
    reference = completeness.get("reference_rows")
    matched = completeness.get("matched_reference_rows")
    missing = completeness.get("missing_reference_rows")
    expected_values = completeness.get("expected_values")
    compared_values = completeness.get("compared_values")
    expected_observables = completeness.get("observables_expected")
    compared_observables = completeness.get("observables_compared")
    def ratio_matches(key: str, expected: float) -> bool:
        actual = completeness.get(key)
        return (
            isinstance(actual, (int, float))
            and not isinstance(actual, bool)
            and math.isfinite(float(actual))
            and float(actual) == expected
        )

    if all(isinstance(value, int) and not isinstance(value, bool) for value in (reference, matched, missing)):
        if reference != matched + missing:
            errors.append(f"{prefix}: reference_rows must equal matched_reference_rows + missing_reference_rows")
        expected_row_coverage = matched / reference if reference else 0.0
        if not ratio_matches("row_coverage", expected_row_coverage):
            errors.append(f"{prefix}.row_coverage is arithmetically inconsistent")
    if isinstance(reference, int) and isinstance(expected_observables, list):
        if expected_values != reference * len(expected_observables):
            errors.append(f"{prefix}.expected_values is arithmetically inconsistent")
    if isinstance(expected_values, int) and isinstance(compared_values, int):
        expected_value_coverage = compared_values / expected_values if expected_values else 0.0
        if compared_values > expected_values:
            errors.append(f"{prefix}.compared_values exceeds expected_values")
        if not ratio_matches("value_coverage", expected_value_coverage):
            errors.append(f"{prefix}.value_coverage is arithmetically inconsistent")
    if isinstance(expected_observables, list) and isinstance(compared_observables, list):
        expected_names = {
            item for item in expected_observables if isinstance(item, str)
        }
        compared_names = {
            item for item in compared_observables if isinstance(item, str)
        }
        if len(expected_names) != len(expected_observables) or len(
            compared_names
        ) != len(compared_observables):
            errors.append(f"{prefix}.observable lists must contain only unique strings")
        if not compared_names <= expected_names:
            errors.append(f"{prefix}.observables_compared is not a subset of expected observables")
        if completeness.get("complete") is True and compared_names != expected_names:
            errors.append(f"{prefix}.complete result did not compare every observable")
    if completeness.get("complete") is True and completeness.get("blocking_reasons"):
        errors.append(f"{prefix}.complete result cannot have blocking reasons")
    if completeness.get("complete") is True:
        if (
            matched != reference
            or missing != 0
            or completeness.get("row_coverage") != 1.0
            or compared_values != expected_values
            or completeness.get("value_coverage") != 1.0
        ):
            errors.append(
                f"{prefix}.complete result must have full row and value coverage"
            )
    if completeness.get("complete") is False and not completeness.get("blocking_reasons"):
        errors.append(f"{prefix}.incomplete result must have blocking reasons")
    return errors


def _scientific_metrics_match(expected: Any, actual: Any) -> bool:
    if isinstance(expected, dict) and isinstance(actual, dict):
        return set(expected) == set(actual) and all(
            _scientific_metrics_match(expected[key], actual[key])
            for key in expected
        )
    if isinstance(expected, bool) != isinstance(actual, bool):
        return False
    return expected == actual


def _recomputed_comparison_errors(
    payload: dict[str, Any],
    result: dict[str, Any],
    *,
    project_dir: Path,
    prefix: str,
) -> list[str]:
    comparison = result.get("comparison")
    if not isinstance(comparison, dict):
        return []
    persisted_metrics = comparison.get("metrics")
    errors: list[str] = []
    try:
        try:
            from _compare_metrics import (
                benchmark_point_metrics,
                exclusion_region_metrics,
                figure_curve_metrics,
                keyed_benchmark_metrics,
                load_csv,
                parametric_curve_metrics,
                scan_table_metrics,
            )
            from _strict_json import load_json
        except ModuleNotFoundError:
            from scripts._compare_metrics import (
                benchmark_point_metrics,
                exclusion_region_metrics,
                figure_curve_metrics,
                keyed_benchmark_metrics,
                load_csv,
                parametric_curve_metrics,
                scan_table_metrics,
            )
            from scripts._strict_json import load_json

        targets_payload = load_json(project_dir / "literature" / "repro-targets.json")
        matching_targets = [
            item
            for item in targets_payload.get("targets", [])
            if isinstance(item, dict) and item.get("id") == result.get("target_id")
        ]
        if len(matching_targets) != 1:
            return [
                f"{prefix}: target_id must match exactly one repro target; "
                f"matched {len(matching_targets)}"
            ]
        target = matching_targets[0]
        if comparison.get("kind") != target.get("kind"):
            errors.append(f"{prefix}.comparison.kind does not match repro target")
        if result.get("tolerance") != target.get("tolerance"):
            errors.append(f"{prefix}.tolerance does not match repro target")
        try:
            expected_reference, expected_comparison = expected_evidence_axes(target)
        except ValueError as exc:
            errors.append(f"{prefix}: cannot derive current evidence axes: {exc}")
        else:
            if result.get("reference_evidence") != expected_reference:
                errors.append(
                    f"{prefix}.reference_evidence does not match current repro target; "
                    f"expected {expected_reference!r}"
                )
            if result.get("comparison_evidence") != expected_comparison:
                errors.append(
                    f"{prefix}.comparison_evidence does not match current repro target; "
                    f"expected {expected_comparison!r}"
                )
        if target.get("kind") == "figure_curve":
            if comparison.get("interpolation_method") != "piecewise_linear_union_knots":
                errors.append(
                    f"{prefix}.comparison.interpolation_method is not the declared curve method"
                )
        elif target.get("kind") == "parametric_curve":
            if (
                comparison.get("geometry_method")
                != "normalized_continuous_polyline_hausdorff"
            ):
                errors.append(
                    f"{prefix}.comparison.geometry_method is not the declared curve method"
                )
            if "interpolation_method" in comparison:
                errors.append(
                    f"{prefix}.comparison.interpolation_method is invalid for parametric_curve"
                )
        elif "interpolation_method" in comparison or "geometry_method" in comparison:
            errors.append(
                f"{prefix}.comparison method auxiliary is invalid for target kind"
            )
        expects_completeness = target.get("kind") in {
            "scan_table",
            "keyed_benchmark_set",
        }
        if expects_completeness != ("completeness" in comparison):
            errors.append(
                f"{prefix}.comparison completeness presence does not match target kind"
            )
        if target.get("kind") == "formula":
            return errors
        if result.get("verdict") == "blocked" and not persisted_metrics:
            if result.get("derivation_independence") == "tainted":
                return errors
            paper_extract = load_json(
                project_dir / "literature" / "paper-extract.json"
            )
            matching_hints = [
                item
                for item in paper_extract.get("scan_config_hints", [])
                if isinstance(item, dict)
                and item.get("target_id") == result.get("target_id")
            ]
            if len(matching_hints) != 1 or bool(
                matching_hints[0].get("missing_fields")
            ):
                return errors
        analysis_id = payload.get("depends_on", {}).get("numerics", {}).get(
            "analysis_id"
        )
        scan = load_csv(
            project_dir
            / "numerics"
            / "scan-results"
            / str(analysis_id)
            / "scan.csv"
        )
        reference = load_csv(project_dir / str(target["data_file"]))
        kind = target.get("kind")
        completeness = None
        if kind == "figure_curve":
            recomputed_metrics, _ = figure_curve_metrics(scan, reference, target)
        elif kind == "parametric_curve":
            recomputed_metrics, _ = parametric_curve_metrics(scan, reference, target)
        elif kind == "benchmark_point":
            recomputed_metrics, _ = benchmark_point_metrics(scan, reference, target)
        elif kind == "keyed_benchmark_set":
            metric_result = keyed_benchmark_metrics(scan, reference, target)
            recomputed_metrics = metric_result.metrics
            completeness = metric_result.completeness
        elif kind == "scan_table":
            metric_result = scan_table_metrics(scan, reference, target)
            recomputed_metrics = metric_result.metrics
            completeness = metric_result.completeness
        elif kind == "exclusion_region":
            recomputed_metrics, _ = exclusion_region_metrics(scan, reference, target)
        else:
            return [f"{prefix}: cannot recompute unsupported target kind {kind!r}"]
    except (OSError, ValueError, KeyError, StopIteration, TypeError) as exc:
        expected_warning = f"metric_computation_blocked: {exc}"
        warnings = result.get("warnings")
        if (
            result.get("verdict") == "blocked"
            and persisted_metrics == {}
            and isinstance(warnings, list)
            and expected_warning in warnings
        ):
            return errors
        return [f"{prefix}: cannot recompute comparison evidence: {exc}"]

    if not _scientific_metrics_match(recomputed_metrics, persisted_metrics):
        errors.append(f"{prefix}.comparison.metrics do not match current scientific inputs")
    if completeness is not None and not _scientific_metrics_match(
        completeness,
        comparison.get("completeness"),
    ):
        errors.append(
            f"{prefix}.comparison.completeness does not match current scientific inputs"
        )
    return errors


def reproduction_result_semantic_errors(
    payload: dict[str, Any],
    *,
    project_dir: Path | None = None,
    expected_run_dir: Path | None = None,
    scientific_project_dir: Path | None = None,
    verify_current_scientific_inputs: bool = True,
) -> list[str]:
    """Return deterministic cross-field and filesystem validation errors."""

    if not isinstance(payload, dict):
        return ["reproduction result must be an object"]
    errors: list[str] = []
    results = payload.get("results")
    if not isinstance(results, list):
        return ["results must be an array"]

    target_ids = [
        item.get("target_id")
        for item in results
        if isinstance(item, dict) and isinstance(item.get("target_id"), str)
    ]
    duplicate_targets = sorted(
        str(target_id)
        for target_id, count in Counter(target_ids).items()
        if count > 1
    )
    if duplicate_targets:
        errors.append(f"results contain duplicate target_id values: {duplicate_targets}")

    verdict_counts: Counter[str] = Counter()
    independence_values: list[str] = []
    repro_id = payload.get("repro_id")
    depends_on = payload.get("depends_on")
    dependency_tasks: set[str] = set()
    seen_generated_evidence: list[tuple[Path, str]] = []
    scientific_root = (
        scientific_project_dir or project_dir
        if verify_current_scientific_inputs
        else None
    )
    quantitative_evidence_required = any(
        isinstance(item, dict)
        and isinstance(item.get("comparison"), dict)
        and item["comparison"].get("kind") != "formula"
        for item in results
    )
    computational_model_required = quantitative_evidence_required
    selected_targets: list[dict[str, Any]] | None = None
    coverage_derivation_error: str | None = None
    if scientific_root is not None:
        try:
            targets_payload = load_json(
                scientific_root / "literature" / "repro-targets.json"
            )
            if not isinstance(targets_payload, dict):
                raise ValueError("repro-targets.json must contain an object")
            target_by_id = {
                str(target.get("id")): target
                for target in targets_payload.get("targets", [])
                if isinstance(target, dict)
            }
            selected_ids = {
                str(result.get("target_id"))
                for result in results
                if isinstance(result, dict)
            }
            missing_target_ids = sorted(selected_ids - set(target_by_id))
            if missing_target_ids:
                raise ValueError(
                    f"current repro-targets is missing result targets {missing_target_ids}"
                )
            selected_targets = [target_by_id[target_id] for target_id in sorted(selected_ids)]
            paper_extract_payload = load_json(
                scientific_root / "literature" / "paper-extract.json"
            )
            if not isinstance(paper_extract_payload, dict):
                raise ValueError("paper-extract.json must contain an object")
            quantitative_evidence_required = bool(
                reproduction_scan_required_target_ids(
                    selected_targets,
                    paper_extract_payload,
                )
            )
        except (OSError, ValueError, TypeError) as exc:
            coverage_derivation_error = str(exc)

    if (
        scientific_root is not None
        and isinstance(depends_on, dict)
        and computational_model_required
    ):
        errors.extend(_current_model_projection_errors(depends_on, scientific_root))
    if isinstance(depends_on, dict) and not computational_model_required:
        model_dependency = depends_on.get("model")
        calculation_dependency = depends_on.get("calculations")
        numerics_dependency = depends_on.get("numerics")
        if model_dependency != {"version": None, "checksum": None}:
            errors.append(
                "formula-only reproduction must declare model dependency as not applicable"
            )
        if calculation_dependency != {"tasks": [], "model_version": None}:
            errors.append(
                "formula-only reproduction must declare calculations dependency as not applicable"
            )
        if not isinstance(numerics_dependency, dict) or any(
            numerics_dependency.get(key) is not None
            for key in ("scan_meta_checksum", "scan_csv_checksum")
        ):
            errors.append(
                "formula-only reproduction must not declare numeric scan evidence"
            )

    input_provenance = payload.get("input_provenance")
    provenance_status = (
        input_provenance.get("verification_status")
        if isinstance(input_provenance, dict)
        else None
    )
    if provenance_status == "legacy-unverified":
        if scientific_root is not None and coverage_derivation_error is not None:
            errors.append(
                "current scan dependency coverage cannot be derived: "
                + coverage_derivation_error
            )
        errors.extend(
            f"input_provenance: {issue}"
            for issue in verify_dependency_graph(
                input_provenance,
                scientific_root or Path.cwd(),
                Path(__file__).resolve().parent.parent,
                allow_legacy=True,
            )
        )
        for index, result in enumerate(results):
            if not isinstance(result, dict):
                continue
            if result.get("verdict") == "pass":
                errors.append(
                    f"results.{index}.legacy-unverified input provenance cannot support pass"
                )
            if result.get("derivation_independence") == "independent":
                errors.append(
                    f"results.{index}.legacy-unverified input provenance cannot support independent"
                )
            if result.get("verdict_ceiling") != "needs_human_review":
                errors.append(
                    f"results.{index}.legacy-unverified input provenance requires a human-review ceiling"
                )
    elif scientific_root is not None and isinstance(depends_on, dict):
        if coverage_derivation_error is not None or selected_targets is None:
            errors.append(
                "input_provenance coverage cannot be derived: "
                + (coverage_derivation_error or "selected targets are unavailable")
            )
        else:
            numerics_dependency = depends_on.get("numerics", {})
            analysis_id = str(numerics_dependency.get("analysis_id"))
            calculation_dependency = depends_on.get("calculations", {})
            graph_task_ids = (
                calculation_dependency.get("tasks", [])
                if isinstance(calculation_dependency, dict)
                else []
            )
            try:
                expected_dependencies = reproduction_dependency_specs(
                    scientific_root,
                    Path(__file__).resolve().parent.parent,
                    selected_targets,
                    graph_task_ids,
                    analysis_id=analysis_id,
                    include_scan=quantitative_evidence_required,
                )
            except (OSError, ValueError, TypeError) as exc:
                errors.append(f"input_provenance coverage cannot be derived: {exc}")
            else:
                errors.extend(
                    f"input_provenance: {issue}"
                    for issue in verify_dependency_graph(
                        input_provenance,
                        scientific_root,
                        Path(__file__).resolve().parent.parent,
                        expected_specs=expected_dependencies,
                    )
                )
    if isinstance(depends_on, dict):
        calculation_dependency = depends_on.get("calculations", {})
        if isinstance(calculation_dependency, dict) and isinstance(
            calculation_dependency.get("tasks"), list
        ):
            dependency_tasks = {
                item
                for item in calculation_dependency["tasks"]
                if isinstance(item, str)
            }
        if scientific_root is not None:
            numerics_dependency = depends_on.get("numerics")
            if isinstance(numerics_dependency, dict):
                analysis_id = numerics_dependency.get("analysis_id")
                for filename, checksum_key in (
                    ("scan.csv", "scan_csv_checksum"),
                    ("scan.meta.json", "scan_meta_checksum"),
                ):
                    path = (
                        scientific_root
                        / "numerics"
                        / "scan-results"
                        / str(analysis_id)
                        / filename
                    )
                    declared = numerics_dependency.get(checksum_key)
                    if quantitative_evidence_required or declared is not None:
                        if path.exists() and path.is_file():
                            if declared != _sha256(path):
                                errors.append(
                                    f"depends_on.numerics.{checksum_key} does not match current {filename}"
                                )
                        else:
                            errors.append(
                                f"depends_on.numerics.{checksum_key} references missing {filename}"
                            )
            literature_dependency = depends_on.get("literature")
            if isinstance(literature_dependency, dict):
                targets_path = scientific_root / "literature" / "repro-targets.json"
                paper_extract_path = scientific_root / "literature" / "paper-extract.json"
                if not paper_extract_path.exists() or not paper_extract_path.is_file():
                    errors.append(
                        "depends_on.literature references missing paper-extract.json"
                    )
                elif literature_dependency.get("paper_extract_checksum") != _sha256(
                    paper_extract_path
                ):
                    errors.append(
                        "depends_on.literature.paper_extract_checksum does not match current file"
                    )
                if not targets_path.exists() or not targets_path.is_file():
                    errors.append("depends_on.literature references missing repro-targets.json")
                else:
                    if literature_dependency.get("repro_targets_checksum") != _sha256(
                        targets_path
                    ):
                        errors.append(
                            "depends_on.literature.repro_targets_checksum does not match current file"
                        )
                    try:
                        targets_payload = load_json(targets_path)
                        selected_ids = {
                            item.get("target_id")
                            for item in results
                            if isinstance(item, dict)
                        }
                        expected_paths: set[str] = set()
                        for target in targets_payload.get("targets", []):
                            if not isinstance(target, dict) or target.get("id") not in selected_ids:
                                continue
                            expected_paths.add(str(target.get("data_file")))
                            normalization = target.get("normalization")
                            if isinstance(normalization, dict):
                                expected_paths.update(
                                    {
                                        str(normalization.get("source_data_file")),
                                        str(normalization.get("record_file")),
                                    }
                                )
                        expected_paths.discard("None")
                        actual_checksums = literature_dependency.get(
                            "digitized_files_checksums"
                        )
                        if not isinstance(actual_checksums, dict) or set(
                            actual_checksums
                        ) != expected_paths:
                            errors.append(
                                "depends_on.literature.digitized_files_checksums does not "
                                "exactly cover selected reference evidence"
                            )
                        else:
                            digitized_root = (
                                scientific_root / "literature" / "digitized"
                            ).resolve()
                            for relpath in sorted(expected_paths):
                                evidence_path = (scientific_root / relpath).resolve()
                                if (
                                    not evidence_path.is_relative_to(digitized_root)
                                    or not evidence_path.exists()
                                    or not evidence_path.is_file()
                                ):
                                    errors.append(
                                        f"depends_on literature evidence is missing or escapes "
                                        f"digitized root: {relpath}"
                                    )
                                elif actual_checksums.get(relpath) != _sha256(evidence_path):
                                    errors.append(
                                        f"depends_on literature checksum does not match: {relpath}"
                                    )
                    except (OSError, ValueError, TypeError) as exc:
                        errors.append(
                            f"depends_on literature evidence cannot be verified: {exc}"
                        )
    for index, result in enumerate(results):
        prefix = f"results.{index}"
        if not isinstance(result, dict):
            errors.append(f"{prefix} must be an object")
            continue

        verdict = result.get("verdict")
        independence = result.get("derivation_independence")
        reference_evidence = result.get("reference_evidence")
        comparison_evidence = result.get("comparison_evidence")
        ceiling = result.get("verdict_ceiling")
        comparison = result.get("comparison")
        if not isinstance(comparison, dict):
            errors.append(f"{prefix}.comparison must be an object")
            comparison = {}
        kind = comparison.get("kind")
        metrics = comparison.get("metrics")
        if isinstance(verdict, str):
            verdict_counts[verdict] += 1
        else:
            errors.append(f"{prefix}.verdict must be a string")
        if not isinstance(kind, str):
            errors.append(f"{prefix}.comparison.kind must be a string")
        tolerance = result.get("tolerance")
        tolerance_kind = tolerance.get("kind") if isinstance(tolerance, dict) else None
        tolerance_value = tolerance.get("value") if isinstance(tolerance, dict) else None
        if kind == "formula":
            if tolerance_kind != "qualitative" or not (
                tolerance_value is None or isinstance(tolerance_value, str)
            ):
                errors.append(f"{prefix}.formula tolerance must be qualitative")
            if reference_evidence != "unverified":
                errors.append(f"{prefix}.formula reference_evidence must be unverified")
            if comparison_evidence != "requires_human_review":
                errors.append(
                    f"{prefix}.formula comparison_evidence must require human review"
                )
            if ceiling != "needs_human_review":
                errors.append(f"{prefix}.formula verdict_ceiling must require human review")
            if result.get("generated_files") != {}:
                errors.append(f"{prefix}.formula result cannot declare generated figures")
            if "interpolation_method" in comparison or "completeness" in comparison:
                errors.append(
                    f"{prefix}.formula comparison cannot declare quantitative auxiliaries"
                )
        elif isinstance(kind, str):
            if (
                not isinstance(tolerance_kind, str)
                or tolerance_kind not in {"relative", "absolute", "normalized_distance"}
                or not isinstance(tolerance_value, (int, float))
                or isinstance(tolerance_value, bool)
                or not math.isfinite(float(tolerance_value))
                or float(tolerance_value) < 0
            ):
                errors.append(f"{prefix}.quantitative tolerance must be finite and non-negative")
            if kind in {"parametric_curve", "exclusion_region"} and tolerance_kind != "normalized_distance":
                errors.append(
                    f"{prefix}.{kind} tolerance must use normalized_distance"
                )
            if kind not in {"parametric_curve", "exclusion_region"} and (
                not isinstance(tolerance_kind, str)
                or tolerance_kind not in {"relative", "absolute"}
            ):
                errors.append(
                    f"{prefix}.{kind} tolerance must use relative or absolute error"
                )
            if kind == "figure_curve":
                if comparison.get("interpolation_method") != "piecewise_linear_union_knots":
                    errors.append(
                        f"{prefix}.figure_curve requires its fixed interpolation method"
                    )
                if "completeness" in comparison:
                    errors.append(f"{prefix}.figure_curve cannot declare table completeness")
                if "geometry_method" in comparison:
                    errors.append(f"{prefix}.figure_curve cannot declare geometry_method")
            elif kind == "parametric_curve":
                if (
                    comparison.get("geometry_method")
                    != "normalized_continuous_polyline_hausdorff"
                ):
                    errors.append(
                        f"{prefix}.parametric_curve requires its fixed geometry method"
                    )
                if "interpolation_method" in comparison or "completeness" in comparison:
                    errors.append(
                        f"{prefix}.parametric_curve declares invalid comparison auxiliaries"
                    )
            elif kind in {"scan_table", "keyed_benchmark_set"}:
                if "interpolation_method" in comparison or "geometry_method" in comparison:
                    errors.append(f"{prefix}.{kind} cannot declare a geometry/interpolation method")
            elif (
                "interpolation_method" in comparison
                or "geometry_method" in comparison
                or "completeness" in comparison
            ):
                errors.append(f"{prefix}.{kind} declares invalid comparison auxiliaries")
        if not isinstance(independence, str):
            errors.append(f"{prefix}.derivation_independence must be a string")
        if not isinstance(ceiling, str):
            errors.append(f"{prefix}.verdict_ceiling must be a string")
        if isinstance(independence, str) and independence in INDEPENDENCE_ORDER:
            independence_values.append(independence)

        expected_ceiling = (
            "pass"
            if independence == "independent"
            and reference_evidence == "independent_snapshot"
            and comparison_evidence == "machine_verifiable"
            else "needs_human_review"
        )
        if ceiling != expected_ceiling:
            errors.append(
                f"{prefix}.verdict_ceiling is {ceiling!r}; "
                "derivation/reference/comparison evidence requires "
                f"{expected_ceiling!r}"
            )
        if verdict == "pass" and (independence != "independent" or ceiling != "pass"):
            errors.append(f"{prefix}.verdict pass exceeds its provenance ceiling")

        tasks_used = result.get("tasks_used")
        if isinstance(tasks_used, list):
            string_tasks = [item for item in tasks_used if isinstance(item, str)]
            if len(string_tasks) != len(tasks_used):
                errors.append(f"{prefix}.tasks_used must contain only task IDs")
            if len(string_tasks) != len(set(string_tasks)):
                errors.append(f"{prefix}.tasks_used contains duplicates")
        if isinstance(tasks_used, list):
            missing_dependency_tasks = sorted(
                {item for item in tasks_used if isinstance(item, str)}
                - dependency_tasks
            )
            if missing_dependency_tasks:
                errors.append(
                    f"{prefix}.tasks_used are absent from depends_on.calculations.tasks: "
                    f"{missing_dependency_tasks}"
                )
        provenance_issues = result.get("provenance_issues")
        if independence == "independent":
            if not isinstance(tasks_used, list) or not tasks_used:
                errors.append(f"{prefix}.independent result must cite at least one task")
            if provenance_issues:
                errors.append(f"{prefix}.independent result cannot have provenance issues")
            if isinstance(depends_on, dict):
                model_dependency = depends_on.get("model", {})
                calculation_dependency = depends_on.get("calculations", {})
                numerics_dependency = depends_on.get("numerics", {})
                literature_dependency = depends_on.get("literature", {})
                model_version = (
                    model_dependency.get("version")
                    if isinstance(model_dependency, dict)
                    else None
                )
                if not isinstance(model_dependency, dict) or not model_dependency.get(
                    "version"
                ) or not model_dependency.get("checksum"):
                    errors.append(f"{prefix}.independent result lacks verified model dependency")
                if (
                    not isinstance(calculation_dependency, dict)
                    or calculation_dependency.get("model_version")
                    != model_version
                ):
                    errors.append(
                        f"{prefix}.calculation/model dependency versions are inconsistent"
                    )
                if kind != "formula" and verdict != "blocked" and (
                    not isinstance(numerics_dependency, dict)
                    or not numerics_dependency.get("scan_meta_checksum")
                ):
                    errors.append(
                        f"{prefix}.independent quantitative result lacks scan-meta checksum"
                    )
                if (
                    not isinstance(literature_dependency, dict)
                    or not literature_dependency.get("repro_targets_checksum")
                    or (
                        kind != "formula"
                        and not literature_dependency.get("digitized_files_checksums")
                    )
                ):
                    errors.append(
                        f"{prefix}.independent result lacks verified literature dependencies"
                    )
            else:
                errors.append(f"{prefix}.independent result lacks depends_on metadata")
            if scientific_root is not None:
                errors.append(
                    f"{prefix}.independent provenance cannot be mechanically revalidated: "
                    "Phase 0 accepts static derivation evidence only with a human-review ceiling"
                )
        elif isinstance(provenance_issues, list) and not provenance_issues:
            errors.append(
                f"{prefix}.{independence} result must explain its provenance limitation"
            )

        if not isinstance(metrics, dict):
            errors.append(f"{prefix}.comparison.metrics must be an object")
        else:
            errors.extend(
                _metric_contract_errors(
                    metrics,
                    kind=kind,
                    verdict=verdict,
                    prefix=f"{prefix}.comparison.metrics",
                    tolerance=result.get("tolerance"),
                )
            )
        verdict_error = _verdict_contract_error(result, prefix)
        if verdict_error:
            errors.append(verdict_error)
        errors.extend(
            _completeness_errors(
                comparison.get("completeness"),
                f"{prefix}.comparison.completeness",
            )
        )
        if scientific_root is not None:
            errors.extend(
                _recomputed_comparison_errors(
                    payload,
                    result,
                    project_dir=scientific_root,
                    prefix=prefix,
                )
            )

        if project_dir is not None:
            generated = result.get("generated_files")
            if not isinstance(generated, dict):
                errors.append(f"{prefix}.generated_files must declare generated evidence")
            else:
                if kind == "formula":
                    expected_groups: set[str] = set()
                elif verdict == "blocked":
                    expected_groups = {"overlay"}
                else:
                    expected_groups = {"overlay", "side_by_side", "residual"}
                actual_groups = set(generated)
                if actual_groups != expected_groups:
                    errors.append(
                        f"{prefix}.generated_files groups are {sorted(actual_groups)}; "
                        f"expected {sorted(expected_groups)}"
                    )
                figures_root = (
                    project_dir / "reproduction" / "figures" / str(repro_id)
                )
                for group, pair in sorted(generated.items()):
                    if not isinstance(pair, dict):
                        errors.append(f"{prefix}.generated_files.{group} must be an object")
                        continue
                    for extension in ("pdf", "png"):
                        relpath = pair.get(extension)
                        target_id = result.get("target_id")
                        group_suffix = {
                            "overlay": "overlay",
                            "side_by_side": "side-by-side",
                            "residual": "residual",
                        }.get(group)
                        expected_relpath = (
                            f"reproduction/figures/{repro_id}/{target_id}-"
                            f"{group_suffix}.{extension}"
                            if isinstance(target_id, str) and group_suffix is not None
                            else None
                        )
                        if expected_relpath is not None and relpath != expected_relpath:
                            errors.append(
                                f"{prefix}.generated_files.{group}.{extension} must equal "
                                f"{expected_relpath!r}"
                            )
                        file_error = _contained_file(
                            project_dir,
                            relpath,
                            figures_root,
                        )
                        if file_error:
                            errors.append(
                                f"{prefix}.generated_files.{group}.{extension}: {file_error}"
                            )
                            continue
                        path = (project_dir / str(relpath)).resolve()
                        evidence_label = f"{group}.{extension}"
                        for previous_path, previous_label in seen_generated_evidence:
                            try:
                                same_file = path.samefile(previous_path)
                            except OSError:
                                same_file = path == previous_path
                            if same_file:
                                errors.append(
                                    f"{prefix}.generated_files.{evidence_label} reuses "
                                    f"evidence file declared by {previous_label}"
                                )
                        seen_generated_evidence.append(
                            (path, f"{prefix}.{evidence_label}")
                        )
                        if path.suffix.lower() != f".{extension}":
                            errors.append(
                                f"{prefix}.generated_files.{group}.{extension}: "
                                f"path extension must be .{extension}"
                            )
                        signature_error = _format_signature_error(path, extension)
                        if signature_error:
                            errors.append(
                                f"{prefix}.generated_files.{group}.{extension}: "
                                f"{signature_error}"
                            )
                        declared_checksum = pair.get(f"{extension}_sha256")
                        actual_checksum = _sha256(path)
                        if declared_checksum != actual_checksum:
                            errors.append(
                                f"{prefix}.generated_files.{group}.{extension}_sha256 "
                                "does not match the current file"
                            )
                    if isinstance(pair.get("pdf"), str) and isinstance(pair.get("png"), str):
                        pdf_path = (project_dir / pair["pdf"]).resolve()
                        png_path = (project_dir / pair["png"]).resolve()
                        if pdf_path == png_path:
                            errors.append(
                                f"{prefix}.generated_files.{group} PDF and PNG paths must differ"
                            )

    summary = payload.get("run_summary")
    if isinstance(summary, dict):
        expected_counts = {
            "n_targets_total": len(results),
            "n_targets_pass": verdict_counts["pass"],
            "n_targets_fail": verdict_counts["fail"],
            "n_targets_needs_human_review": verdict_counts["needs_human_review"],
            "n_targets_blocked": verdict_counts["blocked"],
        }
        for key, expected in expected_counts.items():
            if summary.get(key) != expected:
                errors.append(
                    f"run_summary.{key} is {summary.get(key)!r}; expected {expected}"
                )
        if independence_values:
            expected_aggregate = max(
                independence_values,
                key=lambda value: INDEPENDENCE_ORDER[value],
            )
            if summary.get("derivation_independence_aggregate") != expected_aggregate:
                errors.append(
                    "run_summary.derivation_independence_aggregate is inconsistent "
                    f"with results; expected {expected_aggregate!r}"
                )
    else:
        errors.append("run_summary must be an object")

    if any(
        isinstance(item, dict)
        and isinstance(item.get("verdict"), str)
        and item.get("verdict") in {"fail", "needs_human_review", "blocked"}
        for item in results
    ) and not payload.get("diagnostic_file"):
        errors.append("diagnostic_file is required when any target is not pass")

    started_at = payload.get("started_at")
    finished_at = payload.get("finished_at")
    if isinstance(started_at, str) and isinstance(finished_at, str):
        try:
            started = datetime.strptime(started_at, "%Y-%m-%dT%H:%M:%SZ")
            finished = datetime.strptime(finished_at, "%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            errors.append("started_at/finished_at contain an invalid calendar timestamp")
        else:
            if finished < started:
                errors.append("finished_at precedes started_at")

    if project_dir is not None and "diagnostic_file" in payload:
        run_root = expected_run_dir or (
            project_dir / "reproduction" / "runs" / str(repro_id)
        )
        file_error = _contained_file(
            project_dir,
            payload.get("diagnostic_file"),
            run_root,
        )
        if file_error:
            errors.append(f"diagnostic_file: {file_error}")

    return errors
