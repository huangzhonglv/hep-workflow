from __future__ import annotations

from copy import deepcopy

import numpy as np
import pandas as pd
import pytest

from scripts import _compare_metrics as metrics_helpers
from scripts._compare_figures import _boundary_reference_residuals
from scripts._reproduction_result_validation import _metric_contract_errors
from scripts.compare_to_reference import compute_verdict
from tests.unit.compare_reference_fixtures import enrich_target


def _parametric_target(*, tolerance: float = 0.01) -> dict[str, object]:
    target: dict[str, object] = {
        "id": "parametric-loop",
        "kind": "parametric_curve",
        "x_param": "curve_x",
        "y_param": "curve_y",
        "curve_parameter": "path_t",
        "scan_parameters": ["path_t", "hidden"],
        "observables": ["curve_x", "curve_y"],
        "fixed": {"hidden": 1.0},
        "constraints_in_paper": [],
        "data_file": "literature/digitized/parametric-loop.csv",
        "tolerance": {"kind": "normalized_distance", "value": tolerance},
        "curve_representation": "ordered_parametric_xy",
        "curve_closed": True,
        "parameter_domain": {"parameter_min": 0.0, "parameter_max": 1.0},
        "coordinate_scales": {"curve_x": 1.0, "curve_y": 1.0},
    }
    target = enrich_target(target)
    normalization = target["normalization"]
    for key in ("source_units", "canonical_units"):
        normalization[key]["path_t"] = "dimensionless"
    normalization["conversions"]["path_t"] = {
        "operation": "linear",
        "factor": 1.0,
        "offset": 0.0,
    }
    return target


def _curve_frame(
    parameter: list[float],
    points: list[tuple[float, float]],
    *,
    hidden: float | None = None,
) -> pd.DataFrame:
    payload: dict[str, object] = {
        "path_t": parameter,
        "curve_x": [point[0] for point in points],
        "curve_y": [point[1] for point in points],
    }
    if hidden is not None:
        payload["hidden"] = [hidden] * len(parameter)
    return pd.DataFrame(payload)


def _diamond_reference() -> pd.DataFrame:
    return _curve_frame(
        [0.0, 0.25, 0.5, 0.75, 1.0],
        [(1.0, 0.0), (0.0, 1.0), (-1.0, 0.0), (0.0, -1.0), (1.0, 0.0)],
    )


def _subdivided_diamond(*, offset: float = 0.0) -> pd.DataFrame:
    points = [
        (1.0, 0.0),
        (0.5, 0.5),
        (0.0, 1.0),
        (-0.5, 0.5),
        (-1.0, 0.0),
        (-0.5, -0.5),
        (0.0, -1.0),
        (0.5, -0.5),
        (1.0, 0.0),
    ]
    shifted = [(x, y + offset) for x, y in points]
    return _curve_frame(
        [0.0, 0.1, 0.23, 0.38, 0.5, 0.64, 0.79, 0.91, 1.0],
        shifted,
        hidden=1.0,
    )


