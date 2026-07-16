from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math

from jsonschema import Draft202012Validator
import pytest

from scripts._reproduction_result_validation import (
    expected_evidence_axes,
    reproduction_result_semantic_errors,
)
from tests.unit.compare_reference_fixtures import (
    load_result,
    make_compare_project,
    run_compare,
)


def _example(repo_root):
    return json.loads(
        (repo_root / "schemas" / "examples" / "reproduction-result.example.json").read_text(
            encoding="utf-8"
        )
    )


def _materialize_declared_figure_evidence(project_dir, payload) -> None:
    contents = {
        "pdf": b"%PDF-1.4\n% pytest evidence\n%%EOF\n",
        "png": b"\x89PNG\r\n\x1a\npytest evidence\n",
    }
    for result in payload["results"]:
        for pair in result["generated_files"].values():
            for extension, content in contents.items():
                path = project_dir / pair[extension]
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
                pair[f"{extension}_sha256"] = (
                    f"sha256:{hashlib.sha256(content).hexdigest()}"
                )


def test_expected_evidence_axes_cover_formula_acquisition_and_boundary_modes() -> None:
    assert expected_evidence_axes({"kind": "formula"}) == (
        "unverified",
        "requires_human_review",
    )
    assert expected_evidence_axes(
        {
            "kind": "figure_curve",
            "normalization": {
                "acquisition": {"source_type": "synthetic_fixture"}
            },
        }
    ) == ("synthetic", "machine_verifiable")
    assert expected_evidence_axes(
        {
            "kind": "exclusion_region",
            "normalization": {"acquisition": {"source_type": "paper_figure"}},
            "boundary": {"mode": "precomputed_boundary"},
        }
    ) == ("independent_snapshot", "requires_human_review")


def test_schema_rejects_pass_above_provenance_ceiling(repo_root) -> None:
    payload = _example(repo_root)
    result = payload["results"][0]
    result["derivation_independence"] = "tainted"
    result["verdict"] = "pass"
    result["verdict_ceiling"] = "pass"
    schema = json.loads(
        (repo_root / "schemas" / "reproduction-result.schema.json").read_text(
            encoding="utf-8"
        )
    )

    errors = list(Draft202012Validator(schema).iter_errors(payload))

    assert errors
    assert any("independent" in error.message or "needs_human_review" in error.message for error in errors)


def test_parametric_result_schema_requires_fixed_geometry_method_and_tolerance(
    repo_root,
) -> None:
    payload = _example(repo_root)
    result = payload["results"][0]
    result["comparison"]["kind"] = "parametric_curve"
    result["comparison"].pop("interpolation_method")
    result["comparison"]["geometry_method"] = (
        "normalized_continuous_polyline_hausdorff"
    )
    result["tolerance"] = {"kind": "normalized_distance", "value": 0.15}
    schema = json.loads(
        (repo_root / "schemas" / "reproduction-result.schema.json").read_text(
            encoding="utf-8"
        )
    )
    validator = Draft202012Validator(schema)
    assert not list(validator.iter_errors(payload))

    missing_method = deepcopy(payload)
    missing_method["results"][0]["comparison"].pop("geometry_method")
    assert list(validator.iter_errors(missing_method))

    wrong_tolerance = deepcopy(payload)
    wrong_tolerance["results"][0]["tolerance"] = {
        "kind": "relative",
        "value": 0.15,
    }
    assert list(validator.iter_errors(wrong_tolerance))

    mixed_methods = deepcopy(payload)
    mixed_methods["results"][0]["comparison"]["interpolation_method"] = (
        "piecewise_linear_union_knots"
    )
    assert list(validator.iter_errors(mixed_methods))


