from __future__ import annotations

import math

import numpy as np
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


@pytest.mark.parametrize("prediction", [float("nan"), float("inf"), float("-inf"), True])
def test_evaluate_constraint_rejects_non_finite_or_boolean_predictions(
    run_scan_module,
    prediction,
) -> None:
    with pytest.raises(ValueError, match="prediction must"):
        run_scan_module.evaluate_constraint(
            {
                "id": "finite-only",
                "type": "upper_limit",
                "implementation_status": "direct",
                "limit_value": 1.0,
            },
            prediction,
        )


def test_point_status_requires_all_constraints_to_be_allowed(run_scan_module) -> None:
    row = {
        "c-001_verdict": "allowed",
        "c-002_verdict": "skipped",
    }

    assert run_scan_module.point_status_from_row(row, ["c-001", "c-002"]) == "skipped"
    assert run_scan_module.point_status_from_row(row, []) == "skipped"


def test_evaluate_point_marks_mixed_allowed_and_skipped_evidence_incomplete(
    run_scan_module,
) -> None:
    inputs = {
        "scan_config": {
            "scan_parameters": [{"canonical_name": "x"}],
            "fixed_parameters": [],
            "observables": [],
            "constraints_used": ["c-001", "c-002"],
        },
        "constraints_by_id": {
            "c-001": {
                "id": "c-001",
                "type": "upper_limit",
                "observable": "x",
                "implementation_status": "direct",
                "limit_value": 1.0,
            },
            "c-002": {
                "id": "c-002",
                "type": "upper_limit",
                "observable": "x",
                "implementation_status": "manual_only",
                "limit_value": 1.0,
            },
        },
    }
    runtime = {"interpolation_tables": {}}

    result = run_scan_module.evaluate_point({"x": 0.5}, inputs, runtime)

    assert result["point_status"] == "skipped"
    assert result["point_failed"] is True
    assert result["row"]["c-001_verdict"] == "allowed"
    assert result["row"]["c-002_verdict"] == "skipped"


@pytest.mark.parametrize("prediction", [True, np.bool_(True), np.array(0.5)])
def test_task_constraint_prediction_is_validated_before_float_coercion(
    run_scan_module,
    prediction,
) -> None:
    def backend(**kwargs):
        return prediction

    constraint = {
        "id": "c-001",
        "observable": "derived_value",
        "computed_by": {"type": "task", "task_id": "task-001"},
    }
    runtime = {
        "task_backends": {"task-001": backend},
        "task_parameter_names": {"task-001": {"x"}},
    }

    with pytest.raises(ValueError, match="finite numeric scalar"):
        run_scan_module.resolve_constraint_prediction(
            constraint,
            {"x": 1.0},
            {},
            runtime,
        )


@pytest.mark.parametrize("prediction", [True, np.bool_(True), np.array(0.5)])
def test_parameter_combination_fallback_is_validated_before_float_coercion(
    run_scan_module,
    prediction,
) -> None:
    def fallback(**kwargs):
        return prediction

    constraint = {
        "id": "c-001",
        "observable": "derived_value",
        "computed_by": {"type": "parameter_combination"},
    }
    runtime = {
        "formula_evaluators": {},
        "parameter_combination_backends": {"c-001": fallback},
        "task_backends": {},
    }

    with pytest.raises(ValueError, match="finite numeric scalar"):
        run_scan_module.resolve_constraint_prediction(
            constraint,
            {"x": 1.0},
            {},
            runtime,
        )


def test_safe_parameter_combination_rejects_boolean_literal(run_scan_module) -> None:
    with pytest.raises(ValueError, match="unsupported constant True"):
        run_scan_module.compile_parameter_combination("True")
