from __future__ import annotations

import math
import numpy as np
import pandas as pd
import pytest

from scripts import _compare_metrics as metrics_helpers
from scripts import compare_to_reference
from tests.unit.compare_reference_fixtures import enrich_target


def _curve_target(**overrides: object) -> dict[str, object]:
    target: dict[str, object] = {
        "id": "curve-policy",
        "kind": "figure_curve",
        "x_param": "x",
        "y_param": "observable",
        "observables": ["observable"],
        "fixed": {},
        "constraints_in_paper": [],
        "data_file": "literature/digitized/curve-policy.csv",
        "tolerance": {"kind": "relative", "value": 0.05},
        "scan_parameters": ["x"],
        "comparison_domain": {"x_min": 0.0, "x_max": 2.0},
        "curve_representation": "single_valued_y_of_x",
    }
    target.update(overrides)
    return enrich_target(target)


def _curve_verdict(target: dict[str, object], metric_values: dict[str, object]) -> str:
    return compare_to_reference.compute_verdict(
        blocked=False,
        target_kind="figure_curve",
        tolerance=target["tolerance"],
        metrics=metric_values,
        completeness=None,
        ceiling="pass",
    )


def _precomputed_target(
    *,
    probe: tuple[float, float],
    tolerance: float = 0.1,
    coordinate_scale: float = 1.0,
) -> dict[str, object]:
    target: dict[str, object] = {
        "id": "boundary-policy",
        "kind": "exclusion_region",
        "x_param": "x",
        "y_param": "y",
        "observables": [],
        "fixed": {},
        "constraints_in_paper": [],
        "data_file": "literature/digitized/boundary-policy.csv",
        "tolerance": {"kind": "normalized_distance", "value": tolerance},
        "scan_parameters": ["x", "y"],
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
            "reference_excluded_probe": {"x": probe[0], "y": probe[1]},
        },
        "coordinate_scales": {"x": coordinate_scale, "y": coordinate_scale},
    }
    return enrich_target(target)


def _predicted_boundary(
    points: list[tuple[float, float]],
    *,
    components: list[str] | None = None,
    closed: bool | list[bool] = False,
) -> pd.DataFrame:
    labels = components or ["outer"] * len(points)
    if isinstance(closed, bool):
        closed_values = [closed] * len(points)
    else:
        closed_values = closed
    order_by_component: dict[str, int] = {}
    orders: list[int] = []
    for component in labels:
        orders.append(order_by_component.get(component, 0))
        order_by_component[component] = orders[-1] + 1
    return pd.DataFrame(
        {
            "x": [point[0] for point in points],
            "y": [point[1] for point in points],
            "is_boundary": [1] * len(points),
            "component_id": labels,
            "boundary_order": orders,
            "boundary_closed": closed_values,
            "region_status": ["excluded"] * len(points),
        }
    )


def _reference_boundary(
    points: list[tuple[float, float]],
    *,
    components: list[str] | None = None,
    closed: bool | list[bool] = False,
) -> pd.DataFrame:
    labels = components or ["outer"] * len(points)
    if isinstance(closed, bool):
        closed_values = [closed] * len(points)
    else:
        closed_values = closed
    order_by_component: dict[str, int] = {}
    orders: list[int] = []
    for component in labels:
        orders.append(order_by_component.get(component, 0))
        order_by_component[component] = orders[-1] + 1
    return pd.DataFrame(
        {
            "x": [point[0] for point in points],
            "y": [point[1] for point in points],
            "component_id": labels,
            "point_order": orders,
            "is_closed": closed_values,
        }
    )


def test_curve_compares_union_of_knots_and_detects_internal_excursion() -> None:
    target = _curve_target()
    scan = pd.DataFrame(
        {"x": [0.0, 1.0, 2.0], "observable": [1.0, 10.0, 1.0]}
    )
    reference = pd.DataFrame({"x": [0.0, 2.0], "observable": [1.0, 1.0]})

    metric_values, comparison = metrics_helpers.figure_curve_metrics(
        scan, reference, target
    )

    assert comparison.x.tolist() == [0.0, 1.0, 2.0]
    assert metric_values["scan_node_count"] == 3
    assert metric_values["reference_node_count"] == 2
    assert metric_values["max_relative_error"] == pytest.approx(9.0)
    assert _curve_verdict(target, metric_values) == "fail"


