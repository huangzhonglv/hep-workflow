from __future__ import annotations

import numpy as np
import pytest


def make_constraint(method: str, policy: str = "forbidden") -> dict:
    return {
        "id": f"interp-{method}-{policy}",
        "type": "upper_limit",
        "implementation_status": "interpolated",
        "interpolation": {
            "method": method,
            "x_parameter": "x",
            "y_quantity": "limit",
            "valid_range": [1.0, 100.0],
            "extrapolation_policy": policy,
        },
    }


@pytest.mark.parametrize(
    ("method", "x_values", "y_values", "x_query", "expected"),
    [
        ("linear", np.array([1.0, 5.0]), np.array([2.0, 10.0]), 2.5, 5.0),
        ("loglog_linear", np.array([1.0, 100.0]), np.array([1.0, 10000.0]), 10.0, 100.0),
        ("log_x_linear", np.array([1.0, 100.0]), np.array([1.0, 5.0]), 10.0, 3.0),
        ("log_y_linear", np.array([1.0, 3.0]), np.array([10.0, 1000.0]), 2.0, 100.0),
    ],
)
def test_interpolation_methods_match_expected_values(
    run_scan_module,
    method,
    x_values,
    y_values,
    x_query,
    expected,
) -> None:
    constraint = make_constraint(method)
    interpolation_tables = {
        constraint["id"]: {
            "x": x_values,
            "y": y_values,
        }
    }

    limit, skip_reason = run_scan_module.interpolate_limit(
        constraint,
        {"x": x_query},
        interpolation_tables,
    )

    assert skip_reason is None
    assert limit == pytest.approx(expected)


def test_interpolation_valid_range_boundary_is_allowed(run_scan_module) -> None:
    constraint = make_constraint("linear")
    interpolation_tables = {
        constraint["id"]: {
            "x": np.array([1.0, 100.0]),
            "y": np.array([10.0, 20.0]),
        }
    }

    limit, skip_reason = run_scan_module.interpolate_limit(
        constraint,
        {"x": 1.0},
        interpolation_tables,
    )

    assert skip_reason is None
    assert limit == pytest.approx(10.0)


def test_interpolation_forbidden_out_of_range_skips(run_scan_module) -> None:
    constraint = make_constraint("linear", policy="forbidden")
    interpolation_tables = {
        constraint["id"]: {
            "x": np.array([1.0, 100.0]),
            "y": np.array([10.0, 20.0]),
        }
    }

    limit, skip_reason = run_scan_module.interpolate_limit(
        constraint,
        {"x": 120.0},
        interpolation_tables,
    )

    assert limit is None
    assert skip_reason == "out of interpolation range"


def test_interpolation_nearest_out_of_range_clamps(run_scan_module) -> None:
    constraint = make_constraint("linear", policy="nearest")
    interpolation_tables = {
        constraint["id"]: {
            "x": np.array([1.0, 100.0]),
            "y": np.array([10.0, 20.0]),
        }
    }

    limit, skip_reason = run_scan_module.interpolate_limit(
        constraint,
        {"x": 120.0},
        interpolation_tables,
    )

    assert skip_reason is None
    assert limit == pytest.approx(20.0)
