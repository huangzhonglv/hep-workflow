from __future__ import annotations

import copy
import hashlib
import math
import shutil
from pathlib import Path
from typing import Any

import pytest

from scripts._scan_artifact_validation import (
    _require_contained,
    expected_scan_columns,
    validate_scan_artifact_pair,
    validate_scan_config_namespace,
)


ANALYSIS_ID = "analysis-001"


def _scan_paths(project_dir: Path) -> tuple[Path, Path, Path, Path]:
    config_path = project_dir / "numerics" / "scan-configs" / f"{ANALYSIS_ID}.json"
    result_dir = project_dir / "numerics" / "scan-results" / ANALYSIS_ID
    return (
        config_path,
        result_dir / "scan.csv",
        result_dir / "scan.meta.json",
        project_dir / "numerics" / f"analysis-summary-{ANALYSIS_ID}.md",
    )


def _checksum(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def test_containment_accepts_trusted_root_alias_but_rejects_internal_symlink(
    tmp_path: Path,
) -> None:
    project = tmp_path / "real-project"
    nested = project / "numerics" / "scan-results" / ANALYSIS_ID
    nested.mkdir(parents=True)
    target = nested / "scan.csv"
    target.write_text("x\n", encoding="utf-8")
    alias = tmp_path / "project-alias"
    alias.symlink_to(project, target_is_directory=True)

    issues: list[str] = []
    _require_contained(
        alias / "numerics" / "scan-results" / ANALYSIS_ID / "scan.csv",
        project,
        "scan CSV",
        issues,
    )
    assert issues == []

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "scan.csv").write_text("outside\n", encoding="utf-8")
    redirect = project / "redirect"
    redirect.symlink_to(outside, target_is_directory=True)
    issues = []
    _require_contained(redirect / "scan.csv", project, "scan CSV", issues)
    assert any("symlink component" in issue for issue in issues)


@pytest.fixture
def strict_scan_project(
    tmp_path: Path,
    project_copy_factory,
    read_json,
    write_json,
) -> Path:
    project_dir = project_copy_factory(tmp_path)
    config_path, csv_path, meta_path, summary_path = _scan_paths(project_dir)
    config = read_json(config_path)

    csv_path.write_text(
        "M_Hpp,v_Delta,m_lightest,Br_mu_to_egamma,"
        "c-005_verdict,c-005_margin,c-005_chi2,c-005_skip_reason,"
        "c-006_verdict,c-006_margin,c-006_chi2,c-006_skip_reason\n"
        "100.0,0.001,0.01,1.1e-13,allowed,0.9,,,allowed,0.9,,\n"
        "200.0,0.001,0.01,2.75e-14,allowed,0.8,,,allowed,0.8,,\n",
        encoding="utf-8",
    )
    meta = read_json(meta_path)
    meta["scan_config_snapshot"] = config
    meta["scan_config_source"] = config_path.read_text(encoding="utf-8")
    meta["scan_config_sha256"] = _checksum(config_path)
    meta["rng"] = {
        "algorithm": "numpy.random.PCG64",
        "algorithm_version": "pcg64-v1",
        "substream_scheme": "numpy-seedsequence-v1",
        "seed": config["seed"],
        "substreams": {"smoke": 0, "scan": 1},
        "consumers": [],
    }
    meta["n_points"] = 2
    meta["n_allowed"] = 2
    meta["n_excluded"] = 0
    meta["n_skipped"] = 0
    meta["scan_csv_sha256"] = _checksum(csv_path)
    write_json(meta_path, meta)
    summary_path.write_text(
        "# Analysis analysis-001: strict synthetic fixture\n\n"
        "## Scan coverage\n"
        "- Total points: 2\n"
        "- Allowed: 2 (100.00%)\n"
        "- Excluded: 0 (0.00%)\n"
        "- Skipped: 0 (0.00%)\n",
        encoding="utf-8",
    )
    return project_dir


def _read_csv_cells(csv_path: Path) -> list[list[str]]:
    return [line.split(",") for line in csv_path.read_text(encoding="utf-8").splitlines()]


def _write_csv_cells(csv_path: Path, rows: list[list[str]]) -> None:
    csv_path.write_text(
        "\n".join(",".join(row) for row in rows) + "\n",
        encoding="utf-8",
    )


