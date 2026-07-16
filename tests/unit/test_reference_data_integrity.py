from __future__ import annotations

import json

from jsonschema import Draft202012Validator
import pytest

from tests.unit.compare_reference_fixtures import (
    default_target,
    make_compare_project,
    run_compare,
    write_json,
)


def _targets(project_dir):
    path = project_dir / "literature" / "repro-targets.json"
    return path, json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "invalid_timestamp",
    [
        "not-a-timestamp",
        "2026-07-13T00:00:00+00:00",
        "2026-07-13T00:00:00.000Z",
    ],
)
def test_reference_schemas_require_canonical_utc_second_timestamps(
    repo_root,
    invalid_timestamp,
) -> None:
    cases = []

    formula = json.loads(
        (repo_root / "schemas" / "examples" / "formula-reference.example.json")
        .read_text(encoding="utf-8")
    )
    formula["acquired_at"] = invalid_timestamp
    cases.append(("formula-reference.schema.json", formula))

    record = json.loads(
        (repo_root / "schemas" / "examples" / "normalization-record.example.json")
        .read_text(encoding="utf-8")
    )
    record["acquisition"]["acquired_at"] = invalid_timestamp
    cases.append(("normalization-record.schema.json", record))

    targets = json.loads(
        (repo_root / "schemas" / "examples" / "repro-targets.example.json")
        .read_text(encoding="utf-8")
    )
    quantitative_target = next(
        target for target in targets["targets"] if "normalization" in target
    )
    quantitative_target["normalization"]["acquisition"][
        "acquired_at"
    ] = invalid_timestamp
    cases.append(("repro-targets.schema.json", targets))

    for schema_name, payload in cases:
        schema = json.loads(
            (repo_root / "schemas" / schema_name).read_text(encoding="utf-8")
        )
        errors = list(Draft202012Validator(schema).iter_errors(payload))
        assert any(error.validator == "pattern" for error in errors), schema_name


@pytest.mark.parametrize("kind", ["formula", "figure_curve"])
def test_compare_rejects_calendar_invalid_canonical_timestamp(
    repo_root,
    tmp_path,
    kind,
) -> None:
    target = default_target(kind=kind)
    project_dir = make_compare_project(tmp_path, targets=[target])
    if kind == "formula":
        formula_path = project_dir / target["data_file"]
        payload = json.loads(formula_path.read_text(encoding="utf-8"))
        payload["acquired_at"] = "2026-02-30T00:00:00Z"
        write_json(formula_path, payload)
    else:
        targets_path, payload = _targets(project_dir)
        payload["targets"][0]["normalization"]["acquisition"][
            "acquired_at"
        ] = "2026-02-30T00:00:00Z"
        write_json(targets_path, payload)

    result = run_compare(repo_root, project_dir, "run-001")

    assert result.returncode == 1
    assert "valid UTC timestamp" in result.stderr


@pytest.mark.parametrize(
    "unsafe_path",
    [
        "/tmp/external-reference.csv",
        "numerics/scan-results/analysis-001/scan.csv",
        "literature/digitized/../../numerics/scan-results/analysis-001/scan.csv",
    ],
)
def test_compare_rejects_reference_paths_outside_digitized_root(
    repo_root, tmp_path, unsafe_path
) -> None:
    project_dir = make_compare_project(tmp_path)
    targets_path, payload = _targets(project_dir)
    payload["targets"][0]["data_file"] = unsafe_path
    write_json(targets_path, payload)

    result = run_compare(repo_root, project_dir, "run-001")

    assert result.returncode == 1
    assert "literature/digitized" in result.stderr
    assert not (project_dir / "reproduction" / "runs" / "run-001").exists()


def test_compare_rejects_symlink_escape_from_digitized_root(repo_root, tmp_path) -> None:
    project_dir = make_compare_project(tmp_path)
    targets_path, payload = _targets(project_dir)
    target = payload["targets"][0]
    canonical_path = project_dir / target["data_file"]
    canonical_path.unlink()
    canonical_path.symlink_to(
        project_dir / "numerics" / "scan-results" / "analysis-001" / "scan.csv"
    )
    write_json(targets_path, payload)

    result = run_compare(repo_root, project_dir, "run-001")

    assert result.returncode == 1
    assert "escapes literature/digitized" in result.stderr


