from __future__ import annotations

import json
import subprocess
import sys

import pytest


def run_scan(repo_root, run_scan_script, project_dir):
    return subprocess.run(
        [
            sys.executable,
            str(run_scan_script),
            "--project-dir",
            str(project_dir),
            "--analysis-id",
            "analysis-001",
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )


def test_non_finite_grid_evidence_cannot_overwrite_completed_scan(
    repo_root,
    run_scan_script,
    project_copy_factory,
    rebind_calculation_result,
    tmp_path,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    scan_path = project_dir / "numerics" / "scan-results" / "analysis-001" / "scan.csv"
    meta_path = scan_path.with_name("scan.meta.json")
    manifest_path = project_dir / "manifest.json"
    before = {
        scan_path: scan_path.read_bytes(),
        meta_path: meta_path.read_bytes(),
        manifest_path: manifest_path.read_bytes(),
    }
    backend = project_dir / "calculations" / "task-001" / "result-python.py"
    backend.write_text(
        "def compute_br_mu_to_egamma(*, M_Hpp, v_Delta):\n"
        "    return float('nan')\n",
        encoding="utf-8",
    )
    rebind_calculation_result(project_dir)

    completed = run_scan(repo_root, run_scan_script, project_dir)

    assert completed.returncode == 1
    assert "incomplete scientific evidence" in completed.stderr
    for path, content in before.items():
        assert path.read_bytes() == content


def test_duplicate_scan_config_keys_are_rejected_before_outputs(
    repo_root,
    run_scan_script,
    project_copy_factory,
    tmp_path,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    scan_path = project_dir / "numerics" / "scan-results" / "analysis-001" / "scan.csv"
    manifest_path = project_dir / "manifest.json"
    scan_before = scan_path.read_bytes()
    manifest_before = manifest_path.read_bytes()
    config_path = project_dir / "numerics" / "scan-configs" / "analysis-001.json"
    config_text = config_path.read_text(encoding="utf-8")
    assert '"seed": 0' in config_text
    config_path.write_text(
        config_text.replace('"seed": 0', '"seed": 0,\n  "seed": 1', 1),
        encoding="utf-8",
    )

    completed = run_scan(repo_root, run_scan_script, project_dir)

    assert completed.returncode == 1
    assert "duplicate object key: 'seed'" in completed.stdout + completed.stderr
    assert scan_path.read_bytes() == scan_before
    assert manifest_path.read_bytes() == manifest_before


def test_smoke_can_pass_but_non_finite_grid_still_aborts_without_publication(
    repo_root,
    run_scan_script,
    project_copy_factory,
    rebind_calculation_result,
    tmp_path,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    scan_path = project_dir / "numerics" / "scan-results" / "analysis-001" / "scan.csv"
    meta_path = scan_path.with_name("scan.meta.json")
    summary_path = project_dir / "numerics" / "analysis-summary-analysis-001.md"
    manifest_path = project_dir / "manifest.json"
    before = {
        path: path.read_bytes()
        for path in (scan_path, meta_path, summary_path, manifest_path)
    }
    backend = project_dir / "calculations" / "task-001" / "result-python.py"
    backend.write_text(
        "def compute_br_mu_to_egamma(*, M_Hpp, v_Delta):\n"
        "    return 1e-13 if float(M_Hpp) == 150.0 else float('nan')\n",
        encoding="utf-8",
    )
    rebind_calculation_result(project_dir)

    completed = run_scan(repo_root, run_scan_script, project_dir)

    assert completed.returncode == 1
    assert "incomplete scientific evidence" in completed.stderr
    for path, content in before.items():
        assert path.read_bytes() == content


@pytest.mark.parametrize("expression", ["True", "[1.0]", "float('inf')", "-float('inf')"])
def test_malformed_scalar_observable_fails_before_outputs(
    repo_root,
    run_scan_script,
    project_copy_factory,
    rebind_calculation_result,
    tmp_path,
    expression,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    scan_path = project_dir / "numerics" / "scan-results" / "analysis-001" / "scan.csv"
    manifest_path = project_dir / "manifest.json"
    before = {scan_path: scan_path.read_bytes(), manifest_path: manifest_path.read_bytes()}
    backend = project_dir / "calculations" / "task-001" / "result-python.py"
    backend.write_text(
        "def compute_br_mu_to_egamma(*, M_Hpp, v_Delta):\n"
        f"    return {expression}\n",
        encoding="utf-8",
    )
    rebind_calculation_result(project_dir)

    completed = run_scan(repo_root, run_scan_script, project_dir)

    assert completed.returncode == 1
    for path, content in before.items():
        assert path.read_bytes() == content


def test_zero_constraints_cannot_publish_an_allowed_scan(
    repo_root,
    run_scan_script,
    project_copy_factory,
    tmp_path,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    config_path = project_dir / "numerics" / "scan-configs" / "analysis-001.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["constraints_used"] = []
    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    scan_path = project_dir / "numerics" / "scan-results" / "analysis-001" / "scan.csv"
    manifest_path = project_dir / "manifest.json"
    before = {scan_path: scan_path.read_bytes(), manifest_path: manifest_path.read_bytes()}

    completed = run_scan(repo_root, run_scan_script, project_dir)

    assert completed.returncode == 1
    assert "constraints_used" in completed.stdout + completed.stderr
    for path, content in before.items():
        assert path.read_bytes() == content


@pytest.mark.parametrize(
    "duplicate_kind",
    ["scan_parameter", "fixed_parameter", "observable", "constraint"],
)
def test_duplicate_canonical_entries_cannot_publish_scan(
    repo_root,
    run_scan_script,
    project_copy_factory,
    tmp_path,
    duplicate_kind,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    config_path = project_dir / "numerics" / "scan-configs" / "analysis-001.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if duplicate_kind == "scan_parameter":
        duplicate = dict(config["scan_parameters"][0])
        duplicate["range"] = [200.0, 300.0]
        config["scan_parameters"].append(duplicate)
    elif duplicate_kind == "fixed_parameter":
        duplicate = dict(config["fixed_parameters"][0])
        duplicate["value"] = float(duplicate["value"]) + 1.0
        config["fixed_parameters"].append(duplicate)
    elif duplicate_kind == "observable":
        duplicate = json.loads(json.dumps(config["observables"][0]))
        duplicate["source"] = {
            "type": "custom",
            "function": "duplicate_observable",
            "canonical_unit": "dimensionless",
        }
        config["observables"].append(duplicate)
    else:
        config["constraints_used"].append(config["constraints_used"][0])
    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    scan_path = project_dir / "numerics" / "scan-results" / "analysis-001" / "scan.csv"
    manifest_path = project_dir / "manifest.json"
    before = {scan_path: scan_path.read_bytes(), manifest_path: manifest_path.read_bytes()}

    completed = run_scan(repo_root, run_scan_script, project_dir)

    assert completed.returncode == 1
    assert any(
        token in completed.stdout + completed.stderr
        for token in ("duplicate", "non-unique")
    )
    for path, content in before.items():
        assert path.read_bytes() == content


@pytest.mark.parametrize(
    "mutation",
    ["task_id", "parameter_unit", "duplicate_parameter", "return_name"],
)
def test_result_meta_identity_and_unit_drift_cannot_publish_scan(
    repo_root,
    run_scan_script,
    project_copy_factory,
    tmp_path,
    mutation,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    meta_path = project_dir / "calculations" / "task-001" / "result-meta.json"
    metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    if mutation == "task_id":
        metadata["task_id"] = "task-002"
    elif mutation == "parameter_unit":
        metadata["parameters"][0]["unit"] = "MeV"
    elif mutation == "duplicate_parameter":
        duplicate = dict(metadata["parameters"][0])
        duplicate["role"] = "fixed"
        metadata["parameters"].append(duplicate)
    else:
        metadata["return_value"]["name"] = "wrong_observable"
    meta_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")

    scan_path = project_dir / "numerics" / "scan-results" / "analysis-001" / "scan.csv"
    meta_output = scan_path.with_name("scan.meta.json")
    summary_path = project_dir / "numerics" / "analysis-summary-analysis-001.md"
    manifest_path = project_dir / "manifest.json"
    before = {
        path: path.read_bytes()
        for path in (scan_path, meta_output, summary_path, manifest_path)
    }

    completed = run_scan(repo_root, run_scan_script, project_dir)

    assert completed.returncode == 1
    for path, content in before.items():
        assert path.read_bytes() == content
