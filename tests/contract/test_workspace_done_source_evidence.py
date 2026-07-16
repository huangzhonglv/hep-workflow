from __future__ import annotations

from pathlib import Path
import hashlib
import json
import subprocess
import sys

import pytest


ANALYSIS_ID = "analysis-001"


def _run_validator(repo_root: Path, project_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(repo_root / "scripts" / "validate_workspace_projects.py"),
            "--workspace-root",
            str(project_dir.parent),
            project_dir.name,
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize(
    "artifact_name,required_relpaths",
    [
        ("idea", ("idea/proposal.md",)),
        (
            "model",
            ("model/model-spec.json", "model/calc-tasks.json"),
        ),
        ("constraints", ("constraints/constraints-data.json",)),
        (
            "literature",
            (
                "literature/paper-meta.json",
                "literature/paper-extract.json",
                "literature/repro-targets.json",
            ),
        ),
    ],
)
def test_arbitrary_placeholder_file_cannot_support_done_status(
    tmp_path: Path,
    project_copy_factory,
    read_json,
    write_json,
    repo_root: Path,
    artifact_name: str,
    required_relpaths: tuple[str, ...],
) -> None:
    project_dir = project_copy_factory(tmp_path)
    placeholder_relpath = f"{artifact_name}/placeholder.txt"
    placeholder_path = project_dir / placeholder_relpath
    placeholder_path.parent.mkdir(parents=True, exist_ok=True)
    placeholder_path.write_text("not source-of-truth evidence\n", encoding="utf-8")

    manifest_path = project_dir / "manifest.json"
    manifest = read_json(manifest_path)
    artifact = manifest["artifacts"].get(artifact_name)
    if not isinstance(artifact, dict):
        artifact = {
            "status": "done",
            "files": [],
            "produced_by": "pytest",
            "timestamp": "2026-07-13T00:00:00Z",
        }
        manifest["artifacts"][artifact_name] = artifact
    artifact["status"] = "done"
    artifact["files"] = [placeholder_relpath]
    write_json(manifest_path, manifest)

    completed = _run_validator(repo_root, project_dir)
    combined = completed.stdout + completed.stderr

    assert completed.returncode != 0
    for relpath in required_relpaths:
        assert (
            f"status='done' requires {relpath!r} in artifacts.{artifact_name}.files"
            in combined
        )


def test_done_model_does_not_skip_missing_model_spec(
    tmp_path: Path,
    project_copy_factory,
    repo_root: Path,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    (project_dir / "model" / "model-spec.json").unlink()

    completed = _run_validator(repo_root, project_dir)
    combined = completed.stdout + completed.stderr

    assert completed.returncode != 0
    assert (
        "artifacts.model.status='done' requires non-empty regular file "
        "'model/model-spec.json'"
    ) in combined


def test_done_model_checksum_binds_exact_model_spec_bytes(
    tmp_path: Path,
    project_copy_factory,
    repo_root: Path,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    model_spec_path = project_dir / "model" / "model-spec.json"
    model_spec_path.write_bytes(model_spec_path.read_bytes() + b"\n")

    completed = _run_validator(repo_root, project_dir)
    combined = completed.stdout + completed.stderr

    assert completed.returncode != 0
    assert (
        "artifacts.model.checksum does not match exact model/model-spec.json bytes"
        in combined
    )


def test_done_model_version_binds_model_spec_payload(
    tmp_path: Path,
    project_copy_factory,
    read_json,
    write_json,
    repo_root: Path,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    model_spec_path = project_dir / "model" / "model-spec.json"
    model_spec = read_json(model_spec_path)
    model_spec["version"] = "v2"
    write_json(model_spec_path, model_spec)

    completed = _run_validator(repo_root, project_dir)

    assert completed.returncode != 0
    assert (
        "artifacts.model.version does not match model/model-spec.json version"
        in completed.stdout + completed.stderr
    )


@pytest.mark.parametrize(
    "dependency_field,forged_value",
    [
        ("version", None),
        ("version", "v999"),
        ("checksum", None),
        ("checksum", f"sha256:{'0' * 64}"),
    ],
)
def test_done_calculations_dependency_must_exactly_match_current_model(
    tmp_path: Path,
    project_copy_factory,
    read_json,
    write_json,
    repo_root: Path,
    dependency_field: str,
    forged_value: str | None,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    manifest_path = project_dir / "manifest.json"
    manifest = read_json(manifest_path)
    manifest["artifacts"]["calculations"]["depends_on"]["model"][
        dependency_field
    ] = forged_value
    write_json(manifest_path, manifest)

    completed = _run_validator(repo_root, project_dir)

    assert completed.returncode != 0
    assert (
        "artifacts.calculations.status='done' requires depends_on.model "
        "to exactly match artifacts.model version and checksum"
        in completed.stdout + completed.stderr
    )


@pytest.mark.parametrize(
    "dependency_path,forged_value,expected_fragment",
    [
        (
            ("model", "version"),
            None,
            "model dependency does not match its scan snapshot",
        ),
        (
            ("model", "checksum"),
            f"sha256:{'0' * 64}",
            "model dependency does not match its scan snapshot",
        ),
        (
            ("calculations", "model_version"),
            None,
            "calculation dependency does not match its scan snapshot",
        ),
        (
            ("calculations", "model_version"),
            "v999",
            "calculation dependency does not match its scan snapshot",
        ),
        (
            ("constraints", "checksum"),
            None,
            "constraints dependency does not match its recorded scan graph",
        ),
        (
            ("constraints", "checksum"),
            f"sha256:{'0' * 64}",
            "constraints dependency does not match its recorded scan graph",
        ),
    ],
)
def test_done_numerics_dependencies_reject_null_and_stale_claims(
    tmp_path: Path,
    project_copy_factory,
    read_json,
    write_json,
    repo_root: Path,
    dependency_path: tuple[str, str],
    forged_value: str | None,
    expected_fragment: str,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    manifest_path = project_dir / "manifest.json"
    manifest = read_json(manifest_path)
    dependency_group, dependency_field = dependency_path
    manifest["artifacts"]["numerics"]["analyses"][0]["depends_on"][dependency_group][
        dependency_field
    ] = forged_value
    write_json(manifest_path, manifest)

    completed = _run_validator(repo_root, project_dir)

    assert completed.returncode != 0
    assert expected_fragment in completed.stdout + completed.stderr


def test_done_numerics_constraint_dependency_binds_exact_file_bytes(
    tmp_path: Path,
    project_copy_factory,
    repo_root: Path,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    constraints_path = project_dir / "constraints" / "constraints-data.json"
    constraints_path.write_bytes(constraints_path.read_bytes() + b"\n")

    completed = _run_validator(repo_root, project_dir)

    assert completed.returncode != 0
    assert "must be marked stale" in completed.stdout + completed.stderr


@pytest.mark.parametrize("declared_tasks", [[], ["task-001", "task-999"]])
def test_done_numerics_tasks_match_their_analysis_scan_dependencies(
    tmp_path: Path,
    project_copy_factory,
    read_json,
    write_json,
    repo_root: Path,
    declared_tasks: list[str],
) -> None:
    project_dir = project_copy_factory(tmp_path)
    manifest_path = project_dir / "manifest.json"
    manifest = read_json(manifest_path)
    manifest["artifacts"]["numerics"]["analyses"][0]["depends_on"]["calculations"][
        "tasks"
    ] = declared_tasks
    write_json(manifest_path, manifest)

    completed = _run_validator(repo_root, project_dir)
    combined = completed.stdout + completed.stderr

    assert completed.returncode != 0
    assert "calculation dependency does not match its scan snapshot" in combined


@pytest.mark.parametrize(
    "field,forged,fragment",
    [
        (
            "produced_by",
            "forged-writer",
            "produced_by must equal the deterministic latest analysis producer",
        ),
        (
            "timestamp",
            "1999-01-01T00:00:00Z",
            "timestamp must equal the deterministic latest analysis timestamp",
        ),
    ],
)
def test_numerics_aggregate_provenance_is_derived_from_latest_analysis(
    tmp_path: Path,
    project_copy_factory,
    read_json,
    write_json,
    repo_root: Path,
    field: str,
    forged: str,
    fragment: str,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    manifest_path = project_dir / "manifest.json"
    manifest = read_json(manifest_path)
    manifest["artifacts"]["numerics"][field] = forged
    write_json(manifest_path, manifest)

    completed = _run_validator(repo_root, project_dir)

    assert completed.returncode != 0
    assert fragment in completed.stdout + completed.stderr


def test_stale_scan_still_requires_finite_intrinsic_evidence(
    tmp_path: Path,
    project_copy_factory,
    read_json,
    write_json,
    repo_root: Path,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    manifest_path = project_dir / "manifest.json"
    manifest = read_json(manifest_path)
    manifest["artifacts"]["numerics"]["analyses"][0]["status"] = "stale"
    manifest["artifacts"]["numerics"]["status"] = "stale"
    write_json(manifest_path, manifest)

    csv_path = (
        project_dir
        / "numerics"
        / "scan-results"
        / ANALYSIS_ID
        / "scan.csv"
    )
    rows = csv_path.read_text(encoding="utf-8").splitlines()
    cells = rows[1].split(",")
    cells[3] = "NaN"
    rows[1] = ",".join(cells)
    csv_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    meta_path = csv_path.with_name("scan.meta.json")
    meta = read_json(meta_path)
    meta["scan_csv_sha256"] = "sha256:" + hashlib.sha256(
        csv_path.read_bytes()
    ).hexdigest()
    write_json(meta_path, meta)

    completed = _run_validator(repo_root, project_dir)
    combined = completed.stdout + completed.stderr

    assert completed.returncode != 0
    assert "finite" in combined.lower()


def test_stale_scan_requires_a_direct_or_transitive_dependency_drift(
    tmp_path: Path,
    project_copy_factory,
    read_json,
    write_json,
    repo_root: Path,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    manifest_path = project_dir / "manifest.json"
    manifest = read_json(manifest_path)
    manifest["artifacts"]["numerics"]["analyses"][0]["status"] = "stale"
    manifest["artifacts"]["numerics"]["status"] = "stale"
    write_json(manifest_path, manifest)

    completed = _run_validator(repo_root, project_dir)

    assert completed.returncode != 0
    assert (
        "input provenance, including transitive calculations, still matches"
        in completed.stdout + completed.stderr
    )


@pytest.mark.parametrize("mutation", ["invalid-root", "incomplete-coverage"])
def test_stale_scan_still_requires_valid_complete_recorded_graph(
    tmp_path: Path,
    project_copy_factory,
    read_json,
    write_json,
    rebind_calculation_result,
    rebind_scan_result,
    repo_root: Path,
    mutation: str,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    rebind_calculation_result(project_dir)
    rebind_scan_result(project_dir)
    manifest_path = project_dir / "manifest.json"
    manifest = read_json(manifest_path)
    manifest["artifacts"]["numerics"]["analyses"][0]["status"] = "stale"
    manifest["artifacts"]["numerics"]["status"] = "stale"
    write_json(manifest_path, manifest)

    # Preserve valid JSON while making the recorded constraints hash historical.
    constraints_path = project_dir / "constraints" / "constraints-data.json"
    constraints_path.write_bytes(constraints_path.read_bytes() + b"\n")
    meta_path = (
        project_dir
        / "numerics"
        / "scan-results"
        / ANALYSIS_ID
        / "scan.meta.json"
    )
    metadata = read_json(meta_path)
    if mutation == "invalid-root":
        metadata["input_provenance"]["root_sha256"] = "sha256:" + "0" * 64
    else:
        constraints_entry = next(
            entry
            for entry in metadata["input_provenance"]["entries"]
            if entry["scope"] == "project" and entry["role"] == "constraints-data"
        )
        canonical = {
            "entries": [constraints_entry],
            "verification_status": "verified",
            "version": "sha256-bytes-v1",
        }
        metadata["input_provenance"] = {
            **canonical,
            "root_sha256": "sha256:"
            + hashlib.sha256(
                json.dumps(
                    canonical,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                    allow_nan=False,
                ).encode("utf-8")
            ).hexdigest(),
        }
    write_json(meta_path, metadata)

    completed = _run_validator(repo_root, project_dir)
    combined = completed.stdout + completed.stderr

    assert completed.returncode != 0
    if mutation == "invalid-root":
        assert "root_sha256 does not match canonical entries" in combined
    else:
        assert "scan-runner" in combined or "missing expected entries" in combined


def test_stale_scan_uses_embedded_config_for_intrinsic_validation(
    tmp_path: Path,
    project_copy_factory,
    read_json,
    write_json,
    rebind_calculation_result,
    rebind_scan_result,
    repo_root: Path,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    rebind_calculation_result(project_dir)
    rebind_scan_result(project_dir)

    manifest_path = project_dir / "manifest.json"
    manifest = read_json(manifest_path)
    manifest["artifacts"]["numerics"]["analyses"][0]["status"] = "stale"
    manifest["artifacts"]["numerics"]["status"] = "stale"
    write_json(manifest_path, manifest)

    config_path = (
        project_dir / "numerics" / "scan-configs" / f"{ANALYSIS_ID}.json"
    )
    live_config = read_json(config_path)
    live_config["description"] = "A valid live config whose bytes changed after the run."
    write_json(config_path, live_config)

    completed = _run_validator(repo_root, project_dir)

    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_benchmarks_remain_optional_for_done_model(
    tmp_path: Path,
    project_copy_factory,
    read_json,
    write_json,
    rebind_calculation_result,
    rebind_scan_result,
    repo_root: Path,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    benchmarks_path = project_dir / "model" / "benchmarks.json"
    benchmarks_path.unlink()
    manifest_path = project_dir / "manifest.json"
    manifest = read_json(manifest_path)
    manifest["artifacts"]["model"]["files"].remove("model/benchmarks.json")
    write_json(manifest_path, manifest)
    rebind_calculation_result(project_dir)
    rebind_scan_result(project_dir)

    completed = _run_validator(repo_root, project_dir)

    assert completed.returncode == 0, completed.stdout + completed.stderr


def _replace_with_derived_scan_meta(
    project_dir: Path,
    read_json,
    write_json,
) -> None:
    meta_path = (
        project_dir
        / "numerics"
        / "scan-results"
        / ANALYSIS_ID
        / "scan.meta.json"
    )
    run_meta = read_json(meta_path)
    write_json(
        meta_path,
        {
            "analysis_id": ANALYSIS_ID,
            "description": "Derived analysis metadata is not run-scan evidence.",
            "generated_at": "2026-07-13T00:00:00Z",
            "scan_csv_sha256": run_meta["scan_csv_sha256"],
            "input_provenance": run_meta["input_provenance"],
            "source_analysis": "analysis-000",
        },
    )


def test_derived_scan_meta_cannot_bypass_completed_pair_validation(
    tmp_path: Path,
    project_copy_factory,
    read_json,
    write_json,
    repo_root: Path,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    _replace_with_derived_scan_meta(project_dir, read_json, write_json)

    completed = _run_validator(repo_root, project_dir)
    combined = completed.stdout + completed.stderr

    assert completed.returncode != 0
    assert (
        "derived analysis metadata cannot serve as a completed run-scan artifact"
        in combined
    )
    assert "scan.meta.json is not complete run-scan metadata" in combined


def test_derived_scan_meta_with_snapshot_still_fails_as_incomplete_run_metadata(
    tmp_path: Path,
    project_copy_factory,
    read_json,
    write_json,
    repo_root: Path,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    _replace_with_derived_scan_meta(project_dir, read_json, write_json)
    meta_path = (
        project_dir
        / "numerics"
        / "scan-results"
        / ANALYSIS_ID
        / "scan.meta.json"
    )
    derived_meta = read_json(meta_path)
    derived_meta["history_action"] = "numerics_analysis_complete"
    derived_meta["scan_config_snapshot"] = read_json(
        project_dir / "numerics" / "scan-configs" / f"{ANALYSIS_ID}.json"
    )
    write_json(meta_path, derived_meta)

    completed = _run_validator(repo_root, project_dir)
    combined = completed.stdout + completed.stderr

    assert completed.returncode != 0
    assert f"FAIL numerics scan artifact pair {ANALYSIS_ID}" in combined
    assert "scan.meta.json is not complete run-scan metadata" in combined


@pytest.mark.parametrize(
    "missing_relpath,expected_fragment",
    [
        (
            "numerics/scan-configs/analysis-001.json",
            "missing scan config",
        ),
        (
            "numerics/scan-results/analysis-001/scan.csv",
            "missing scan CSV",
        ),
    ],
)
def test_derived_scan_meta_fails_closed_when_pair_component_is_missing(
    tmp_path: Path,
    project_copy_factory,
    read_json,
    write_json,
    repo_root: Path,
    missing_relpath: str,
    expected_fragment: str,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    _replace_with_derived_scan_meta(project_dir, read_json, write_json)
    (project_dir / missing_relpath).unlink()

    completed = _run_validator(repo_root, project_dir)
    combined = completed.stdout + completed.stderr

    assert completed.returncode != 0
    assert expected_fragment in combined