def test_compare_rejects_hash_identical_generated_scan_as_reference(
    repo_root, tmp_path
) -> None:
    project_dir = make_compare_project(tmp_path)
    _, payload = _targets(project_dir)
    target = payload["targets"][0]
    canonical_path = project_dir / target["data_file"]
    source_path = project_dir / target["normalization"]["source_data_file"]
    scan_path = project_dir / "numerics" / "scan-results" / "analysis-001" / "scan.csv"
    canonical_path.write_bytes(scan_path.read_bytes())
    source_path.write_bytes(scan_path.read_bytes())

    record_path = project_dir / target["normalization"]["record_file"]
    record = json.loads(record_path.read_text(encoding="utf-8"))
    import hashlib

    record["canonical_checksum"] = "sha256:" + hashlib.sha256(
        canonical_path.read_bytes()
    ).hexdigest()
    record["source_checksum"] = "sha256:" + hashlib.sha256(
        source_path.read_bytes()
    ).hexdigest()
    write_json(record_path, record)

    result = run_compare(repo_root, project_dir, "run-001")

    assert result.returncode == 1
    assert "same SHA-256 content" in result.stderr


def test_compare_rejects_stale_normalization_record(repo_root, tmp_path) -> None:
    project_dir = make_compare_project(tmp_path)
    _, payload = _targets(project_dir)
    target = payload["targets"][0]
    source_path = project_dir / target["normalization"]["source_data_file"]
    source_path.write_text("M_Zp,delta_a_mu\n1,999\n", encoding="utf-8")

    result = run_compare(repo_root, project_dir, "run-001")

    assert result.returncode == 1
    assert "normalization record does not bind current data" in result.stderr


def test_compare_rejects_formula_reference_copied_from_generated_json(
    repo_root, tmp_path
) -> None:
    target = default_target(kind="formula")
    project_dir = make_compare_project(tmp_path, targets=[target])
    formula_path = project_dir / target["data_file"]
    generated = project_dir / "reproduction" / "prior-generated-formula.json"
    generated.parent.mkdir(parents=True, exist_ok=True)
    generated.write_bytes(formula_path.read_bytes())

    result = run_compare(repo_root, project_dir, "run-001")

    assert result.returncode == 1
    assert "same SHA-256 content" in result.stderr
    assert not (project_dir / "reproduction" / "runs" / "run-001").exists()


@pytest.mark.parametrize("field", ["expression", "source_locator"])
def test_compare_rejects_whitespace_only_formula_evidence(
    repo_root, tmp_path, field
) -> None:
    target = default_target(kind="formula")
    project_dir = make_compare_project(tmp_path, targets=[target])
    formula_path = project_dir / target["data_file"]
    payload = json.loads(formula_path.read_text(encoding="utf-8"))
    payload[field] = " \t "
    write_json(formula_path, payload)

    result = run_compare(repo_root, project_dir, "run-001")

    assert result.returncode == 1
    assert field in result.stderr
    assert not (project_dir / "reproduction" / "runs" / "run-001").exists()


def test_compare_rejects_formula_locator_that_claims_generated_project_data(
    repo_root, tmp_path
) -> None:
    target = default_target(kind="formula")
    project_dir = make_compare_project(tmp_path, targets=[target])
    formula_path = project_dir / target["data_file"]
    payload = json.loads(formula_path.read_text(encoding="utf-8"))
    payload["source_locator"] = "numerics/scan-results/analysis-001/scan.csv"
    write_json(formula_path, payload)

    result = run_compare(repo_root, project_dir, "run-001")

    assert result.returncode == 1
    assert "generated project data" in result.stderr
    assert not (project_dir / "reproduction" / "runs" / "run-001").exists()