@pytest.mark.parametrize(
    ("scan", "reference", "error"),
    [
        (
            pd.DataFrame(
                {"x": [0.1, 1.0, 2.0], "observable": [1.0, 1.0, 1.0]}
            ),
            pd.DataFrame({"x": [0.0, 2.0], "observable": [1.0, 1.0]}),
            "scan must exactly cover comparison_domain endpoints",
        ),
        (
            pd.DataFrame({"x": [0.0, 2.0], "observable": [1.0, 1.0]}),
            pd.DataFrame(
                {"x": [-0.1, 0.0, 2.0], "observable": [1.0, 1.0, 1.0]}
            ),
            "outside comparison_domain",
        ),
        (
            pd.DataFrame(
                {"x": [0.0, 1.0, 1.0, 2.0], "observable": [1.0] * 4}
            ),
            pd.DataFrame({"x": [0.0, 2.0], "observable": [1.0, 1.0]}),
            "scan contains duplicate x values",
        ),
        (
            pd.DataFrame({"x": [0.0, 2.0], "observable": [1.0, 1.0]}),
            pd.DataFrame(
                {"x": [0.0, 1.0, 1.0, 2.0], "observable": [1.0] * 4}
            ),
            "not a single-valued",
        ),
    ],
)
def test_curve_requires_full_declared_domain_and_unique_x(
    scan: pd.DataFrame,
    reference: pd.DataFrame,
    error: str,
) -> None:
    with pytest.raises(ValueError, match=error):
        metrics_helpers.figure_curve_metrics(scan, reference, _curve_target())


def test_curve_rejects_one_ulp_inward_domain_endpoints() -> None:
    inward_min = np.nextafter(0.0, math.inf)
    inward_max = np.nextafter(2.0, 0.0)
    scan = pd.DataFrame(
        {"x": [inward_min, 1.0, inward_max], "observable": [1.0, 1.0, 1.0]}
    )
    reference = scan.copy()

    with pytest.raises(ValueError, match="exactly cover comparison_domain endpoints"):
        metrics_helpers.figure_curve_metrics(scan, reference, _curve_target())


def test_curve_hidden_parameter_slice_is_exact_not_approximate() -> None:
    target = _curve_target(
        scan_parameters=["x", "hidden"],
        fixed={"hidden": 1.0},
    )
    near = np.nextafter(1.0, 2.0)
    scan = pd.DataFrame(
        {
            "x": [0.0, 1.0, 2.0, 0.0, 1.0, 2.0],
            "hidden": [1.0, 1.0, 1.0, near, near, near],
            "observable": [1.0, 2.0, 3.0, 100.0, 200.0, 300.0],
        }
    )
    reference = pd.DataFrame(
        {"x": [0.0, 1.0, 2.0], "observable": [1.0, 2.0, 3.0]}
    )

    metric_values, _ = metrics_helpers.figure_curve_metrics(scan, reference, target)

    assert metric_values["scan_node_count"] == 3
    assert metric_values["max_absolute_error"] == 0.0

    unfixed = _curve_target(scan_parameters=["x", "hidden"])
    with pytest.raises(ValueError, match="exact fixed slice for: hidden"):
        metrics_helpers.figure_curve_metrics(scan, reference, unfixed)


@pytest.mark.parametrize(
    ("reference_values", "expected_zero_values", "expected_crossings"),
    [
        ([1.0, 0.0, 1.0], 1, 0),
        ([-1.0, 1.0], 1, 1),
    ],
)
def test_relative_curve_with_zero_evidence_is_explicitly_blocked(
    reference_values: list[float],
    expected_zero_values: int,
    expected_crossings: int,
) -> None:
    x_values = np.linspace(0.0, 2.0, len(reference_values))
    frame = pd.DataFrame({"x": x_values, "observable": reference_values})
    target = _curve_target()

    metric_values, _ = metrics_helpers.figure_curve_metrics(frame, frame, target)

    assert metric_values["n_zero_reference_values"] == expected_zero_values
    assert metric_values["n_zero_reference_crossings"] == expected_crossings
    assert metric_values["relative_error_defined"] == 0
    assert _curve_verdict(target, metric_values) == "blocked"


