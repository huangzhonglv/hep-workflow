from __future__ import annotations

from decimal import Decimal
import json
import os

import pandas as pd
import pytest

from scripts._compare_metrics import (
    exact_decimal_linear_conversion_matches,
    scan_table_metrics,
    validate_fixed_parameter_normalization,
)
from scripts.compare_to_reference import _same_json_scalar, validate_target_normalization
from tests.unit.compare_reference_fixtures import (
    default_target,
    hash_file,
    make_compare_project,
    normalization_for_target,
    rebind_scan_graph,
    run_compare,
    write_json,
)


def _target(project_dir):
    targets_path = project_dir / "literature" / "repro-targets.json"
    payload = json.loads(targets_path.read_text(encoding="utf-8"))
    return targets_path, payload, payload["targets"][0]


def test_exact_json_scalar_comparison_preserves_large_integer_identity() -> None:
    assert not _same_json_scalar(9007199254740992, 9007199254740993)
    assert not _same_json_scalar(True, 1)
    assert _same_json_scalar(1, 1.0)


def _rewrite_record(project_dir, target) -> None:
    normalization = target["normalization"]
    source_path = project_dir / normalization["source_data_file"]
    canonical_path = project_dir / target["data_file"]
    write_json(
        project_dir / normalization["record_file"],
        {
            "status": normalization["status"],
            "method": normalization["method"],
            "source_data_file": normalization["source_data_file"],
            "canonical_data_file": target["data_file"],
            "source_units": normalization["source_units"],
            "canonical_units": normalization["canonical_units"],
            "conversions": normalization["conversions"],
            "fixed_parameters": normalization["fixed_parameters"],
            "acquisition": normalization["acquisition"],
            "source_checksum": hash_file(source_path),
            "canonical_checksum": hash_file(canonical_path),
        },
    )


def _configure_mass_conversion(
    project_dir,
    *,
    source_unit="GeV",
    canonical_unit="MeV",
    factor=1000.0,
    offset=0.0,
    convert_table=True,
):
    targets_path, payload, target = _target(project_dir)
    normalization = target["normalization"]
    normalization["method"] = "converted"
    normalization["source_units"]["M_Zp"] = source_unit
    normalization["canonical_units"]["M_Zp"] = canonical_unit
    normalization["conversions"]["M_Zp"] = {
        "operation": "linear",
        "factor": factor,
        "offset": offset,
    }
    source_path = project_dir / normalization["source_data_file"]
    canonical_path = project_dir / target["data_file"]
    source = pd.read_csv(source_path)
    canonical = source.copy()
    if convert_table:
        canonical["M_Zp"] = canonical["M_Zp"] * factor + offset
    canonical.to_csv(canonical_path, index=False)
    write_json(targets_path, payload)
    _rewrite_record(project_dir, target)
    return target


def test_valid_import_time_gev_to_mev_conversion_is_verified(tmp_path) -> None:
    project_dir = make_compare_project(tmp_path)
    target = _configure_mass_conversion(project_dir)

    validate_target_normalization(
        project_dir,
        target,
        paper_id="arxiv:2601.01234v2",
    )


@pytest.mark.parametrize("method", ["identity", "converted"])
def test_normalization_mappings_must_cover_the_same_compared_columns(
    tmp_path, method
) -> None:
    project_dir = make_compare_project(tmp_path)
    target = (
        _target(project_dir)[2]
        if method == "identity"
        else _configure_mass_conversion(project_dir)
    )
    target["normalization"]["conversions"].pop("delta_a_mu")
    targets_path = project_dir / "literature" / "repro-targets.json"
    payload = json.loads(targets_path.read_text(encoding="utf-8"))
    payload["targets"][0] = target
    write_json(targets_path, payload)
    _rewrite_record(project_dir, target)

    with pytest.raises(ValueError, match="must cover exactly the same columns"):
        validate_target_normalization(
            project_dir,
            target,
            paper_id="arxiv:2601.01234v2",
        )