def _mutate_csv(project_dir: Path, mutation: str) -> None:
    _, csv_path, _, _ = _scan_paths(project_dir)
    rows = _read_csv_cells(csv_path)
    header, first, second = rows
    if mutation == "extra_column":
        header.append("undeclared")
        first.append("1")
        second.append("1")
    elif mutation == "duplicate_header":
        header[1] = header[0]
    elif mutation == "wrong_order":
        header[0], header[1] = header[1], header[0]
    elif mutation == "ragged_row":
        first.pop()
    elif mutation == "nonfinite_observable":
        first[3] = "NaN"
    elif mutation == "blank_observable":
        first[3] = ""
    elif mutation == "invalid_verdict":
        first[4] = "maybe"
    elif mutation == "nonempty_skip_reason":
        first[7] = "backend failed"
    elif mutation == "nonfinite_margin":
        first[5] = "Infinity"
    elif mutation == "duplicate_coordinate":
        second[0] = first[0]
    elif mutation == "off_grid_coordinate":
        second[0] = "150.0"
    elif mutation == "wrong_fixed_value":
        first[1] = "0.002"
    else:  # pragma: no cover - protects the mutation table itself.
        raise AssertionError(f"unknown mutation {mutation}")
    _write_csv_cells(csv_path, rows)


def _mutate_json(path: Path, read_json, write_json, changes: dict[str, Any]) -> None:
    payload = read_json(path)
    payload.update(changes)
    write_json(path, payload)


def test_expected_scan_columns_and_valid_baseline(
    strict_scan_project: Path,
    read_json,
    repo_root: Path,
) -> None:
    config_path, _, _, _ = _scan_paths(strict_scan_project)
    config = read_json(config_path)

    assert expected_scan_columns(config) == [
        "M_Hpp",
        "v_Delta",
        "m_lightest",
        "Br_mu_to_egamma",
        "c-005_verdict",
        "c-005_margin",
        "c-005_chi2",
        "c-005_skip_reason",
        "c-006_verdict",
        "c-006_margin",
        "c-006_chi2",
        "c-006_skip_reason",
    ]
    assert validate_scan_config_namespace(config) == []
    assert (
        validate_scan_artifact_pair(
            strict_scan_project,
            ANALYSIS_ID,
            repo_root=repo_root,
        )
        == []
    )


def test_candidate_scan_pair_can_be_validated_before_publication(
    strict_scan_project: Path,
) -> None:
    config_path, csv_path, meta_path, summary_path = _scan_paths(strict_scan_project)
    staging_dir = strict_scan_project / "numerics" / ".publication-candidate"
    staged_result_dir = staging_dir / ANALYSIS_ID
    staged_result_dir.mkdir(parents=True)
    staged_csv = staged_result_dir / "scan.csv"
    staged_meta = staged_result_dir / "scan.meta.json"
    staged_summary = staging_dir / f"analysis-summary-{ANALYSIS_ID}.md"
    shutil.copy2(csv_path, staged_csv)
    shutil.copy2(meta_path, staged_meta)
    shutil.copy2(summary_path, staged_summary)

    assert validate_scan_artifact_pair(
        strict_scan_project,
        ANALYSIS_ID,
        scan_config_path=config_path,
        scan_csv_path=staged_csv,
        scan_meta_path=staged_meta,
        analysis_summary_path=staged_summary,
    ) == []


def test_historical_pair_validation_uses_embedded_snapshot_without_live_relabel(
    strict_scan_project: Path,
    read_json,
    write_json,
) -> None:
    config_path, _, meta_path, _ = _scan_paths(strict_scan_project)
    metadata = read_json(meta_path)
    historical_snapshot = metadata["scan_config_snapshot"]
    live_config = read_json(config_path)
    live_config["seed"] = live_config["seed"] + 1
    write_json(config_path, live_config)

    assert any(
        "execution semantics do not match" in issue
        for issue in validate_scan_artifact_pair(strict_scan_project, ANALYSIS_ID)
    )
    assert validate_scan_artifact_pair(
        strict_scan_project,
        ANALYSIS_ID,
        historical_scan_config_snapshot=historical_snapshot,
    ) == []