def test_subnormal_reference_is_not_treated_as_zero_or_epsilon_clamped() -> None:
    subnormal = np.nextafter(0.0, 1.0)
    target = _curve_target(comparison_domain={"x_min": 0.0, "x_max": 1.0})
    reference = pd.DataFrame(
        {"x": [0.0, 1.0], "observable": [subnormal, subnormal]}
    )
    scan = pd.DataFrame(
        {"x": [0.0, 1.0], "observable": [2.0 * subnormal, subnormal]}
    )

    metric_values, _ = metrics_helpers.figure_curve_metrics(scan, reference, target)

    assert metric_values["n_zero_reference_values"] == 0
    assert metric_values["relative_error_defined"] == 1
    assert metric_values["max_relative_error"] == 1.0


def test_identical_curve_over_opposite_float_extremes_compares_exactly() -> None:
    target = _curve_target(
        comparison_domain={"x_min": -1.0e308, "x_max": 1.0e308}
    )
    frame = pd.DataFrame(
        {"x": [-1.0e308, 1.0e308], "observable": [-1.0e308, 1.0e308]}
    )

    metric_values, _ = metrics_helpers.figure_curve_metrics(frame, frame, target)

    assert metric_values["max_absolute_error"] == 0.0
    assert metric_values["max_relative_error"] == 0.0
    assert metric_values["n_zero_reference_crossings"] == 1
    assert metric_values["relative_error_defined"] == 0


def test_benchmark_point_and_keyed_set_have_disjoint_row_count_semantics() -> None:
    scan = pd.DataFrame(
        {
            "M": [100.0, 200.0],
            "g": [0.1, 0.2],
            "sigma": [1.0, 2.0],
        }
    )
    one_row = scan.iloc[[0]].copy()
    two_rows = scan.copy()
    benchmark = enrich_target(
        {
            "id": "benchmark-one",
            "kind": "benchmark_point",
            "x_param": "M",
            "y_param": "g",
            "match_columns": ["M", "g"],
            "observables": ["sigma"],
            "fixed": {},
            "constraints_in_paper": [],
            "data_file": "literature/digitized/benchmark-one.csv",
            "tolerance": {"kind": "relative", "value": 0.01},
            "scan_parameters": ["M", "g"],
        }
    )
    keyed = {**benchmark, "id": "benchmark-many", "kind": "keyed_benchmark_set"}

    benchmark_metrics, _ = metrics_helpers.benchmark_point_metrics(
        scan, one_row, benchmark
    )
    assert benchmark_metrics["n_points_compared"] == 1
    with pytest.raises(ValueError, match="requires exactly one digitized row"):
        metrics_helpers.benchmark_point_metrics(scan, two_rows, benchmark)

    incomplete = metrics_helpers.keyed_benchmark_metrics(scan, one_row, keyed)
    assert incomplete.completeness["complete"] is False
    assert incomplete.completeness["blocking_reasons"] == [
        "keyed_benchmark_set_requires_multiple_rows"
    ]
    complete = metrics_helpers.keyed_benchmark_metrics(scan, two_rows, keyed)
    assert complete.completeness["complete"] is True
    assert complete.metrics["n_points_compared"] == 2