def test_valid_import_conversion_to_scan_canonical_unit_compares_end_to_end(
    repo_root, tmp_path
) -> None:
    project_dir = make_compare_project(tmp_path)
    targets_path, payload, target = _target(project_dir)
    normalization = target["normalization"]
    normalization["method"] = "converted"
    normalization["source_units"]["M_Zp"] = "MeV"
    normalization["canonical_units"]["M_Zp"] = "GeV"
    normalization["conversions"]["M_Zp"] = {
        "operation": "linear",
        "factor": 0.001,
        "offset": 0.0,
    }
    source_path = project_dir / normalization["source_data_file"]
    source = pd.read_csv(source_path)
    source["M_Zp"] = source["M_Zp"] * 1000.0
    source.to_csv(source_path, index=False)
    write_json(targets_path, payload)
    _rewrite_record(project_dir, target)

    completed = run_compare(repo_root, project_dir, "run-001")

    assert completed.returncode == 0, completed.stdout + completed.stderr


@pytest.mark.parametrize(
    ("source_unit", "canonical_unit", "factor", "offset"),
    [
        ("GeV", "MeV", 999.0, 0.0),
        ("GeV", "MeV", 1000.0000000000005, 0.0),
        ("GeV", "s", 1.0, 0.0),
        ("mystery", "MeV", 1.0, 0.0),
        ("GeV", "MeV", 1000.0, 1.0),
    ],
)
def test_unknown_incompatible_or_inexact_conversion_is_rejected(
    tmp_path, source_unit, canonical_unit, factor, offset
) -> None:
    project_dir = make_compare_project(tmp_path)
    target = _configure_mass_conversion(
        project_dir,
        source_unit=source_unit,
        canonical_unit=canonical_unit,
        factor=factor,
        offset=offset,
    )

    with pytest.raises(ValueError, match="allowlisted dimension-preserving"):
        validate_target_normalization(
            project_dir,
            target,
            paper_id="arxiv:2601.01234v2",
        )


def test_converted_status_requires_an_actual_unit_change(tmp_path) -> None:
    project_dir = make_compare_project(tmp_path)
    target = _configure_mass_conversion(
        project_dir,
        source_unit="GeV",
        canonical_unit="GeV",
        factor=1.0,
        convert_table=False,
    )

    with pytest.raises(ValueError, match="has no unit change"):
        validate_target_normalization(
            project_dir,
            target,
            paper_id="arxiv:2601.01234v2",
        )


def test_canonical_table_rejects_even_sub_tolerance_conversion_drift(tmp_path) -> None:
    project_dir = make_compare_project(tmp_path)
    target = _configure_mass_conversion(project_dir)
    canonical_path = project_dir / target["data_file"]
    canonical = pd.read_csv(canonical_path)
    canonical.loc[0, "M_Zp"] = canonical.loc[0, "M_Zp"] + 5.0e-10
    canonical.to_csv(canonical_path, index=False)
    _rewrite_record(project_dir, target)

    with pytest.raises(ValueError, match="does not reproduce canonical data"):
        validate_target_normalization(
            project_dir,
            target,
            paper_id="arxiv:2601.01234v2",
        )


def test_decimal_exact_conversion_accepts_adjacent_binary64_result(tmp_path) -> None:
    project_dir = make_compare_project(tmp_path)
    target = _configure_mass_conversion(project_dir)
    source_path = project_dir / target["normalization"]["source_data_file"]
    canonical_path = project_dir / target["data_file"]
    source = pd.read_csv(source_path)
    canonical = pd.read_csv(canonical_path)
    source.loc[0, "M_Zp"] = 1.23456789
    canonical.loc[0, "M_Zp"] = 1234.56789
    source.to_csv(source_path, index=False)
    canonical.to_csv(canonical_path, index=False)
    _rewrite_record(project_dir, target)

    validate_target_normalization(
        project_dir,
        target,
        paper_id="arxiv:2601.01234v2",
    )


