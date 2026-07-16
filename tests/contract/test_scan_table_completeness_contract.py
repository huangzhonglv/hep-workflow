from __future__ import annotations

from copy import deepcopy
import json

from jsonschema import Draft202012Validator


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def validation_errors(schema, payload):
    return list(Draft202012Validator(schema).iter_errors(payload))


def test_scan_table_target_requires_explicit_match_columns(repo_root) -> None:
    schema = load_json(repo_root / "schemas" / "repro-targets.schema.json")
    Draft202012Validator.check_schema(schema)
    example = load_json(
        repo_root / "schemas" / "examples" / "repro-targets.example.json"
    )
    scan_table = next(
        target for target in example["targets"] if target["kind"] == "scan_table"
    )
    assert not validation_errors(schema, example)

    without_match_columns = deepcopy(example)
    target = next(
        item
        for item in without_match_columns["targets"]
        if item["id"] == scan_table["id"]
    )
    target.pop("match_columns")

    errors = validation_errors(schema, without_match_columns)
    assert errors
    assert any("match_columns" in error.message for error in errors)


def test_scan_table_pass_requires_complete_nonempty_comparison(repo_root) -> None:
    schema = load_json(repo_root / "schemas" / "reproduction-result.schema.json")
    Draft202012Validator.check_schema(schema)
    example = load_json(
        repo_root / "schemas" / "examples" / "reproduction-result.example.json"
    )
    payload = deepcopy(example)
    result = payload["results"][0]
    result["comparison"]["kind"] = "scan_table"
    result["comparison"].pop("interpolation_method")
    result["comparison"]["metrics"] = {
        "max_relative_error": 0.0,
        "rms_relative_error": 0.0,
        "max_absolute_error": 0.0,
        "n_points_compared": 0,
        "missing_rows": 0,
    }

    errors = validation_errors(schema, payload)
    assert errors
    assert any("completeness" in error.message for error in errors)

    result["comparison"]["completeness"] = {
        "complete": False,
        "match_columns": ["M_Zp", "g_prime"],
        "reference_rows": 1,
        "matched_reference_rows": 0,
        "missing_reference_rows": 1,
        "row_coverage": 0.0,
        "observables_expected": ["delta_a_mu"],
        "observables_compared": [],
        "expected_values": 1,
        "compared_values": 0,
        "value_coverage": 0.0,
        "blocking_reasons": ["missing_reference_rows:1"],
    }
    errors = validation_errors(schema, payload)
    assert errors
    assert any(
        error.absolute_path and error.absolute_path[-1] == "complete"
        for error in errors
    )

    result["comparison"]["completeness"] = {
        "complete": True,
        "match_columns": ["M_Zp", "g_prime"],
        "reference_rows": 1,
        "matched_reference_rows": 1,
        "missing_reference_rows": 0,
        "row_coverage": 1.0,
        "observables_expected": ["delta_a_mu"],
        "observables_compared": ["delta_a_mu"],
        "expected_values": 1,
        "compared_values": 1,
        "value_coverage": 1.0,
        "blocking_reasons": [],
    }
    result["comparison"]["metrics"]["n_points_compared"] = 1
    assert not validation_errors(schema, payload)


def test_scan_table_blocked_result_may_persist_completeness_diagnostics(
    repo_root,
) -> None:
    schema = load_json(repo_root / "schemas" / "reproduction-result.schema.json")
    Draft202012Validator.check_schema(schema)
    example = load_json(
        repo_root / "schemas" / "examples" / "reproduction-result.example.json"
    )
    payload = deepcopy(example)
    result = payload["results"][0]
    result["comparison"] = {
        "kind": "scan_table",
        "completeness": {
            "complete": False,
            "match_columns": ["M_Zp", "g_prime"],
            "reference_rows": 3,
            "matched_reference_rows": 3,
            "missing_reference_rows": 0,
            "row_coverage": 1.0,
            "observables_expected": ["delta_a_mu"],
            "observables_compared": [],
            "expected_values": 3,
            "compared_values": 0,
            "value_coverage": 0.0,
            "blocking_reasons": ["missing_observable_columns:scan.csv=delta_a_mu"],
        },
        "metrics": {},
    }
    result["verdict"] = "blocked"
    result["warnings"] = [
        "metric_computation_blocked: missing_observable_columns:scan.csv=delta_a_mu"
    ]

    assert not validation_errors(schema, payload)