def test_same_open_polyline_with_different_subdivision_has_zero_distance() -> None:
    reference = _reference_boundary([(0.0, 0.0), (1.0, 0.0), (2.0, 0.0)])
    predicted = _predicted_boundary(
        [(0.0, 0.0), (0.5, 0.0), (1.0, 0.0), (1.5, 0.0), (2.0, 0.0)]
    )
    target = _precomputed_target(probe=(0.0, 0.0))

    metric_values, _ = metrics_helpers.exclusion_region_metrics(
        predicted, reference, target
    )

    assert metric_values["max_normalized_hausdorff_distance"] == 0.0
    assert metric_values["max_normalized_hausdorff_distance_lower_bound"] == 0.0
    assert metric_values["max_normalized_hausdorff_distance_upper_bound"] == 0.0
    assert metric_values["distance_within_tolerance_proven"] == 1
    assert metric_values["distance_decision_defined"] == 1


@pytest.mark.parametrize("scale", [1.0, 1.0e-300])
def test_closed_polyline_accepts_roundoff_duplicate_endpoint_at_any_scale(
    scale: float,
) -> None:
    first = scale
    near_first = np.nextafter(first, np.inf)
    reference_points = [
        (first, first),
        (2.0 * scale, first),
        (2.0 * scale, 2.0 * scale),
        (first, 2.0 * scale),
    ]
    predicted_points = [*reference_points, (near_first, first)]
    reference = _reference_boundary(reference_points, closed=True)
    predicted = _predicted_boundary(predicted_points, closed=True)
    target = _precomputed_target(
        probe=reference_points[0],
        coordinate_scale=scale,
    )

    metric_values, comparison = metrics_helpers.exclusion_region_metrics(
        predicted, reference, target
    )

    assert metric_values["max_normalized_hausdorff_distance"] == 0.0
    assert metric_values["closed_topology_match"] == 1
    assert comparison.predicted_closed == {"outer": True}


def test_closed_bowtie_boundary_is_rejected_as_self_intersecting() -> None:
    reference = _reference_boundary(
        [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)],
        closed=True,
    )
    bowtie = _predicted_boundary(
        [(0.0, 0.0), (1.0, 1.0), (0.0, 1.0), (1.0, 0.0)],
        closed=True,
    )
    target = _precomputed_target(probe=(0.0, 0.0))

    with pytest.raises(ValueError, match="predicted boundary contains a self-intersection"):
        metrics_helpers.exclusion_region_metrics(bowtie, reference, target)


def test_multi_component_boundary_without_face_contract_is_typed_block() -> None:
    points = [(0.0, 0.0), (1.0, 0.0), (0.0, 2.0), (1.0, 2.0)]
    components = ["lower", "lower", "upper", "upper"]
    reference = _reference_boundary(points, components=components)
    predicted = _predicted_boundary(points, components=components)
    target = _precomputed_target(probe=(0.0, 0.0))

    with pytest.raises(ValueError, match="requires complete reference_faces"):
        metrics_helpers.exclusion_region_metrics(predicted, reference, target)


def test_constraint_transition_mode_is_typed_block_not_point_cloud_metric() -> None:
    target = {
        "id": "transition-policy",
        "kind": "exclusion_region",
        "x_param": "x",
        "y_param": "y",
        "scan_parameters": ["x", "y"],
        "fixed": {},
        "boundary": {
            "mode": "constraint_verdict_transition",
            "constraint_id": "limit",
            "connectivity": "four",
        },
    }
    scan = pd.DataFrame(
        {
            "x": [0.0, 1.0, 0.0, 1.0],
            "y": [0.0, 0.0, 1.0, 1.0],
            "limit_verdict": ["excluded", "allowed", "excluded", "allowed"],
        }
    )

    with pytest.raises(
        ValueError,
        match="blocked until transition edges are assembled into ordered boundary paths",
    ):
        metrics_helpers.extract_contour_points(scan, target)


def test_boolean_curve_coordinate_is_not_coerced_to_numeric() -> None:
    target = _curve_target(comparison_domain={"x_min": 0.0, "x_max": 1.0})
    scan = pd.DataFrame({"x": [False, True], "observable": [1.0, 2.0]})
    reference = pd.DataFrame({"x": [0.0, 1.0], "observable": [1.0, 2.0]})

    with pytest.raises(ValueError, match="contains boolean data"):
        metrics_helpers.figure_curve_metrics(scan, reference, target)