def test_exact_conversion_helper_has_no_fixed_significant_digit_ceiling(tmp_path) -> None:
    project_dir = make_compare_project(tmp_path)
    target = _configure_mass_conversion(project_dir)
    source_path = project_dir / target["normalization"]["source_data_file"]
    canonical_path = project_dir / target["data_file"]
    source_value = "0." + "1" * 2101
    exact_value = "111." + "1" * 2098
    rounded_value = "111." + "1" * 2045
    source_path.write_text(
        f"M_Zp,delta_a_mu\n{source_value},2.002\n",
        encoding="utf-8",
    )
    canonical_path.write_text(
        f"M_Zp,delta_a_mu\n{exact_value},2.002\n",
        encoding="utf-8",
    )
    _rewrite_record(project_dir, target)

    assert exact_decimal_linear_conversion_matches(
        Decimal(source_value),
        Decimal("1000"),
        Decimal("0"),
        Decimal(exact_value),
    )
    assert not exact_decimal_linear_conversion_matches(
        Decimal(source_value),
        Decimal("1000"),
        Decimal("0"),
        Decimal(rounded_value),
    )
    with pytest.raises(ValueError, match="does not reproduce canonical data"):
        validate_target_normalization(
            project_dir,
            target,
            paper_id="arxiv:2601.01234v2",
        )

    canonical_path.write_text(
        f"M_Zp,delta_a_mu\n{rounded_value},2.002\n",
        encoding="utf-8",
    )
    _rewrite_record(project_dir, target)
    with pytest.raises(ValueError, match="does not reproduce canonical data"):
        validate_target_normalization(
            project_dir,
            target,
            paper_id="arxiv:2601.01234v2",
        )


def test_identity_normalization_compares_exact_decimal_cells(tmp_path) -> None:
    project_dir = make_compare_project(tmp_path)
    _, _, target = _target(project_dir)
    source_path = project_dir / target["normalization"]["source_data_file"]
    source_path.write_text(
        source_path.read_text(encoding="utf-8").replace(
            "1.0,2.002", "1.00000000000000001,2.002", 1
        ),
        encoding="utf-8",
    )
    _rewrite_record(project_dir, target)

    with pytest.raises(ValueError, match="changed tabular values"):
        validate_target_normalization(
            project_dir,
            target,
            paper_id="arxiv:2601.01234v2",
        )


@pytest.mark.parametrize("token", ["1e-4000", "1e4000", "9007199254740993"])
def test_identity_canonical_numeric_cells_must_roundtrip_binary64(
    tmp_path, token
) -> None:
    project_dir = make_compare_project(tmp_path)
    _, _, target = _target(project_dir)
    source_path = project_dir / target["normalization"]["source_data_file"]
    canonical_path = project_dir / target["data_file"]
    text = f"M_Zp,delta_a_mu\n{token},2.002\n"
    source_path.write_text(text, encoding="utf-8")
    canonical_path.write_text(text, encoding="utf-8")
    _rewrite_record(project_dir, target)

    with pytest.raises(ValueError, match="changed tabular values"):
        validate_target_normalization(
            project_dir,
            target,
            paper_id="arxiv:2601.01234v2",
        )


def test_converted_raw_may_be_outside_binary64_if_canonical_is_stable(
    tmp_path,
) -> None:
    project_dir = make_compare_project(tmp_path)
    target = _configure_mass_conversion(
        project_dir,
        source_unit="eV",
        canonical_unit="TeV",
        factor=1.0e-12,
    )
    source_path = project_dir / target["normalization"]["source_data_file"]
    canonical_path = project_dir / target["data_file"]
    source_path.write_text(
        "M_Zp,delta_a_mu\n1e320,2.002\n",
        encoding="utf-8",
    )
    canonical_path.write_text(
        "M_Zp,delta_a_mu\n1e308,2.002\n",
        encoding="utf-8",
    )
    _rewrite_record(project_dir, target)

    validate_target_normalization(
        project_dir,
        target,
        paper_id="arxiv:2601.01234v2",
    )


def test_converted_table_rejects_nonzero_underflow(tmp_path) -> None:
    project_dir = make_compare_project(tmp_path)
    target = _configure_mass_conversion(
        project_dir,
        source_unit="fs",
        canonical_unit="s",
        factor=1.0e-15,
    )
    source_path = project_dir / target["normalization"]["source_data_file"]
    canonical_path = project_dir / target["data_file"]
    source = pd.read_csv(source_path)
    canonical = pd.read_csv(canonical_path)
    source["M_Zp"] = float.fromhex("0x0.0000000000001p-1022")
    canonical["M_Zp"] = 0.0
    source.to_csv(source_path, index=False)
    canonical.to_csv(canonical_path, index=False)
    _rewrite_record(project_dir, target)

    with pytest.raises(ValueError, match="does not reproduce canonical data"):
        validate_target_normalization(
            project_dir,
            target,
            paper_id="arxiv:2601.01234v2",
        )