def test_scan_metadata_rng_consumers_are_derived_from_active_signatures(
    strict_scan_project: Path,
    read_json,
    write_json,
) -> None:
    _, _, meta_path, _ = _scan_paths(strict_scan_project)
    metadata = read_json(meta_path)
    metadata["rng"]["consumers"] = ["forged_consumer"]
    write_json(meta_path, metadata)

    assert any(
        "rng.consumers does not match" in issue
        for issue in validate_scan_artifact_pair(strict_scan_project, ANALYSIS_ID)
    )


@pytest.mark.parametrize(
    ("mutation", "expected_fragment"),
    [
        ({"algorithm": "numpy.random.MT19937"}, "rng.algorithm"),
        ({"algorithm_version": "ambient-v0"}, "rng.algorithm_version"),
        ({"substreams": {"smoke": 1, "scan": 0}}, "rng.substreams"),
        ({"consumers": ["duplicate", "duplicate"]}, "sorted unique"),
        ({"unexpected": True}, "rng fields must exactly equal"),
    ],
)
def test_scan_metadata_requires_the_exact_rng_contract_without_schema_help(
    strict_scan_project: Path,
    read_json,
    write_json,
    mutation: dict[str, Any],
    expected_fragment: str,
) -> None:
    _, _, meta_path, _ = _scan_paths(strict_scan_project)
    metadata = read_json(meta_path)
    metadata["rng"].update(mutation)
    write_json(meta_path, metadata)

    issues = validate_scan_artifact_pair(strict_scan_project, ANALYSIS_ID)

    assert any(expected_fragment in issue for issue in issues), issues


@pytest.mark.parametrize(
    ("collision_name", "owner_fragment"),
    [
        ("M_Hpp", "scan_parameters[0]"),
        ("c-005_verdict", "constraints_used[0]"),
    ],
)
def test_global_output_namespace_rejects_cross_role_collisions(
    strict_scan_project: Path,
    read_json,
    collision_name: str,
    owner_fragment: str,
) -> None:
    config_path, _, _, _ = _scan_paths(strict_scan_project)
    config = read_json(config_path)
    config["observables"].append(
        {
            "observable": collision_name,
            "source": {
                "type": "custom",
                "function": "m_eff_bb",
                "canonical_unit": "GeV",
            },
        }
    )

    issues = validate_scan_config_namespace(config)

    assert any("collides" in issue and owner_fragment in issue for issue in issues)
    with pytest.raises(ValueError, match="collides"):
        expected_scan_columns(config)


@pytest.mark.parametrize(
    ("mutation", "expected_fragment"),
    [
        ("extra_column", "header/order does not match"),
        ("duplicate_header", "header contains duplicate columns"),
        ("wrong_order", "header/order does not match"),
        ("ragged_row", "cells; expected exactly"),
        ("nonfinite_observable", "observable Br_mu_to_egamma must be finite"),
        ("blank_observable", "observable Br_mu_to_egamma must not be blank"),
        ("invalid_verdict", "c-005_verdict must be 'allowed' or 'excluded'"),
        ("nonempty_skip_reason", "c-005_skip_reason must be exactly empty"),
        ("nonfinite_margin", "c-005_margin must be finite"),
        ("duplicate_coordinate", "duplicates scan coordinate"),
        ("off_grid_coordinate", "is not on the exact configured grid"),
        ("wrong_fixed_value", "does not exactly match configured value"),
    ],
)
def test_scan_csv_malformed_states_fail_closed(
    strict_scan_project: Path,
    mutation: str,
    expected_fragment: str,
) -> None:
    _mutate_csv(strict_scan_project, mutation)

    issues = validate_scan_artifact_pair(strict_scan_project, ANALYSIS_ID)

    assert any(expected_fragment in issue for issue in issues), issues