def test_parametric_curve_is_reparameterization_and_subdivision_invariant() -> None:
    metrics, comparison = metrics_helpers.parametric_curve_metrics(
        _subdivided_diamond(),
        _diamond_reference(),
        _parametric_target(tolerance=0.0),
    )

    assert metrics["max_normalized_hausdorff_distance"] == 0.0
    assert metrics["max_normalized_hausdorff_distance_upper_bound"] == 0.0
    assert metrics["distance_within_tolerance_proven"] == 1
    assert comparison.reference_closed == {"curve": True}
    assert not _metric_contract_errors(
        metrics,
        kind="parametric_curve",
        verdict="pass",
        prefix="metrics",
        tolerance={"kind": "normalized_distance", "value": 0.0},
    )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda frame: frame.iloc[:-1].copy(), "exactly cover parameter_domain endpoints"),
        (
            lambda frame: frame.assign(path_t=[0.0, 0.1, 0.23, 0.38, 0.5, 0.64, 0.79, 0.79, 1.0]),
            "curve_parameter values must be unique",
        ),
    ],
)
def test_parametric_curve_blocks_incomplete_or_duplicate_parameter_evidence(
    mutation,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        metrics_helpers.parametric_curve_metrics(
            mutation(_subdivided_diamond()),
            _diamond_reference(),
            _parametric_target(),
        )


def test_parametric_curve_uses_exact_hidden_slice() -> None:
    exact = _subdivided_diamond()
    near = exact.copy()
    near["hidden"] = np.nextafter(1.0, 2.0)
    near["curve_y"] = near["curve_y"] + 100.0
    metrics, _ = metrics_helpers.parametric_curve_metrics(
        pd.concat([exact, near], ignore_index=True),
        _diamond_reference(),
        _parametric_target(tolerance=0.0),
    )

    assert metrics["scan_node_count"] == len(exact)
    assert metrics["max_normalized_hausdorff_distance"] == 0.0


def test_parametric_curve_blocks_when_error_bound_straddles_tolerance() -> None:
    target = _parametric_target(tolerance=1.0e-4)
    metrics, _ = metrics_helpers.parametric_curve_metrics(
        _subdivided_diamond(offset=5.0e-5),
        _diamond_reference(),
        target,
    )

    assert metrics["max_normalized_hausdorff_distance_lower_bound"] <= 1.0e-4
    assert metrics["max_normalized_hausdorff_distance_upper_bound"] > 1.0e-4
    assert metrics["distance_decision_defined"] == 0
    assert compute_verdict(
        blocked=False,
        target_kind="parametric_curve",
        tolerance=target["tolerance"],
        metrics=metrics,
        completeness=None,
        ceiling="pass",
    ) == "blocked"


def test_geometry_verdict_and_result_contract_reject_forged_bound_flags() -> None:
    metric_values, _ = metrics_helpers.parametric_curve_metrics(
        _subdivided_diamond(),
        _diamond_reference(),
        _parametric_target(tolerance=0.0),
    )
    forged = deepcopy(metric_values)
    forged["max_normalized_hausdorff_distance_upper_bound"] = 0.2
    forged["max_normalized_hausdorff_distance_uncertainty"] = 0.2
    forged["polyline_sampling_error_bound"] = 0.2
    forged["distance_within_tolerance_proven"] = 1
    forged["distance_exceeds_tolerance_proven"] = 0
    forged["distance_decision_defined"] = 1
    forged["polyline_sample_count"] += 1
    tolerance = {"kind": "normalized_distance", "value": 0.1}

    assert compute_verdict(
        blocked=False,
        target_kind="parametric_curve",
        tolerance=tolerance,
        metrics=forged,
        completeness=None,
        ceiling="pass",
    ) == "blocked"
    errors = _metric_contract_errors(
        forged,
        kind="parametric_curve",
        verdict="pass",
        prefix="metrics",
        tolerance=tolerance,
    )
    assert any("decision flags contradict bounds and tolerance" in item for item in errors)
    assert any("n_points_compared must equal" in item for item in errors)


@pytest.mark.parametrize(
    ("closed", "points", "message"),
    [
        (
            True,
            [(0.0, 0.0), (1.0, 0.0), (0.0, 0.0)],
            "at least three distinct vertices",
        ),
        (
            False,
            [(0.0, 0.0), (1.0, 0.0), (0.0, 0.0)],
            "overlapping adjacent segments",
        ),
    ],
)
def test_parametric_curve_blocks_degenerate_retraced_geometry(
    closed: bool,
    points: list[tuple[float, float]],
    message: str,
) -> None:
    target = _parametric_target()
    target["curve_closed"] = closed
    frame = _curve_frame([0.0, 0.5, 1.0], points, hidden=1.0)

    with pytest.raises(ValueError, match=message):
        metrics_helpers.parametric_curve_metrics(frame, frame, target)


def test_parametric_curve_does_not_zero_real_distance_at_large_offset() -> None:
    offset = 1.0e12
    reference = _curve_frame(
        [0.0, 0.25, 0.5, 0.75, 1.0],
        [
            (offset + 1.0, offset),
            (offset, offset + 1.0),
            (offset - 1.0, offset),
            (offset, offset - 1.0),
            (offset + 1.0, offset),
        ],
    )
    predicted = reference.copy()
    predicted["curve_y"] += 0.005
    predicted["hidden"] = 1.0

    metric_values, _ = metrics_helpers.parametric_curve_metrics(
        predicted,
        reference,
        _parametric_target(tolerance=0.0),
    )

    assert metric_values["max_normalized_hausdorff_distance"] > 0.004
    assert metric_values["distance_exceeds_tolerance_proven"] == 1


def test_parametric_curve_rejects_extreme_finite_self_intersection() -> None:
    angles = np.linspace(np.pi / 2.0, np.pi / 2.0 + 2.0 * np.pi, 5, endpoint=False)
    vertices = np.column_stack((np.cos(angles), np.sin(angles)))
    star = vertices[[0, 2, 4, 1, 3]] * 1.0e308
    frame = _curve_frame(
        [0.0, 0.25, 0.5, 0.75, 1.0],
        [(float(x_value), float(y_value)) for x_value, y_value in star],
        hidden=1.0,
    )

    with pytest.raises(ValueError, match="self-intersection"):
        metrics_helpers.parametric_curve_metrics(
            frame,
            frame,
            _parametric_target(),
        )


def _face(
    face_id: str,
    *,
    parent_id: str | None,
    excluded_side: str,
    probe: tuple[float, float],
) -> dict[str, object]:
    return {
        "id": face_id,
        "parent_id": parent_id,
        "closed": True,
        "excluded_side": excluded_side,
        "excluded_probe": {"x": probe[0], "y": probe[1]},
    }


def _face_target(faces: list[dict[str, object]], *, tolerance: float = 0.0) -> dict[str, object]:
    return enrich_target(
        {
            "id": "multi-face",
            "kind": "exclusion_region",
            "x_param": "x",
            "y_param": "y",
            "scan_parameters": ["x", "y"],
            "observables": ["region_status"],
            "fixed": {},
            "constraints_in_paper": [],
            "data_file": "literature/digitized/multi-face.csv",
            "tolerance": {"kind": "normalized_distance", "value": tolerance},
            "boundary": {
                "mode": "precomputed_boundary",
                "membership_column": "is_boundary",
                "membership_value": 1,
                "component_column": "component_id",
                "order_column": "boundary_order",
                "closed_column": "boundary_closed",
                "reference_order_column": "point_order",
                "reference_closed_column": "is_closed",
                "region_column": "region_status",
                "excluded_value": "excluded",
                "reference_faces": faces,
            },
            "coordinate_scales": {"x": 1.0, "y": 1.0},
        }
    )


def _reference_components(
    components: dict[str, list[tuple[float, float]]],
) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "x": point[0],
                "y": point[1],
                "component_id": component_id,
                "point_order": order,
                "is_closed": True,
            }
            for component_id, points in components.items()
            for order, point in enumerate(points)
        ]
    )