def test_converted_numeric_and_categorical_columns_use_exact_typed_rules(
    tmp_path,
) -> None:
    project_dir = make_compare_project(tmp_path)
    target = _configure_mass_conversion(project_dir)
    normalization = target["normalization"]
    for field in ("source_units", "canonical_units"):
        normalization[field]["tag"] = "categorical"
    normalization["conversions"]["tag"] = {
        "operation": "linear",
        "factor": 1.0,
        "offset": 0.0,
    }
    source_path = project_dir / normalization["source_data_file"]
    canonical_path = project_dir / target["data_file"]
    source = pd.read_csv(source_path)
    canonical = pd.read_csv(canonical_path)
    source["tag"] = ["A", "B", "C"]
    canonical["tag"] = ["A", "B", "C"]
    source.to_csv(source_path, index=False)
    canonical.to_csv(canonical_path, index=False)
    _rewrite_record(project_dir, target)

    validate_target_normalization(
        project_dir,
        target,
        paper_id="arxiv:2601.01234v2",
    )
    canonical.loc[0, "tag"] = "changed"
    canonical.to_csv(canonical_path, index=False)
    _rewrite_record(project_dir, target)
    with pytest.raises(ValueError, match="categorical conversion"):
        validate_target_normalization(
            project_dir,
            target,
            paper_id="arxiv:2601.01234v2",
        )


@pytest.mark.parametrize("evidence", ["raw", "canonical", "record"])
def test_all_normalization_evidence_rejects_cross_extension_generated_alias(
    tmp_path, evidence
) -> None:
    project_dir = make_compare_project(tmp_path)
    target = _configure_mass_conversion(project_dir)
    paths = {
        "raw": project_dir / target["normalization"]["source_data_file"],
        "canonical": project_dir / target["data_file"],
        "record": project_dir / target["normalization"]["record_file"],
    }
    generated = project_dir / "numerics" / f"alias-{evidence}.artifact"
    generated.parent.mkdir(parents=True, exist_ok=True)
    generated.write_bytes(paths[evidence].read_bytes())

    with pytest.raises(ValueError, match="is not independent"):
        validate_target_normalization(
            project_dir,
            target,
            scan_csv=project_dir
            / "numerics"
            / "scan-results"
            / "analysis-001"
            / "scan.csv",
            paper_id="arxiv:2601.01234v2",
        )


def test_numeric_unit_conversion_rejects_boolean_source_values(tmp_path) -> None:
    project_dir = make_compare_project(tmp_path)
    target = _configure_mass_conversion(project_dir)
    source_path = project_dir / target["normalization"]["source_data_file"]
    canonical_path = project_dir / target["data_file"]
    source = pd.read_csv(source_path)
    source["M_Zp"] = True
    source.to_csv(source_path, index=False)
    canonical = pd.read_csv(canonical_path)
    canonical["M_Zp"] = 1000.0
    canonical.to_csv(canonical_path, index=False)
    _rewrite_record(project_dir, target)

    with pytest.raises(ValueError, match="contains boolean data"):
        validate_target_normalization(
            project_dir,
            target,
            paper_id="arxiv:2601.01234v2",
        )


@pytest.mark.parametrize(
    ("column", "wrong_unit", "expected_message"),
    [
        ("M_Zp", "MeV", "model-spec requires 'GeV'"),
        ("delta_a_mu", "GeV", "scan source emits 'dimensionless'"),
    ],
)
def test_comparator_binds_reference_units_to_scan_sources(
    repo_root, tmp_path, column, wrong_unit, expected_message
) -> None:
    project_dir = make_compare_project(tmp_path)
    targets_path, payload, target = _target(project_dir)
    target["normalization"]["source_units"][column] = wrong_unit
    target["normalization"]["canonical_units"][column] = wrong_unit
    write_json(targets_path, payload)
    _rewrite_record(project_dir, target)

    completed = run_compare(repo_root, project_dir, "run-001")

    assert completed.returncode == 1
    assert expected_message in completed.stderr
    assert not (project_dir / "reproduction" / "runs" / "run-001").exists()


def test_comparator_rejects_task_parameter_unit_drift(repo_root, tmp_path) -> None:
    project_dir = make_compare_project(tmp_path)
    meta_path = project_dir / "calculations" / "task-001" / "result-meta.json"
    metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    metadata["parameters"][0]["unit"] = "MeV"
    write_json(meta_path, metadata)

    completed = run_compare(repo_root, project_dir, "run-001")

    assert completed.returncode == 1
    assert "parameter units disagree with model-spec" in completed.stderr