def test_exclusion_distance_is_tolerance_invariant_and_bounds_drive_flags() -> None:
    reference = _reference_boundary([(0.0, 0.0), (1.0, 0.0)])
    predicted = _predicted_boundary([(0.0, 0.1), (1.0, 0.1)])

    loose, _ = metrics_helpers.exclusion_region_metrics(
        predicted,
        reference,
        _precomputed_target(probe=(0.0, 0.1), tolerance=0.2),
    )
    strict, _ = metrics_helpers.exclusion_region_metrics(
        predicted,
        reference,
        _precomputed_target(probe=(0.0, 0.1), tolerance=0.05),
    )

    assert loose["max_normalized_hausdorff_distance"] == pytest.approx(0.1)
    assert strict["max_normalized_hausdorff_distance"] == pytest.approx(0.1)
    assert loose["max_normalized_hausdorff_distance"] == pytest.approx(
        strict["max_normalized_hausdorff_distance"]
    )
    assert loose["max_normalized_hausdorff_distance_lower_bound"] == pytest.approx(0.1)
    assert strict["max_normalized_hausdorff_distance_lower_bound"] == pytest.approx(0.1)
    assert loose["distance_within_tolerance_proven"] == 1
    assert loose["distance_exceeds_tolerance_proven"] == 0
    assert loose["distance_decision_defined"] == 1
    assert strict["distance_within_tolerance_proven"] == 0
    assert strict["distance_exceeds_tolerance_proven"] == 1
    assert strict["distance_decision_defined"] == 1


def test_real_observable_threshold_contour_canonicalizes_vertex_duplicates() -> None:
    axis = np.linspace(-1.5, 1.5, 31)
    scan = pd.DataFrame(
        [
            {"x": float(x), "y": float(y), "r2": float(x * x + y * y)}
            for y in axis
            for x in axis
        ]
    )
    target: dict[str, object] = {
        "id": "threshold-circle",
        "kind": "exclusion_region",
        "x_param": "x",
        "y_param": "y",
        "observables": ["r2"],
        "fixed": {},
        "constraints_in_paper": [],
        "data_file": "literature/digitized/threshold-circle.csv",
        "tolerance": {"kind": "normalized_distance", "value": 0.01},
        "scan_parameters": ["x", "y"],
        "boundary": {
            "mode": "observable_threshold",
            "observable": "r2",
            "operator": "less_than_or_equal",
            "value": 1.0,
            "value_unit": "dimensionless",
            "component_column": "component_id",
            "reference_order_column": "point_order",
            "reference_closed_column": "is_closed",
            "reference_excluded_probe": {"x": 0.0, "y": 0.0},
        },
        "coordinate_scales": {"x": 1.0, "y": 1.0},
    }
    target = enrich_target(target)
    for field in ("source_units", "canonical_units"):
        target["normalization"][field]["r2"] = "dimensionless"
    target["normalization"]["conversions"]["r2"] = {
        "operation": "linear",
        "factor": 1.0,
        "offset": 0.0,
    }

    contour = metrics_helpers.extract_contour_points(scan, target)
    assert set(contour.closed_components.values()) == {True}
    reference_rows: list[dict[str, object]] = []
    for component in sorted(set(contour.component_labels.astype(str))):
        points = contour.points[contour.component_labels.astype(str) == component]
        if metrics_helpers._points_close(points[0], points[-1]):
            points = points[:-1]
        for order, point in enumerate(points):
            reference_rows.append(
                {
                    "x": point[0],
                    "y": point[1],
                    "component_id": component,
                    "point_order": order,
                    "is_closed": True,
                }
            )

    metric_values, _ = metrics_helpers.exclusion_region_metrics(
        scan,
        pd.DataFrame(reference_rows),
        target,
    )

    assert metric_values["closed_topology_match"] == 1
    assert metric_values["distance_within_tolerance_proven"] == 1
    assert metric_values["max_normalized_hausdorff_distance"] == pytest.approx(0.0)


