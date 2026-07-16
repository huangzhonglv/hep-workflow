from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Callable

from jsonschema import Draft202012Validator
import pytest

from scripts._scan_artifact_validation import validate_scan_artifact_pair


ANALYSIS_ID = "analysis-001"


def _workspace_validation(
    repo_root: Path,
    project_dir: Path,
) -> subprocess.CompletedProcess[str]:
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


def _sha256(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _set_nested(payload: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    current: dict[str, Any] = payload
    for part in path[:-1]:
        current = current[part]
    current[path[-1]] = value


@pytest.mark.parametrize(
    "artifact,path,value",
    [
        ("idea", ("files",), []),
        ("model", ("checksum",), None),
        ("calculations", ("completed_tasks",), []),
        ("calculations", ("pending_tasks",), ["task-001"]),
        ("constraints", ("files",), []),
        ("numerics", ("analyses",), []),
        ("literature", ("files",), []),
        ("reproduction", ("runs",), []),
    ],
)
def test_done_status_requires_nonempty_completion_evidence(
    repo_root: Path,
    numerics_contract_fixture_path: Path,
    artifact: str,
    path: tuple[str, ...],
    value: Any,
) -> None:
    schema = json.loads(
        (repo_root / "schemas" / "manifest.schema.json").read_text(encoding="utf-8")
    )
    manifest = json.loads(
        (numerics_contract_fixture_path / "manifest.json").read_text(encoding="utf-8")
    )
    model = manifest["artifacts"]["model"]
    manifest["artifacts"]["literature"] = {
        "status": "done",
        "files": ["literature/paper-meta.json"],
        "produced_by": "pytest-fixture",
        "timestamp": "2026-07-13T00:00:00Z",
    }
    manifest["artifacts"]["reproduction"] = {
        "status": "done",
        "runs": ["run-001"],
        "depends_on": {
            "model": {
                "version": model["version"],
                "checksum": model["checksum"],
            },
            "literature": {"checksum": f"sha256:{'0' * 64}"},
            "numerics": {"analyses": [ANALYSIS_ID]},
        },
        "produced_by": "pytest-fixture",
        "timestamp": "2026-07-13T00:00:00Z",
    }
    _set_nested(manifest["artifacts"][artifact], path, value)

    errors = list(Draft202012Validator(schema).iter_errors(manifest))

    assert errors, (artifact, path)


@pytest.mark.parametrize("artifact", ["idea", "model", "constraints", "numerics"])
@pytest.mark.parametrize("field", ["produced_by", "timestamp"])
def test_done_status_requires_producer_and_timestamp(
    repo_root: Path,
    numerics_contract_fixture_path: Path,
    artifact: str,
    field: str,
) -> None:
    schema = json.loads(
        (repo_root / "schemas" / "manifest.schema.json").read_text(encoding="utf-8")
    )
    manifest = json.loads(
        (numerics_contract_fixture_path / "manifest.json").read_text(encoding="utf-8")
    )
    manifest["artifacts"][artifact][field] = None

    assert list(Draft202012Validator(schema).iter_errors(manifest))


def test_calculations_stale_is_evidence_bearing_and_artifact_specific(
    repo_root: Path,
    numerics_contract_fixture_path: Path,
) -> None:
    schema = json.loads(
        (repo_root / "schemas" / "manifest.schema.json").read_text(encoding="utf-8")
    )
    manifest = json.loads(
        (numerics_contract_fixture_path / "manifest.json").read_text(encoding="utf-8")
    )
    validator = Draft202012Validator(schema)
    manifest["artifacts"]["calculations"]["status"] = "stale"

    assert not list(validator.iter_errors(manifest))

    no_evidence = deepcopy(manifest)
    no_evidence["artifacts"]["calculations"]["completed_tasks"] = []
    assert list(validator.iter_errors(no_evidence))

    no_dependency = deepcopy(manifest)
    no_dependency["artifacts"]["calculations"]["depends_on"]["model"][
        "checksum"
    ] = None
    assert list(validator.iter_errors(no_dependency))

    invalid_model_status = deepcopy(manifest)
    invalid_model_status["artifacts"]["model"]["status"] = "stale"
    assert list(validator.iter_errors(invalid_model_status))


@pytest.mark.parametrize(
    "component,expected_fragment",
    [
        ("scan.csv", "missing scan CSV"),
        ("scan.meta.json", "missing scan metadata"),
        ("summary", "missing analysis summary"),
    ],
)
def test_completed_scan_requires_the_full_artifact_pair_and_summary(
    tmp_path: Path,
    project_copy_factory,
    component: str,
    expected_fragment: str,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    result_dir = project_dir / "numerics" / "scan-results" / ANALYSIS_ID
    if component == "summary":
        target = project_dir / "numerics" / f"analysis-summary-{ANALYSIS_ID}.md"
    else:
        target = result_dir / component
    target.unlink()

    issues = validate_scan_artifact_pair(project_dir, ANALYSIS_ID)

    assert any(expected_fragment in issue for issue in issues), issues


def test_internally_consistent_partial_grid_is_not_a_completed_scan(
    tmp_path: Path,
    project_copy_factory,
    read_json,
    write_json,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    result_dir = project_dir / "numerics" / "scan-results" / ANALYSIS_ID
    csv_path = result_dir / "scan.csv"
    lines = csv_path.read_text(encoding="utf-8").splitlines()
    csv_path.write_text("\n".join(lines[:2]) + "\n", encoding="utf-8")
    meta_path = result_dir / "scan.meta.json"
    metadata = read_json(meta_path)
    metadata.update(
        {
            "n_points": 1,
            "n_allowed": 1,
            "n_excluded": 0,
            "n_skipped": 0,
            "scan_csv_sha256": _sha256(csv_path),
        }
    )
    write_json(meta_path, metadata)
    summary = project_dir / "numerics" / f"analysis-summary-{ANALYSIS_ID}.md"
    summary.write_text(
        "# Analysis analysis-001\n"
        "- Total points: 1\n"
        "- Allowed: 1\n"
        "- Excluded: 0\n"
        "- Skipped: 0\n",
        encoding="utf-8",
    )

    issues = validate_scan_artifact_pair(project_dir, ANALYSIS_ID)

    assert any("configured Cartesian grid size 2" in issue for issue in issues), issues
    assert any("one unique complete Cartesian grid" in issue for issue in issues), issues


def _model_namespace_collision(payload: dict[str, Any]) -> None:
    payload["parameters"][0]["name"] = payload["fields"][0]["name"]


def _duplicate_task_id(payload: dict[str, Any]) -> None:
    duplicate = deepcopy(payload["tasks"][0])
    duplicate["title"] = "A distinct task object reusing the same workflow ID"
    payload["tasks"].append(duplicate)


def _duplicate_constraint_id(payload: dict[str, Any]) -> None:
    duplicate = deepcopy(payload["constraints"][0])
    duplicate["name"] = "A distinct constraint object reusing the same workflow ID"
    payload["constraints"].append(duplicate)


@pytest.mark.parametrize(
    "relative_path,mutator,expected_fragment",
    [
        (
            "model/model-spec.json",
            _model_namespace_collision,
            "model field/parameter canonical namespace has duplicates",
        ),
        (
            "model/calc-tasks.json",
            _duplicate_task_id,
            "model/calc-tasks.json contains duplicate task_id values",
        ),
        (
            "constraints/constraints-data.json",
            _duplicate_constraint_id,
            "constraints/constraints-data.json contains duplicate id values",
        ),
    ],
)
def test_workspace_rejects_cross_collection_and_workflow_id_duplicates(
    tmp_path: Path,
    project_copy_factory,
    read_json,
    write_json,
    repo_root: Path,
    relative_path: str,
    mutator: Callable[[dict[str, Any]], None],
    expected_fragment: str,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    path = project_dir / relative_path
    payload = read_json(path)
    mutator(payload)
    write_json(path, payload)

    completed = _workspace_validation(repo_root, project_dir)

    assert completed.returncode != 0
    assert expected_fragment in completed.stdout + completed.stderr


def test_duplicate_figure_output_key_fails_before_creating_output_directory(
    tmp_path: Path,
    project_copy_factory,
    read_json,
    write_json,
    rebind_calculation_result,
    rebind_scan_result,
    make_figures_script: Path,
    repo_root: Path,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    rebind_calculation_result(project_dir)
    config_path = (
        project_dir / "numerics" / "scan-configs" / f"{ANALYSIS_ID}.json"
    )
    config = read_json(config_path)
    first = {
        "kind": "scan_1d",
        "x": "M_Hpp",
        "observables": ["Br_mu_to_egamma"],
        "overlay_constraint_bands": True,
    }
    second = dict(first, overlay_constraint_bands=False)
    config["figures"] = [first, second]
    write_json(config_path, config)
    rebind_scan_result(project_dir)
    output_dir = project_dir / "numerics" / "figures" / ANALYSIS_ID
    manifest_before = (project_dir / "manifest.json").read_bytes()

    completed = subprocess.run(
        [
            sys.executable,
            str(make_figures_script),
            "--project-dir",
            str(project_dir),
            "--analysis-id",
            ANALYSIS_ID,
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert "same output basename" in completed.stdout + completed.stderr
    assert not output_dir.exists()
    assert (project_dir / "manifest.json").read_bytes() == manifest_before


@pytest.mark.parametrize("damage", ["missing", "empty"])
def test_done_numerics_requires_every_configured_figure_pair_and_manifest_entry(
    tmp_path: Path,
    project_copy_factory,
    read_json,
    write_json,
    rebind_calculation_result,
    rebind_scan_result,
    make_figures_script: Path,
    repo_root: Path,
    damage: str,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    rebind_calculation_result(project_dir)
    config_path = (
        project_dir / "numerics" / "scan-configs" / f"{ANALYSIS_ID}.json"
    )
    config = read_json(config_path)
    config["figures"] = [
        {
            "kind": "scan_1d",
            "x": "M_Hpp",
            "observables": ["Br_mu_to_egamma"],
            "overlay_constraint_bands": True,
        }
    ]
    write_json(config_path, config)

    rebind_scan_result(project_dir)
    rendered = subprocess.run(
        [
            sys.executable,
            str(make_figures_script),
            "--project-dir",
            str(project_dir),
            "--analysis-id",
            ANALYSIS_ID,
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    assert rendered.returncode == 0, rendered.stdout + rendered.stderr

    figure_dir = project_dir / "numerics" / "figures" / ANALYSIS_ID
    basename = "scan1d-M_Hpp-Br_mu_to_egamma"
    pdf_path = figure_dir / f"{basename}.pdf"
    png_path = figure_dir / f"{basename}.png"

    manifest_path = project_dir / "manifest.json"
    expected_relpaths = [
        path.relative_to(project_dir).as_posix() for path in (pdf_path, png_path)
    ]

    baseline = _workspace_validation(repo_root, project_dir)
    assert baseline.returncode == 0, baseline.stdout + baseline.stderr

    # A completed analysis cannot silently lose one half of its configured
    # PDF/PNG pair merely by dropping that path from manifest evidence.
    if damage == "missing":
        manifest = read_json(manifest_path)
        manifest["artifacts"]["numerics"]["files"].remove(expected_relpaths[1])
        manifest["artifacts"]["numerics"]["analyses"][0]["files"].remove(
            expected_relpaths[1]
        )
        write_json(manifest_path, manifest)
        png_path.unlink()
    else:
        png_path.write_bytes(b"")

    completed = _workspace_validation(repo_root, project_dir)
    combined = completed.stdout + completed.stderr
    assert completed.returncode != 0
    assert expected_relpaths[1] in combined


def test_workspace_rejects_duplicate_result_parameter_identity(
    tmp_path: Path,
    project_copy_factory,
    read_json,
    write_json,
    rebind_calculation_result,
    rebind_scan_result,
    repo_root: Path,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    meta_path = (
        project_dir / "calculations" / "task-001" / "result-meta.json"
    )
    metadata = read_json(meta_path)
    duplicate = deepcopy(metadata["parameters"][0])
    duplicate["role"] = "fixed"
    metadata["parameters"].append(duplicate)
    write_json(meta_path, metadata)
    rebind_calculation_result(project_dir)
    rebind_scan_result(project_dir)

    completed = _workspace_validation(repo_root, project_dir)

    assert completed.returncode != 0
    assert "duplicate canonical parameter names ['M_Hpp']" in (
        completed.stdout + completed.stderr
    )


@pytest.mark.parametrize(
    ("mutate_return", "mutate_observable", "expected_fragment"),
    [
        (True, False, "return_value.name 'DifferentObservable' does not match observable"),
        (
            True,
            True,
            "observable 'DifferentObservable' does not match calc-tasks target_quantity",
        ),
    ],
)
def test_workspace_binds_result_observable_across_metadata_and_task(
    tmp_path: Path,
    project_copy_factory,
    read_json,
    write_json,
    rebind_calculation_result,
    rebind_scan_result,
    repo_root: Path,
    mutate_return: bool,
    mutate_observable: bool,
    expected_fragment: str,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    meta_path = (
        project_dir / "calculations" / "task-001" / "result-meta.json"
    )
    metadata = read_json(meta_path)
    if mutate_return:
        metadata["return_value"]["name"] = "DifferentObservable"
    if mutate_observable:
        metadata["observable"] = "DifferentObservable"
    write_json(meta_path, metadata)
    rebind_calculation_result(project_dir)
    rebind_scan_result(project_dir)

    completed = _workspace_validation(repo_root, project_dir)

    assert completed.returncode != 0
    assert expected_fragment in completed.stdout + completed.stderr