def test_comparator_requires_custom_output_unit_to_match_target(
    repo_root, tmp_path
) -> None:
    project_dir = make_compare_project(tmp_path)
    config_path = project_dir / "numerics" / "scan-configs" / "analysis-001.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["observables"][0]["source"] = {
        "type": "custom",
        "function": "compute_delta_a_mu_custom",
        "canonical_unit": "GeV",
    }
    write_json(config_path, config)
    rebind_scan_graph(project_dir)

    completed = run_compare(repo_root, project_dir, "run-001")

    assert completed.returncode == 1
    assert "scan source emits 'GeV'" in completed.stderr


def test_matching_custom_output_unit_is_accepted(repo_root, tmp_path) -> None:
    project_dir = make_compare_project(tmp_path)
    config_path = project_dir / "numerics" / "scan-configs" / "analysis-001.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["observables"][0]["source"] = {
        "type": "custom",
        "function": "compute_delta_a_mu_custom",
        "canonical_unit": "dimensionless",
    }
    write_json(config_path, config)
    rebind_scan_graph(project_dir)

    completed = run_compare(repo_root, project_dir, "run-001")

    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_observable_threshold_exclusion_binds_quantitatively_used_units(
    repo_root, tmp_path
) -> None:
    project_dir = make_compare_project(
        tmp_path,
        targets=[default_target("boundary-units", kind="exclusion_region")],
    )

    completed = run_compare(repo_root, project_dir, "run-001")

    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_comparator_binds_analysis_fixed_parameter_unit(repo_root, tmp_path) -> None:
    target = default_target("fixed-unit")
    target["fixed"] = {"m_mu": 1.0}
    target["normalization"] = normalization_for_target(target)
    project_dir = make_compare_project(tmp_path, targets=[target])
    config_path = project_dir / "numerics" / "scan-configs" / "analysis-001.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["fixed_parameters"] = [{"canonical_name": "m_mu", "value": 1.0}]
    write_json(config_path, config)
    targets_path, payload, persisted_target = _target(project_dir)
    fixed_record = persisted_target["normalization"]["fixed_parameters"]["m_mu"]
    fixed_record["source_unit"] = "MeV"
    fixed_record["canonical_unit"] = "MeV"
    write_json(targets_path, payload)
    _rewrite_record(project_dir, persisted_target)

    completed = run_compare(repo_root, project_dir, "run-001")

    assert completed.returncode == 1
    assert "model-spec requires 'GeV'" in completed.stderr


@pytest.mark.parametrize("field", ["model_version", "model_checksum"])
def test_comparator_rejects_task_model_dependency_drift(
    repo_root, tmp_path, field
) -> None:
    project_dir = make_compare_project(tmp_path)
    meta_path = project_dir / "calculations" / "task-001" / "result-meta.json"
    metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    metadata["depends_on"][field] = (
        "v999" if field == "model_version" else "sha256:" + "b" * 64
    )
    write_json(meta_path, metadata)

    completed = run_compare(repo_root, project_dir, "run-001")

    assert completed.returncode == 1
    assert "model dependency does not match" in completed.stderr


def test_comparator_rejects_result_meta_task_id_drift(repo_root, tmp_path) -> None:
    project_dir = make_compare_project(tmp_path)
    meta_path = project_dir / "calculations" / "task-001" / "result-meta.json"
    metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    metadata["task_id"] = "task-002"
    write_json(meta_path, metadata)

    completed = run_compare(repo_root, project_dir, "run-001")

    assert completed.returncode == 1
    assert "does not match observable binding" in completed.stderr


def test_comparator_rejects_duplicate_result_meta_parameter_names(
    repo_root, tmp_path
) -> None:
    project_dir = make_compare_project(tmp_path)
    meta_path = project_dir / "calculations" / "task-001" / "result-meta.json"
    metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    metadata["parameters"].append(
        {"canonical_name": "M_Zp", "role": "fixed", "unit": "MeV"}
    )
    write_json(meta_path, metadata)

    completed = run_compare(repo_root, project_dir, "run-001")

    assert completed.returncode == 1
    assert "duplicate parameters" in completed.stderr