def test_exclusion_normalization_overflow_is_rejected() -> None:
    reference = _reference_boundary([(1.0e308, 1.0), (9.0e307, 2.0)])
    predicted = _predicted_boundary([(8.0e307, 1.0), (7.0e307, 2.0)])
    target = _precomputed_target(probe=(8.0e307, 1.0), tolerance=0.1)
    target["coordinate_scales"] = {"x": 1.0e-300, "y": 1.0}

    with pytest.raises(ValueError, match="normalized exclusion boundary coordinates"):
        metrics_helpers.exclusion_region_metrics(predicted, reference, target)


def test_extreme_relative_rms_uses_stable_scaling() -> None:
    metrics = metrics_helpers.summarize_errors(
        np.asarray([1.0e200, 1.0e200]),
        np.asarray([1.0, 1.0]),
    )

    assert metrics["max_relative_error"] == pytest.approx(1.0e200)
    assert metrics["rms_relative_error"] == pytest.approx(1.0e200)
    assert math.isfinite(metrics["rms_relative_error"])


def test_unrepresentable_absolute_error_is_typed_failure() -> None:
    with pytest.raises(ValueError, match="absolute error exceeds"):
        metrics_helpers.summarize_errors(
            np.asarray([1.0e308]),
            np.asarray([-1.0e308]),
        )


@pytest.mark.parametrize(
    "row",
    ["1,2,3\n", "1\n", "\n"],
)
def test_csv_loader_rejects_ragged_or_blank_data_rows(tmp_path, row) -> None:
    path = tmp_path / "malformed.csv"
    path.write_text("x,y\n" + row, encoding="utf-8")

    with pytest.raises(ValueError, match="CSV (row|has a blank data row)"):
        metrics_helpers.load_csv(path)


def test_precomputed_boundary_selectors_preserve_boolean_numeric_types() -> None:
    target = _precomputed_target(probe=(0.0, 0.0))
    target["boundary"]["membership_value"] = True
    target["boundary"]["excluded_value"] = True
    scan = _predicted_boundary([(0.0, 0.0), (1.0, 0.0)])
    scan["is_boundary"] = 1
    scan["region_status"] = 1

    with pytest.raises(ValueError, match="selected no rows"):
        metrics_helpers.extract_contour_points(scan, target)

    target["boundary"]["membership_value"] = 1
    extracted = metrics_helpers.extract_contour_points(scan, target)
    assert extracted.excluded_probe_match is False


def test_boundary_order_rejects_boolean_as_integer() -> None:
    target = _precomputed_target(probe=(0.0, 0.0))
    scan = _predicted_boundary([(0.0, 0.0), (1.0, 0.0)])
    scan["boundary_order"] = [False, True]

    with pytest.raises(ValueError, match="contains boolean data"):
        metrics_helpers.extract_contour_points(scan, target)


def test_scan_table_join_does_not_equate_boolean_and_numeric_keys() -> None:
    target: dict[str, object] = {
        "id": "typed-join",
        "kind": "scan_table",
        "x_param": "x",
        "y_param": "y",
        "match_columns": ["x", "y", "tag"],
        "observables": ["observable"],
        "scan_parameters": ["x", "y"],
        "fixed": {},
        "constraints_in_paper": [],
        "data_file": "literature/digitized/typed-join.csv",
        "tolerance": {"kind": "relative", "value": 0.1},
    }
    target = enrich_target(target)
    for field in ("source_units", "canonical_units"):
        target["normalization"][field]["tag"] = "categorical"
    target["normalization"]["conversions"]["tag"] = {
        "operation": "linear",
        "factor": 1.0,
        "offset": 0.0,
    }
    scan = pd.DataFrame({"x": [1.0], "y": [2.0], "tag": [1], "observable": [3.0]})
    reference = pd.DataFrame(
        {"x": [1.0], "y": [2.0], "tag": [True], "observable": [3.0]}
    )

    result = metrics_helpers.scan_table_metrics(scan, reference, target)

    assert result.completeness["complete"] is False
    assert "incompatible_match_key_types:tag" in result.completeness["blocking_reasons"]
