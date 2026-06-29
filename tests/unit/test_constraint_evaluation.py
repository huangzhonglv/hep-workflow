from __future__ import annotations

import math

import pytest


@pytest.mark.parametrize(
    ("constraint", "prediction", "expected_verdict", "expected_margin", "expected_chi2"),
    [
        (
            {
                "id": "m-in",
                "type": "measurement",
                "implementation_status": "direct",
                "central_value": 10.0,
                "uncertainty": 2.0,
                "sigma": 1.0,
            },
            11.0,
            "allowed",
            -0.5,
            0.25,
        ),
        (
            {
                "id": "m-edge",
                "type": "measurement",
                "implementation_status": "direct",
                "central_value": 10.0,
                "uncertainty": 2.0,
                "sigma": 1.0,
            },
            12.0,
            "allowed",
            -1.0,
            1.0,
        ),
        (
            {
                "id": "m-out",
                "type": "measurement",
                "implementation_status": "direct",
                "central_value": 10.0,
                "uncertainty": 2.0,
                "sigma": 1.0,
            },
            13.0,
            "excluded",
            -1.5,
            2.25,
        ),
        (
            {
                "id": "u-in",
                "type": "upper_limit",
                "implementation_status": "direct",
                "limit_value": 10.0,
            },
            8.0,
            "allowed",
            0.2,
            None,
        ),
        (
            {
                "id": "u-edge",
                "type": "upper_limit",
                "implementation_status": "direct",
                "limit_value": 10.0,
            },
            10.0,
            "allowed",
            0.0,
            None,
        ),
        (
            {
                "id": "u-out",
                "type": "upper_limit",
                "implementation_status": "direct",
                "limit_value": 10.0,
            },
            12.0,
            "excluded",
            -0.2,
            None,
        ),
        (
            {
                "id": "l-in",
                "type": "lower_limit",
                "implementation_status": "direct",
                "limit_value": 10.0,
            },
            12.0,
            "allowed",
            0.2,
            None,
        ),
        (
            {
                "id": "l-edge",
                "type": "lower_limit",
                "implementation_status": "direct",
                "limit_value": 10.0,
            },
            10.0,
            "allowed",
            0.0,
            None,
        ),
        (
            {
                "id": "l-out",
                "type": "lower_limit",
                "implementation_status": "direct",
                "limit_value": 10.0,
            },
            8.0,
            "excluded",
            -0.2,
            None,
        ),
        (
            {
                "id": "b-in",
                "type": "allowed_band",
                "implementation_status": "direct",
                "limit_value_min": 10.0,
                "limit_value_max": 20.0,
            },
            12.0,
            "allowed",
            2.0,
            None,
        ),
        (
            {
                "id": "b-edge",
                "type": "allowed_band",
                "implementation_status": "direct",
                "limit_value_min": 10.0,
                "limit_value_max": 20.0,
            },
            10.0,
            "allowed",
            0.0,
            None,
        ),
        (
            {
                "id": "b-out",
                "type": "allowed_band",
                "implementation_status": "direct",
                "limit_value_min": 10.0,
                "limit_value_max": 20.0,
            },
            25.0,
            "excluded",
            -5.0,
            None,
        ),
    ],
)
def test_evaluate_constraint_matches_decision_table(
    run_scan_module,
    constraint,
    prediction,
    expected_verdict,
    expected_margin,
    expected_chi2,
) -> None:
    result = run_scan_module.evaluate_constraint(constraint, prediction)

    assert result["verdict"] == expected_verdict
    assert result["margin"] == pytest.approx(expected_margin)
    if expected_chi2 is None:
        assert result["chi2"] is None
    else:
        assert result["chi2"] == pytest.approx(expected_chi2)


def test_evaluate_constraint_marks_manual_only_as_skipped(run_scan_module) -> None:
    result = run_scan_module.evaluate_constraint(
        {
            "id": "manual",
            "type": "upper_limit",
            "implementation_status": "manual_only",
            "limit_value": 1.0,
        },
        0.5,
    )

    assert result == {
        "verdict": "skipped",
        "margin": None,
        "chi2": None,
        "skip_reason": "manual_only constraint",
    }