def test_raw_canonical_and_record_must_be_distinct_filesystem_objects(tmp_path) -> None:
    project_dir = make_compare_project(tmp_path)
    _, _, target = _target(project_dir)
    source = project_dir / target["normalization"]["source_data_file"]
    canonical = project_dir / target["data_file"]
    canonical.unlink()
    os.link(source, canonical)
    _rewrite_record(project_dir, target)

    with pytest.raises(ValueError, match="distinct filesystem objects"):
        validate_target_normalization(
            project_dir,
            target,
            paper_id="arxiv:2601.01234v2",
        )


def test_fixed_parameter_conversion_must_reproduce_exact_canonical_slice() -> None:
    target = {"fixed": {"mass": 1000.0}}
    normalization = {
        "fixed_parameters": {
            "mass": {
                "source_value": 1.0,
                "source_unit": "GeV",
                "canonical_value": 1000.0,
                "canonical_unit": "MeV",
                "operation": "linear",
                "factor": 1000.0,
                "offset": 0.0,
            }
        }
    }

    assert validate_fixed_parameter_normalization(target, normalization) is True
    normalization["fixed_parameters"]["mass"]["canonical_value"] = 999.0
    with pytest.raises(ValueError, match="does not reproduce target.fixed"):
        validate_fixed_parameter_normalization(target, normalization)


def test_fixed_parameter_conversion_rejects_nonzero_underflow() -> None:
    subnormal = float.fromhex("0x0.0000000000001p-1022")
    target = {"fixed": {"duration": 0.0}}
    normalization = {
        "fixed_parameters": {
            "duration": {
                "source_value": subnormal,
                "source_unit": "fs",
                "canonical_value": 0.0,
                "canonical_unit": "s",
                "operation": "linear",
                "factor": 1.0e-15,
                "offset": 0.0,
            }
        }
    }

    with pytest.raises(ValueError, match="underflows a nonzero value"):
        validate_fixed_parameter_normalization(target, normalization)


def test_fixed_identity_does_not_round_adjacent_large_integers() -> None:
    target = {"fixed": {"large": 9007199254740993}}
    normalization = {
        "fixed_parameters": {
            "large": {
                "source_value": 9007199254740992,
                "source_unit": "dimensionless",
                "canonical_value": 9007199254740992,
                "canonical_unit": "dimensionless",
                "operation": "linear",
                "factor": 1.0,
                "offset": 0.0,
            }
        }
    }

    with pytest.raises(ValueError, match="does not reproduce target.fixed"):
        validate_fixed_parameter_normalization(target, normalization)


def test_identity_normalization_accepts_authoritative_compound_unit() -> None:
    target = {"fixed": {"cross_section": 1.0}}
    normalization = {
        "fixed_parameters": {
            "cross_section": {
                "source_value": 1.0,
                "source_unit": "GeV^-2",
                "canonical_value": 1.0,
                "canonical_unit": "GeV^-2",
                "operation": "linear",
                "factor": 1.0,
                "offset": 0.0,
            }
        }
    }

    assert validate_fixed_parameter_normalization(target, normalization) is False


def test_mixed_observable_units_block_absolute_aggregate_and_keep_per_column_metrics() -> None:
    frame = pd.DataFrame(
        {"x": [1.0, 2.0], "y": [3.0, 4.0], "energy": [5.0, 6.0], "time": [7.0, 8.0]}
    )
    target = {
        "id": "mixed",
        "kind": "scan_table",
        "x_param": "x",
        "y_param": "y",
        "match_columns": ["x", "y"],
        "observables": ["energy", "time"],
        "scan_parameters": ["x", "y"],
        "fixed": {},
        "tolerance": {"kind": "absolute", "value": 0.1},
    }
    target["data_file"] = "literature/digitized/mixed.csv"
    target["normalization"] = normalization_for_target(target)
    target["normalization"]["canonical_units"].update(
        {"energy": "GeV", "time": "s"}
    )
    target["normalization"]["source_units"].update(
        {"energy": "GeV", "time": "s"}
    )

    blocked = scan_table_metrics(frame, frame, target)
    assert blocked.completeness["complete"] is False
    assert "absolute_tolerance_requires_one_shared_observable_unit" in blocked.completeness[
        "blocking_reasons"
    ]

    target["tolerance"] = {"kind": "relative", "value": 0.1}
    compared = scan_table_metrics(frame, frame, target)
    assert compared.completeness["complete"] is True
    assert "max_absolute_error" not in compared.metrics
    assert compared.metrics["max_absolute_error__energy"] == 0.0
    assert compared.metrics["max_absolute_error__time"] == 0.0