def _predicted_components(
    components: dict[str, list[tuple[float, float]]],
    probes: list[tuple[float, float]],
) -> pd.DataFrame:
    rows = [
        {
            "x": point[0],
            "y": point[1],
            "is_boundary": 1,
            "component_id": component_id,
            "boundary_order": order,
            "boundary_closed": True,
            "region_status": "boundary",
        }
        for component_id, points in components.items()
        for order, point in enumerate(points)
    ]
    rows.extend(
        {
            "x": point[0],
            "y": point[1],
            "is_boundary": 0,
            "component_id": "probe",
            "boundary_order": 0,
            "boundary_closed": True,
            "region_status": "excluded",
        }
        for point in probes
    )
    return pd.DataFrame(rows)


def _square(x_min: float, y_min: float, x_max: float, y_max: float):
    return [
        (x_min, y_min),
        (x_max, y_min),
        (x_max, y_max),
        (x_min, y_max),
    ]


def test_disconnected_exclusion_faces_compare_with_complete_side_evidence() -> None:
    components = {
        "left": _square(0.0, 0.0, 1.0, 1.0),
        "right": _square(3.0, 0.0, 4.0, 1.0),
    }
    probes = [(0.5, 0.5), (3.5, 0.5)]
    target = _face_target(
        [
            _face("left", parent_id=None, excluded_side="interior", probe=probes[0]),
            _face("right", parent_id=None, excluded_side="interior", probe=probes[1]),
        ]
    )

    metrics, comparison = metrics_helpers.exclusion_region_metrics(
        _predicted_components(components, probes),
        _reference_components(components),
        target,
    )

    assert metrics["reference_component_count"] == 2
    assert metrics["face_assignment_defined"] == 1
    assert metrics["face_parent_topology_match"] == 1
    assert metrics["face_probe_coverage_ratio"] == 1.0
    assert metrics["max_normalized_hausdorff_distance"] == 0.0
    assert len(_boundary_reference_residuals(comparison)) == 8
    assert not _metric_contract_errors(
        metrics,
        kind="exclusion_region",
        verdict="pass",
        prefix="metrics",
        tolerance={"kind": "normalized_distance", "value": 0.0},
    )


def test_holed_exclusion_face_parent_and_side_semantics_are_verified() -> None:
    components = {
        "outer": _square(0.0, 0.0, 4.0, 4.0),
        "hole": _square(1.0, 1.0, 3.0, 3.0),
    }
    probes = [(0.5, 3.5), (0.25, 0.25)]
    target = _face_target(
        [
            _face("outer", parent_id=None, excluded_side="interior", probe=probes[0]),
            _face("hole", parent_id="outer", excluded_side="exterior", probe=probes[1]),
        ]
    )

    metrics, _ = metrics_helpers.exclusion_region_metrics(
        _predicted_components(components, probes),
        _reference_components(components),
        target,
    )

    assert metrics["face_parent_topology_match"] == 1
    assert metrics["verified_face_probe_count"] == 2
    assert metrics["excluded_probe_match"] == 1


