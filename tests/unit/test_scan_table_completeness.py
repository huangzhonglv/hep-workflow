from __future__ import annotations

import math

import pandas as pd
import pytest

from scripts import _compare_metrics as metric_helpers
from scripts import compare_to_reference
from tests.unit.compare_reference_fixtures import enrich_target


def scan_table_target(**overrides):
    target = {
        "id": "table-1",
        "kind": "scan_table",
        "x_param": "M",
        "y_param": "g",
        "match_columns": ["M", "g"],
        "observables": ["sigma"],
        "fixed": {},
        "constraints_in_paper": [],
        "data_file": "literature/digitized/table-1.csv",
        "tolerance": {"kind": "relative", "value": 0.05},
    }
    target.update(overrides)
    return enrich_target(target)


def evaluate(scan_df: pd.DataFrame, reference_df: pd.DataFrame, target: dict):
    metrics, _, completeness, warnings, blocked = compare_to_reference.compute_metrics(
        scan_df,
        reference_df,
        target,
    )
    verdict = compare_to_reference.compute_verdict(
        blocked=blocked,
        target_kind="scan_table",
        tolerance=target["tolerance"],
        metrics=metrics,
        completeness=completeness,
        ceiling="pass",
    )
    return metrics, completeness, warnings, blocked, verdict


@pytest.mark.parametrize(
    ("scan_df", "reference_df", "target", "reason"),
    [
        (
            pd.DataFrame({"M": [100], "g": [0.1], "other": [999.0]}),
            pd.DataFrame({"M": [100], "g": [0.1], "sigma": [1.23]}),
            scan_table_target(),
            "missing_observable_columns",
        ),
        (
            pd.DataFrame({"M": [100], "g": [0.1], "sigma": [float("nan")]}),
            pd.DataFrame({"M": [100], "g": [0.1], "sigma": [1.23]}),
            scan_table_target(),
            "non_finite_observable_values",
        ),
        (
            pd.DataFrame({"M": [100], "g": [0.1], "sigma": [float("inf")]}),
            pd.DataFrame({"M": [100], "g": [0.1], "sigma": [1.23]}),
            scan_table_target(),
            "non_finite_observable_values",
        ),
        (
            pd.DataFrame({"M": [100], "g": [0.1], "sigma": ["not-a-number"]}),
            pd.DataFrame({"M": [100], "g": [0.1], "sigma": [1.23]}),
            scan_table_target(),
            "non_finite_observable_values",
        ),
        (
            pd.DataFrame({"M": [100], "g": [0.1], "sigma": [1.23]}),
            pd.DataFrame(
                {
                    "M": [100, 200],
                    "g": [0.1, 0.2],
                    "sigma": [1.23, 9.99],
                }
            ),
            scan_table_target(),
            "missing_reference_rows",
        ),
        (
            pd.DataFrame({"M": [100], "sigma": [1.23]}),
            pd.DataFrame({"M": [100], "g": [0.1], "sigma": [1.23]}),
            scan_table_target(),
            "missing_match_columns",
        ),
        (
            pd.DataFrame(
                {
                    "M": [100, 100],
                    "g": [0.1, 0.1],
                    "sigma": [1.23, 1.23],
                }
            ),
            pd.DataFrame({"M": [100], "g": [0.1], "sigma": [1.23]}),
            scan_table_target(),
            "duplicate_match_keys_in_scan",
        ),
        (
            pd.DataFrame({"M": [100], "g": [0.1], "sigma": [1.23]}),
            pd.DataFrame({"M": [100], "g": [0.1], "sigma": [1.23]}),
            scan_table_target(fixed={"channel": "A"}),
            "missing_fixed_columns_in_scan",
        ),
        (
            pd.DataFrame({"M": [100], "g": [float("inf")], "sigma": [1.23]}),
            pd.DataFrame({"M": [100], "g": [0.1], "sigma": [1.23]}),
            scan_table_target(),
            "invalid_match_key_values",
        ),
    ],
)
def test_incomplete_scan_table_data_is_blocked(
    scan_df,
    reference_df,
    target,
    reason,
) -> None:
    metrics, completeness, warnings, blocked, verdict = evaluate(
        scan_df,
        reference_df,
        target,
    )

    assert metrics == {}
    assert completeness["complete"] is False
    assert any(reason in item for item in completeness["blocking_reasons"])
    assert any(reason in item for item in warnings)
    assert blocked is True
    assert verdict == "blocked"