def test_semantics_reject_duplicate_targets_counts_aggregate_and_non_finite(repo_root) -> None:
    payload = _example(repo_root)
    duplicate = deepcopy(payload["results"][0])
    duplicate["comparison"]["metrics"]["max_relative_error"] = float("nan")
    payload["results"].append(duplicate)
    payload["run_summary"]["n_targets_total"] = 99
    payload["run_summary"]["derivation_independence_aggregate"] = "independent"

    errors = reproduction_result_semantic_errors(payload)

    assert any("duplicate target_id" in error for error in errors)
    assert any("non-finite" in error for error in errors)
    assert any("n_targets_total" in error for error in errors)
    assert any("derivation_independence_aggregate" in error for error in errors)


def test_semantics_reject_independent_result_with_unverified_dependencies(repo_root) -> None:
    payload = _example(repo_root)
    payload["depends_on"]["numerics"]["scan_meta_checksum"] = None
    payload["depends_on"]["calculations"]["tasks"] = ["task-002"]

    errors = reproduction_result_semantic_errors(payload)

    assert any("scan-meta checksum" in error for error in errors)
    assert any("absent from depends_on.calculations.tasks" in error for error in errors)


def test_formula_result_may_honestly_declare_no_generated_figures(repo_root) -> None:
    payload = _example(repo_root)
    result = payload["results"][0]
    result["comparison"] = {"kind": "formula", "metrics": {}}
    result["tolerance"] = {"kind": "qualitative", "value": None}
    result["reference_evidence"] = "unverified"
    result["comparison_evidence"] = "requires_human_review"
    result["tasks_used"] = []
    result["derivation_independence"] = "unknown"
    result["provenance_issues"] = [
        {"state": "unknown", "reason": "formula_reference_only"}
    ]
    result["verdict_ceiling"] = "needs_human_review"
    result["verdict"] = "needs_human_review"
    result["generated_files"] = {}
    payload["results"] = [result]
    payload["run_summary"] = {
        "derivation_independence_aggregate": "unknown",
        "n_targets_total": 1,
        "n_targets_pass": 0,
        "n_targets_fail": 0,
        "n_targets_needs_human_review": 1,
        "n_targets_blocked": 0,
    }
    payload["depends_on"]["model"] = {"version": None, "checksum": None}
    payload["depends_on"]["calculations"] = {
        "tasks": [],
        "model_version": None,
    }
    payload["depends_on"]["numerics"]["scan_meta_checksum"] = None
    payload["depends_on"]["numerics"]["scan_csv_checksum"] = None
    schema = json.loads(
        (repo_root / "schemas" / "reproduction-result.schema.json").read_text(
            encoding="utf-8"
        )
    )

    assert not list(Draft202012Validator(schema).iter_errors(payload))
    assert not reproduction_result_semantic_errors(payload)

    forged = deepcopy(payload)
    forged["depends_on"]["model"] = {
        "version": "v1",
        "checksum": "sha256:" + "f" * 64,
    }
    forged["depends_on"]["calculations"] = {
        "tasks": ["task-001"],
        "model_version": "v1",
    }
    forged["depends_on"]["numerics"]["scan_csv_checksum"] = (
        "sha256:" + "e" * 64
    )
    forged_errors = reproduction_result_semantic_errors(forged)
    assert any("model dependency as not applicable" in error for error in forged_errors)
    assert any(
        "calculations dependency as not applicable" in error
        for error in forged_errors
    )
    assert any("must not declare numeric scan evidence" in error for error in forged_errors)


def test_formula_result_schema_rejects_quantitative_tolerance_and_false_axes(
    repo_root,
) -> None:
    payload = _example(repo_root)
    result = payload["results"][0]
    result["comparison"] = {"kind": "formula", "metrics": {}}
    result["tolerance"] = {"kind": "relative", "value": 0.01}
    result["generated_files"] = {}
    schema = json.loads(
        (repo_root / "schemas" / "reproduction-result.schema.json").read_text(
            encoding="utf-8"
        )
    )

    assert list(Draft202012Validator(schema).iter_errors(payload))
    errors = reproduction_result_semantic_errors(payload)
    assert any("formula tolerance" in error for error in errors)
    assert any("formula reference_evidence" in error for error in errors)


