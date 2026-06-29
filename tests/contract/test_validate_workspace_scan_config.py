from __future__ import annotations

import subprocess
import sys


def test_validate_workspace_scan_config(
    tmp_path,
    project_copy_factory,
    ensure_task_result,
    read_json,
    write_json,
    repo_root,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    ensure_task_result(
        project_dir,
        task_id="task-001",
        observable="Br_mu_to_egamma",
        function_name="compute_br_mu_to_egamma",
        parameter_specs=[
            {"canonical_name": "M_Hpp", "role": "scan", "unit": "GeV"},
            {"canonical_name": "v_Delta", "role": "fixed", "unit": "GeV"},
        ],
    )

    manifest = read_json(project_dir / "manifest.json")
    valid_scan_config = {
        "analysis_id": "analysis-901",
            "model_name": "Toy Numerics Contract Model",
        "description": "Contract-test valid scan config.",
        "depends_on": {
            "model_version": manifest["active_model_version"],
            "model_checksum": manifest["artifacts"]["model"]["checksum"],
            "task_ids": ["task-001"],
        },
        "scan_parameters": [
            {"canonical_name": "M_Hpp", "range": [100.0, 500.0], "grid": 5, "scale": "linear"}
        ],
        "fixed_parameters": [
            {"canonical_name": "v_Delta", "value": 1.0e-3}
        ],
        "observables": [
            {"observable": "Br_mu_to_egamma", "source": {"type": "task", "task_id": "task-001"}}
        ],
        "constraints_used": ["c-001"],
        "figures": [
            {
                "kind": "scan_1d",
                "x": "M_Hpp",
                "observables": ["Br_mu_to_egamma"],
                "overlay_constraint_bands": True,
            }
        ],
        "allow_formula_fallback": True,
        "seed": 0,
        "parallelism": 1,
    }
    scan_config_path = project_dir / "numerics" / "scan-configs" / "analysis-901.json"
    write_json(scan_config_path, valid_scan_config)

    workspace_root = project_dir.parent
    valid_result = subprocess.run(
        [
            sys.executable,
            "scripts/validate_workspace_projects.py",
            "--workspace-root",
            str(workspace_root),
            project_dir.name,
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    assert valid_result.returncode == 0, valid_result.stdout + valid_result.stderr
    assert "analysis-901.json <- validate_scan_config.py" in valid_result.stdout
    assert "formula fallback" in valid_result.stdout

    without_allow_scan_config = dict(valid_scan_config)
    without_allow_scan_config.pop("allow_formula_fallback")
    write_json(scan_config_path, without_allow_scan_config)

    fallback_gate_result = subprocess.run(
        [
            sys.executable,
            "scripts/validate_workspace_projects.py",
            "--workspace-root",
            str(workspace_root),
            project_dir.name,
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    assert fallback_gate_result.returncode != 0
    combined_fallback_output = fallback_gate_result.stdout + fallback_gate_result.stderr
    assert "allow_formula_fallback" in combined_fallback_output

    broken_scan_config = dict(valid_scan_config)
    broken_scan_config["scan_parameters"] = [
        {"canonical_name": "BAD_PARAM", "range": [100.0, 500.0], "grid": 5, "scale": "linear"}
    ]
    write_json(scan_config_path, broken_scan_config)

    invalid_result = subprocess.run(
        [
            sys.executable,
            "scripts/validate_workspace_projects.py",
            "--workspace-root",
            str(workspace_root),
            project_dir.name,
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    assert invalid_result.returncode != 0
    combined_output = invalid_result.stdout + invalid_result.stderr
    assert "BAD_PARAM" in combined_output