def test_complete_scan_table_can_pass_or_fail_by_tolerance() -> None:
    reference = pd.DataFrame(
        {"M": [100, 200], "g": [0.1, 0.2], "sigma": [1.0, 2.0]}
    )
    exact_scan = reference.copy()

    metrics, completeness, warnings, blocked, verdict = evaluate(
        exact_scan,
        reference,
        scan_table_target(),
    )

    assert blocked is False
    assert warnings == []
    assert verdict == "pass"
    assert metrics["n_points_compared"] == 2
    assert metrics["max_relative_error"] == 0.0
    assert completeness == {
        "complete": True,
        "match_columns": ["M", "g"],
        "reference_rows": 2,
        "matched_reference_rows": 2,
        "missing_reference_rows": 0,
        "row_coverage": 1.0,
        "observables_expected": ["sigma"],
        "observables_compared": ["sigma"],
        "expected_values": 2,
        "compared_values": 2,
        "value_coverage": 1.0,
        "blocking_reasons": [],
    }

    failing_scan = exact_scan.copy()
    failing_scan["sigma"] = [1.2, 2.4]
    _, completeness, _, blocked, verdict = evaluate(
        failing_scan,
        reference,
        scan_table_target(),
    )
    assert completeness["complete"] is True
    assert blocked is False
    assert verdict == "fail"


def test_extra_scan_rows_do_not_reduce_reference_coverage() -> None:
    reference = pd.DataFrame({"M": [100], "g": [0.1], "sigma": [1.0]})
    scan = pd.DataFrame(
        {
            "M": [100, 200],
            "g": [0.1, 0.2],
            "sigma": [1.0, 9.0],
        }
    )

    metrics, completeness, warnings, blocked, verdict = evaluate(
        scan,
        reference,
        scan_table_target(),
    )

    assert metrics["n_points_compared"] == 1
    assert completeness["complete"] is True
    assert completeness["reference_rows"] == 1
    assert warnings == []
    assert blocked is False
    assert verdict == "pass"


def test_match_columns_must_be_explicit_unique_and_disjoint() -> None:
    frame = pd.DataFrame({"M": [100], "g": [0.1], "sigma": [1.23]})

    for match_columns, reason in (
        ([], "missing_match_columns"),
        (["M", "M"], "duplicate_declared_match_columns"),
        (["M", "sigma"], "match_columns_overlap_observables"),
        (["M"], "match_columns_missing_axes"),
    ):
        result = metric_helpers.scan_table_metrics(
            frame,
            frame,
            scan_table_target(match_columns=match_columns),
        )
        assert result.completeness["complete"] is False
        assert any(
            reason in item for item in result.completeness["blocking_reasons"]
        )


def test_verdict_defensively_blocks_incomplete_or_non_finite_metrics() -> None:
    complete = {
        "complete": True,
        "row_coverage": 1.0,
        "value_coverage": 1.0,
    }
    incomplete = {**complete, "complete": False}

    assert (
        compare_to_reference.compute_verdict(
            blocked=False,
            target_kind="scan_table",
            tolerance={"kind": "relative", "value": 0.05},
            metrics={"max_relative_error": 0.0, "n_points_compared": 0},
            completeness=incomplete,
            ceiling="pass",
        )
        == "blocked"
    )
    assert (
        compare_to_reference.compute_verdict(
            blocked=False,
            target_kind="scan_table",
            tolerance={"kind": "relative", "value": math.nan},
            metrics={"max_relative_error": 0.0, "n_points_compared": 1},
            completeness=complete,
            ceiling="pass",
        )
        == "blocked"
    )
    assert (
        compare_to_reference.compute_verdict(
            blocked=False,
            target_kind="scan_table",
            tolerance={"kind": "relative", "value": 0.05},
            metrics={"max_relative_error": math.nan, "n_points_compared": 1},
            completeness=complete,
            ceiling="pass",
        )
        == "blocked"
    )


def test_column_helpers_do_not_fall_back_or_ignore_missing_fixed_columns() -> None:
    frame = pd.DataFrame({"M": [100], "other": [1.23]})
    target = scan_table_target(y_param="sigma", observables=["sigma"])

    with pytest.raises(ValueError, match="declared y column"):
        metric_helpers.choose_y_column(frame, x_column="M", target=target)
    with pytest.raises(ValueError, match="fixed parameter column channel"):
        metric_helpers.filter_fixed_rows(frame, {"channel": "A"})


def test_json_writer_rejects_non_finite_values(tmp_path) -> None:
    with pytest.raises(ValueError, match="Out of range float values"):
        compare_to_reference.write_json(
            tmp_path / "result.json",
            {"metric": math.nan},
        )