def test_formula_result_rejects_table_completeness_and_interpolation(repo_root) -> None:
    payload = _example(repo_root)
    result = payload["results"][0]
    result["comparison"] = {
        "kind": "formula",
        "metrics": {},
        "interpolation_method": "linear",
        "completeness": {
            "complete": True,
            "match_columns": ["x"],
            "reference_rows": 1,
            "matched_reference_rows": 1,
            "missing_reference_rows": 0,
            "row_coverage": 1.0,
            "observables_expected": ["y"],
            "observables_compared": ["y"],
            "expected_values": 1,
            "compared_values": 1,
            "value_coverage": 1.0,
            "blocking_reasons": [],
        },
    }
    result["tolerance"] = {"kind": "qualitative", "value": None}
    result["reference_evidence"] = "unverified"
    result["comparison_evidence"] = "requires_human_review"
    result["verdict_ceiling"] = "needs_human_review"
    result["generated_files"] = {}
    schema = json.loads(
        (repo_root / "schemas" / "reproduction-result.schema.json").read_text(
            encoding="utf-8"
        )
    )

    assert list(Draft202012Validator(schema).iter_errors(payload))
    assert any(
        "quantitative auxiliaries" in error
        for error in reproduction_result_semantic_errors(payload)
    )


def test_non_formula_result_rejects_arbitrary_string_metrics(repo_root) -> None:
    payload = _example(repo_root)
    payload["results"][0]["comparison"]["metrics"] = {"foo": "bar"}
    schema = json.loads(
        (repo_root / "schemas" / "reproduction-result.schema.json").read_text(
            encoding="utf-8"
        )
    )

    errors = reproduction_result_semantic_errors(payload)

    assert list(Draft202012Validator(schema).iter_errors(payload))
    assert any("missing required metrics" in error for error in errors)
    assert any("foo must be a finite numeric value" in error for error in errors)


def test_semantics_require_declared_generated_files_to_exist_under_run_root(
    repo_root, tmp_path
) -> None:
    payload = _example(repo_root)
    project_dir = tmp_path / "project"
    _materialize_declared_figure_evidence(project_dir, payload)
    diagnostic = project_dir / payload["diagnostic_file"]
    diagnostic.parent.mkdir(parents=True, exist_ok=True)
    diagnostic.write_text("diagnostic\n", encoding="utf-8")

    assert not reproduction_result_semantic_errors(
        payload,
        project_dir=project_dir,
        verify_current_scientific_inputs=False,
    )

    missing = project_dir / payload["results"][0]["generated_files"]["overlay"]["pdf"]
    missing.unlink()
    payload["results"][1]["generated_files"]["overlay"]["png"] = "manifest.json"
    errors = reproduction_result_semantic_errors(
        payload,
        project_dir=project_dir,
        verify_current_scientific_inputs=False,
    )

    assert any("declared file does not exist" in error for error in errors)
    assert any("path escapes reproduction/figures/run-001" in error for error in errors)


def test_semantics_recomputes_verdict_from_fixed_tolerance(repo_root) -> None:
    payload = _example(repo_root)
    result = payload["results"][0]
    result["comparison"]["metrics"]["max_relative_error"] = 999.0

    errors = reproduction_result_semantic_errors(payload)

    assert any("fixed metrics/tolerance require 'fail'" in error for error in errors)


def test_semantics_rejects_arithmetically_false_completeness(repo_root) -> None:
    payload = _example(repo_root)
    result = payload["results"][0]
    result["comparison"]["kind"] = "scan_table"
    result["comparison"]["completeness"] = {
        "complete": True,
        "match_columns": ["x"],
        "reference_rows": 100,
        "matched_reference_rows": 1,
        "missing_reference_rows": 0,
        "row_coverage": 1.0,
        "observables_expected": ["observable"],
        "observables_compared": ["observable"],
        "expected_values": 100,
        "compared_values": 1,
        "value_coverage": 1.0,
        "blocking_reasons": [],
    }
    result["comparison"]["metrics"]["missing_rows"] = 0

    errors = reproduction_result_semantic_errors(payload)

    assert any("reference_rows must equal" in error for error in errors)
    assert any("row_coverage is arithmetically inconsistent" in error for error in errors)
    assert any("value_coverage is arithmetically inconsistent" in error for error in errors)