@pytest.mark.parametrize(
    ("changes", "expected_fragment"),
    [
        ({"n_points": 0}, "n_points=0 does not match scan.csv row count 2"),
        (
            {"n_allowed": 0, "n_excluded": 2},
            "n_allowed=0 does not match CSV-derived count 2",
        ),
        ({"n_skipped": 1}, "completed scan requires n_skipped=0"),
    ],
)
def test_scan_meta_counts_are_recomputed_from_csv(
    strict_scan_project: Path,
    read_json,
    write_json,
    changes: dict[str, Any],
    expected_fragment: str,
) -> None:
    _, _, meta_path, _ = _scan_paths(strict_scan_project)
    _mutate_json(meta_path, read_json, write_json, changes)

    issues = validate_scan_artifact_pair(strict_scan_project, ANALYSIS_ID)

    assert any(expected_fragment in issue for issue in issues), issues


@pytest.mark.parametrize("mode", ["missing", "mismatch"])
def test_scan_csv_checksum_is_required_and_verified(
    strict_scan_project: Path,
    read_json,
    write_json,
    mode: str,
) -> None:
    _, _, meta_path, _ = _scan_paths(strict_scan_project)
    meta = read_json(meta_path)
    if mode == "missing":
        meta.pop("scan_csv_sha256")
    else:
        meta["scan_csv_sha256"] = f"sha256:{'0' * 64}"
    write_json(meta_path, meta)

    issues = validate_scan_artifact_pair(strict_scan_project, ANALYSIS_ID)

    expected = "missing required scan_csv_sha256" if mode == "missing" else "does not match scan.csv"
    assert any(expected in issue for issue in issues), issues


def test_scan_config_snapshot_must_exactly_match_live_config(
    strict_scan_project: Path,
    read_json,
    write_json,
) -> None:
    _, _, meta_path, _ = _scan_paths(strict_scan_project)
    meta = read_json(meta_path)
    meta["scan_config_snapshot"]["parallelism"] = 99
    write_json(meta_path, meta)

    issues = validate_scan_artifact_pair(strict_scan_project, ANALYSIS_ID)

    assert any("scan_config_source does not decode exactly" in issue for issue in issues)


def test_snapshot_comparison_does_not_coerce_boolean_to_integer(
    strict_scan_project: Path,
    read_json,
    write_json,
) -> None:
    _, _, meta_path, _ = _scan_paths(strict_scan_project)
    meta = read_json(meta_path)
    meta["scan_config_snapshot"]["seed"] = False
    write_json(meta_path, meta)

    issues = validate_scan_artifact_pair(strict_scan_project, ANALYSIS_ID)

    assert any("execution semantics do not match" in issue for issue in issues)


@pytest.mark.parametrize(
    ("field", "value", "expected_fragment"),
    [
        ("model_version", "v999", "model_version does not match"),
        ("model_checksum", f"sha256:{'9' * 64}", "model_checksum does not match"),
        ("seed", 999, "seed does not match"),
    ],
)
def test_scan_meta_identity_fields_bind_the_config_snapshot(
    strict_scan_project: Path,
    read_json,
    write_json,
    field: str,
    value: Any,
    expected_fragment: str,
) -> None:
    _, _, meta_path, _ = _scan_paths(strict_scan_project)
    _mutate_json(meta_path, read_json, write_json, {field: value})

    issues = validate_scan_artifact_pair(strict_scan_project, ANALYSIS_ID)

    assert any(expected_fragment in issue for issue in issues), issues


@pytest.mark.parametrize("duplicate_after_rounding", [False, True])
def test_scan_axis_must_be_strictly_increasing_and_binary64_unique(
    strict_scan_project: Path,
    read_json,
    write_json,
    duplicate_after_rounding: bool,
) -> None:
    config_path, _, meta_path, _ = _scan_paths(strict_scan_project)
    config = read_json(config_path)
    if duplicate_after_rounding:
        config["scan_parameters"][0]["range"] = [
            1.0,
            math.nextafter(1.0, math.inf),
        ]
        config["scan_parameters"][0]["grid"] = 3
    else:
        config["scan_parameters"][0]["range"] = [100.0, 100.0]
    write_json(config_path, config)
    meta = read_json(meta_path)
    meta["scan_config_snapshot"] = copy.deepcopy(config)
    write_json(meta_path, meta)

    issues = validate_scan_artifact_pair(strict_scan_project, ANALYSIS_ID)

    expected = "duplicate coordinates at binary64 precision" if duplicate_after_rounding else "strictly increasing"
    assert any(expected in issue for issue in issues), issues