def test_nested_faces_may_share_one_authoritative_excluded_point() -> None:
    components = {
        "outer": _square(0.0, 0.0, 4.0, 4.0),
        "hole": _square(1.0, 1.0, 3.0, 3.0),
    }
    shared_probe = (0.5, 0.5)
    target = _face_target(
        [
            _face(
                "outer",
                parent_id=None,
                excluded_side="interior",
                probe=shared_probe,
            ),
            _face(
                "hole",
                parent_id="outer",
                excluded_side="exterior",
                probe=shared_probe,
            ),
        ]
    )

    metric_values, _ = metrics_helpers.exclusion_region_metrics(
        _predicted_components(components, [shared_probe]),
        _reference_components(components),
        target,
    )

    assert metric_values["verified_face_probe_count"] == 2
    assert metric_values["face_probe_coverage_ratio"] == 1.0


def test_reference_face_probe_on_wrong_side_blocks_comparison() -> None:
    components = {"outer": _square(0.0, 0.0, 2.0, 2.0)}
    probe = (1.0, 1.0)
    target = _face_target(
        [_face("outer", parent_id=None, excluded_side="exterior", probe=probe)]
    )

    with pytest.raises(ValueError, match="excluded_probe contradicts excluded_side"):
        metrics_helpers.exclusion_region_metrics(
            _predicted_components(components, [probe]),
            _reference_components(components),
            target,
        )


def test_extreme_finite_face_probe_on_boundary_remains_undefined() -> None:
    scale = 1.0e308
    components = {
        "outer": [
            (-scale, -scale),
            (scale, -scale),
            (scale, scale),
            (-scale, scale),
        ]
    }
    probe = (scale, 0.0)
    with pytest.raises(ValueError, match="lies on a boundary"):
        metrics_helpers._point_in_closed_component(
            np.asarray(probe, dtype=float),
            np.asarray(components["outer"], dtype=float),
            label="extreme probe",
        )


def test_reference_face_parent_graph_must_match_digitized_geometry() -> None:
    components = {
        "left": _square(0.0, 0.0, 1.0, 1.0),
        "right": _square(3.0, 0.0, 4.0, 1.0),
    }
    probes = [(0.5, 0.5), (3.5, 0.5)]
    target = _face_target(
        [
            _face("left", parent_id=None, excluded_side="interior", probe=probes[0]),
            _face("right", parent_id="left", excluded_side="exterior", probe=probes[1]),
        ]
    )

    with pytest.raises(ValueError, match="nesting does not match"):
        metrics_helpers.exclusion_region_metrics(
            _predicted_components(components, probes),
            _reference_components(components),
            target,
        )


def test_exclusion_face_blocks_zero_area_geometry() -> None:
    components = {
        "face": [(0.0, 0.0), (1.0, 0.0), (2.0, 0.0), (3.0, 0.0)]
    }
    probe = (0.5, 0.0)
    target = _face_target(
        [_face("face", parent_id=None, excluded_side="interior", probe=probe)]
    )

    with pytest.raises(
        ValueError,
        match="self-intersection|overlapping adjacent segments|zero enclosed area",
    ):
        metrics_helpers.exclusion_region_metrics(
            _predicted_components(components, [probe]),
            _reference_components(components),
            target,
        )


def test_missing_predicted_face_is_a_fail_not_partial_success() -> None:
    reference_components = {
        "left": _square(0.0, 0.0, 1.0, 1.0),
        "right": _square(3.0, 0.0, 4.0, 1.0),
    }
    predicted_components = {"left": reference_components["left"]}
    probes = [(0.5, 0.5), (3.5, 0.5)]
    target = _face_target(
        [
            _face("left", parent_id=None, excluded_side="interior", probe=probes[0]),
            _face("right", parent_id=None, excluded_side="interior", probe=probes[1]),
        ],
        tolerance=10.0,
    )

    metrics, _ = metrics_helpers.exclusion_region_metrics(
        _predicted_components(predicted_components, probes),
        _reference_components(reference_components),
        target,
    )

    assert metrics["component_count_match"] == 0
    assert metrics["face_assignment_defined"] == 0
    assert compute_verdict(
        blocked=False,
        target_kind="exclusion_region",
        tolerance=target["tolerance"],
        metrics=metrics,
        completeness=None,
        ceiling="pass",
    ) == "fail"


def test_legacy_multi_component_target_without_faces_remains_blocked() -> None:
    components = {
        "left": _square(0.0, 0.0, 1.0, 1.0),
        "right": _square(3.0, 0.0, 4.0, 1.0),
    }
    target = _face_target(
        [
            _face("left", parent_id=None, excluded_side="interior", probe=(0.5, 0.5)),
            _face("right", parent_id=None, excluded_side="interior", probe=(3.5, 0.5)),
        ]
    )
    legacy = deepcopy(target)
    legacy["boundary"].pop("reference_faces")
    legacy["boundary"]["reference_excluded_probe"] = {"x": 0.5, "y": 0.5}

    with pytest.raises(ValueError, match="requires complete reference_faces"):
        metrics_helpers.exclusion_region_metrics(
            _predicted_components(components, [(0.5, 0.5)]),
            _reference_components(components),
            legacy,
        )