def test_semantics_rejects_impossible_calendar_timestamp(repo_root) -> None:
    payload = _example(repo_root)
    payload["started_at"] = "2026-99-99T99:99:99Z"

    errors = reproduction_result_semantic_errors(payload)

    assert any("invalid calendar timestamp" in error for error in errors)


@pytest.mark.parametrize(
    ("path", "invalid"),
    [
        (("verdict",), []),
        (("verdict",), {}),
        (("comparison", "kind"), []),
        (("comparison", "kind"), {}),
        (("tolerance", "kind"), []),
        (("tolerance", "kind"), {}),
        (("comparison", "metrics", "relative_error_defined"), []),
    ],
)
def test_semantic_validator_is_total_for_schema_invalid_discriminators(
    repo_root, path, invalid
) -> None:
    payload = _example(repo_root)
    node = payload["results"][0]
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = invalid

    errors = reproduction_result_semantic_errors(payload)

    assert isinstance(errors, list)
    assert errors


def test_comparator_output_binds_current_scientific_and_generated_evidence(
    repo_root, tmp_path
) -> None:
    project_dir = make_compare_project(tmp_path)
    completed = run_compare(repo_root, project_dir, "run-001")
    assert completed.returncode == 0, completed.stdout + completed.stderr
    payload = load_result(project_dir, "run-001")
    run_dir = project_dir / "reproduction" / "runs" / "run-001"

    assert not reproduction_result_semantic_errors(
        payload,
        project_dir=project_dir,
        expected_run_dir=run_dir,
    )

    scan_path = project_dir / "numerics" / "scan-results" / "analysis-001" / "scan.csv"
    scan_path.write_text(scan_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    errors = reproduction_result_semantic_errors(
        payload,
        project_dir=project_dir,
        expected_run_dir=run_dir,
    )
    assert any("scan_csv_checksum" in error for error in errors)


@pytest.mark.parametrize(
    ("field", "forged_value", "expected_value"),
    [
        ("reference_evidence", "independent_snapshot", "synthetic"),
        ("comparison_evidence", "requires_human_review", "machine_verifiable"),
    ],
)
def test_semantics_recomputes_persisted_evidence_axes_from_current_target(
    repo_root,
    tmp_path,
    field,
    forged_value,
    expected_value,
) -> None:
    project_dir = make_compare_project(tmp_path)
    completed = run_compare(repo_root, project_dir, "run-001")
    assert completed.returncode == 0, completed.stdout + completed.stderr
    payload = load_result(project_dir, "run-001")
    result = payload["results"][0]
    assert result[field] == expected_value
    result[field] = forged_value

    errors = reproduction_result_semantic_errors(
        payload,
        project_dir=project_dir,
        expected_run_dir=project_dir / "reproduction" / "runs" / "run-001",
    )

    assert any(
        f"{field} does not match current repro target" in error
        and repr(expected_value) in error
        for error in errors
    )


def test_generated_evidence_rejects_tampering_reuse_and_missing_groups(
    repo_root, tmp_path
) -> None:
    project_dir = make_compare_project(tmp_path)
    completed = run_compare(repo_root, project_dir, "run-001")
    assert completed.returncode == 0, completed.stdout + completed.stderr
    payload = load_result(project_dir, "run-001")
    run_dir = project_dir / "reproduction" / "runs" / "run-001"

    tampered = deepcopy(payload)
    png_relpath = tampered["results"][0]["generated_files"]["overlay"]["png"]
    (project_dir / png_relpath).write_bytes(b"not a png")
    errors = reproduction_result_semantic_errors(
        tampered,
        project_dir=project_dir,
        expected_run_dir=run_dir,
    )
    assert any("PNG signature" in error for error in errors)
    assert any("png_sha256" in error for error in errors)

    project_dir = make_compare_project(tmp_path / "reuse")
    completed = run_compare(repo_root, project_dir, "run-001")
    assert completed.returncode == 0, completed.stdout + completed.stderr
    reused = load_result(project_dir, "run-001")
    generated = reused["results"][0]["generated_files"]
    generated["residual"]["pdf"] = generated["overlay"]["pdf"]
    generated["residual"]["pdf_sha256"] = generated["overlay"]["pdf_sha256"]
    errors = reproduction_result_semantic_errors(
        reused,
        project_dir=project_dir,
        expected_run_dir=project_dir / "reproduction" / "runs" / "run-001",
    )
    assert any("reuses evidence file" in error for error in errors)

    missing_group = deepcopy(load_result(project_dir, "run-001"))
    del missing_group["results"][0]["generated_files"]["residual"]
    errors = reproduction_result_semantic_errors(
        missing_group,
        project_dir=project_dir,
        expected_run_dir=project_dir / "reproduction" / "runs" / "run-001",
    )
    assert any("generated_files groups" in error for error in errors)


def test_generated_evidence_groups_are_bound_to_exact_canonical_paths(
    repo_root, tmp_path
) -> None:
    project_dir = make_compare_project(tmp_path)
    completed = run_compare(repo_root, project_dir, "run-001")
    assert completed.returncode == 0, completed.stdout + completed.stderr
    payload = load_result(project_dir, "run-001")
    generated = payload["results"][0]["generated_files"]
    generated["overlay"], generated["residual"] = (
        generated["residual"],
        generated["overlay"],
    )

    errors = reproduction_result_semantic_errors(
        payload,
        project_dir=project_dir,
        expected_run_dir=project_dir / "reproduction" / "runs" / "run-001",
    )

    assert any("must equal" in error for error in errors)


def test_recomputed_metrics_reject_one_ulp_persisted_drift(repo_root, tmp_path) -> None:
    project_dir = make_compare_project(tmp_path)
    completed = run_compare(repo_root, project_dir, "run-001")
    assert completed.returncode == 0, completed.stdout + completed.stderr
    payload = load_result(project_dir, "run-001")
    metric = payload["results"][0]["comparison"]["metrics"]["max_relative_error"]
    payload["results"][0]["comparison"]["metrics"]["max_relative_error"] = math.nextafter(
        metric, math.inf
    )

    errors = reproduction_result_semantic_errors(payload, project_dir=project_dir)

    assert any("metrics do not match current scientific inputs" in error for error in errors)


def test_blocked_label_cannot_hide_fixed_metric_verdict(repo_root) -> None:
    payload = _example(repo_root)
    payload["results"][0]["verdict"] = "blocked"
    payload["run_summary"]["n_targets_needs_human_review"] -= 1
    payload["run_summary"]["n_targets_blocked"] += 1

    errors = reproduction_result_semantic_errors(payload)

    assert any("fixed metrics/tolerance require" in error for error in errors)


def test_forged_blocking_warning_cannot_skip_current_metric_recompute(
    repo_root, tmp_path
) -> None:
    project_dir = make_compare_project(tmp_path)
    completed = run_compare(repo_root, project_dir, "run-001")
    assert completed.returncode == 0, completed.stdout + completed.stderr
    payload = load_result(project_dir, "run-001")
    result = payload["results"][0]
    original_verdict = result["verdict"]
    result["verdict"] = "blocked"
    result["comparison"]["metrics"] = {}
    result["warnings"].append("blocked_by_orchestrator: forged")
    result["generated_files"] = {"overlay": result["generated_files"]["overlay"]}
    payload["run_summary"][f"n_targets_{original_verdict}"] -= 1
    payload["run_summary"]["n_targets_blocked"] += 1

    errors = reproduction_result_semantic_errors(payload, project_dir=project_dir)

    assert any("metrics do not match current scientific inputs" in error for error in errors)