def test_config_payload_and_path_identity_are_enforced(
    strict_scan_project: Path,
    read_json,
    write_json,
) -> None:
    config_path, _, meta_path, _ = _scan_paths(strict_scan_project)
    alternate = strict_scan_project / "numerics" / "alternate" / config_path.name
    alternate.parent.mkdir(parents=True)
    config = read_json(config_path)
    write_json(alternate, config)

    path_issues = validate_scan_artifact_pair(
        strict_scan_project,
        ANALYSIS_ID,
        scan_config_path=alternate,
    )
    assert any("does not match the canonical analysis path" in issue for issue in path_issues)

    config["analysis_id"] = "analysis-002"
    write_json(config_path, config)
    meta = read_json(meta_path)
    meta["scan_config_snapshot"] = copy.deepcopy(config)
    write_json(meta_path, meta)

    payload_issues = validate_scan_artifact_pair(strict_scan_project, ANALYSIS_ID)
    assert any("scan config analysis_id 'analysis-002'" in issue for issue in payload_issues)


def test_derived_metadata_is_not_accepted_as_run_scan_metadata(
    strict_scan_project: Path,
    write_json,
) -> None:
    _, _, meta_path, _ = _scan_paths(strict_scan_project)
    write_json(
        meta_path,
        {
            "analysis_id": ANALYSIS_ID,
            "description": "derived table",
            "generated_at": "2026-06-23T00:00:00Z",
            "source_analysis": ANALYSIS_ID,
        },
    )

    issues = validate_scan_artifact_pair(strict_scan_project, ANALYSIS_ID)

    assert any("not complete run-scan metadata" in issue for issue in issues)
    assert any("not run-scan metadata" in issue for issue in issues)


@pytest.mark.parametrize(
    "mode", ["empty", "wrong_counts", "wrong_analysis", "contradictory_counts"]
)
def test_analysis_summary_must_bind_identity_and_counts(
    strict_scan_project: Path,
    mode: str,
) -> None:
    _, _, _, summary_path = _scan_paths(strict_scan_project)
    if mode == "empty":
        summary_path.write_text("\n", encoding="utf-8")
        expected = "must be non-empty"
    elif mode == "wrong_counts":
        summary_path.write_text(
            "# Analysis analysis-001\n- Total points: 99\n- Allowed: 99\n"
            "- Excluded: 0\n- Skipped: 0\n",
            encoding="utf-8",
        )
        expected = "lacks exact marker '- Total points: 2'"
    elif mode == "wrong_analysis":
        summary_path.write_text(
            "# Analysis analysis-999\n- Total points: 2\n- Allowed: 2\n"
            "- Excluded: 0\n- Skipped: 0\n",
            encoding="utf-8",
        )
        expected = "does not identify analysis-001"
    else:
        summary_path.write_text(
            "# Analysis analysis-001\n- Total points: 2\n- Total points: 999\n"
            "- Allowed: 2\n- Excluded: 0\n- Skipped: 0\n",
            encoding="utf-8",
        )
        expected = "lacks exact marker '- Total points: 2'"

    issues = validate_scan_artifact_pair(strict_scan_project, ANALYSIS_ID)

    assert any(expected in issue for issue in issues), issues


def test_scan_csv_invalid_utf8_is_a_controlled_validation_error(
    strict_scan_project: Path,
) -> None:
    _, csv_path, _, _ = _scan_paths(strict_scan_project)
    csv_path.write_bytes(b"\xff\xfe\x00")

    issues = validate_scan_artifact_pair(strict_scan_project, ANALYSIS_ID)

    assert any("scan.csv is not valid UTF-8" in issue for issue in issues), issues


@pytest.mark.parametrize("target", ["config", "meta"])
def test_scan_pair_json_uses_the_strict_loader(
    strict_scan_project: Path,
    target: str,
) -> None:
    config_path, _, meta_path, _ = _scan_paths(strict_scan_project)
    path = config_path if target == "config" else meta_path
    path.write_text('{"analysis_id":"analysis-001","analysis_id":"analysis-001"}\n', encoding="utf-8")

    issues = validate_scan_artifact_pair(strict_scan_project, ANALYSIS_ID)

    assert any("duplicate object key" in issue for issue in issues), issues
