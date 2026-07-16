from __future__ import annotations

import subprocess
import sys


def test_validate_scan_config_rejects_latex_parameter_name(
    tmp_path,
    project_copy_factory,
    read_json,
    write_json,
    repo_root,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    manifest = read_json(project_dir / "manifest.json")
    scan_config = {
        "analysis_id": "analysis-902",
        "model_name": "Minimal Type II Seesaw (scalar triplet extension)",
        "description": "Invalid LaTeX-name scan config.",
        "depends_on": {
            "model_version": manifest["active_model_version"],
            "model_checksum": manifest["artifacts"]["model"]["checksum"],
            "task_ids": [],
        },
        "scan_parameters": [
            {
                "canonical_name": "M_{Hpp}",
                "range": [100.0, 500.0],
                "grid": 5,
                "scale": "linear",
            }
        ],
        "fixed_parameters": [],
        "observables": [
            {"observable": "Br_mu_to_egamma", "source": {"type": "custom", "function": "dummy", "canonical_unit": "dimensionless"}}
        ],
        "constraints_used": [],
        "figures": [
            {
                "kind": "scan_1d",
                "x": "M_{Hpp}",
                "observables": ["Br_mu_to_egamma"],
            }
        ],
    }
    scan_config_path = project_dir / "numerics" / "scan-configs" / "analysis-902.json"
    write_json(scan_config_path, scan_config)

    result = subprocess.run(
        [
            sys.executable,
            ".agents/skills/hep-numerics/scripts/validate_scan_config.py",
            "--project-dir",
            str(project_dir),
            "--analysis-id",
            "analysis-902",
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    combined_output = result.stdout + result.stderr
    assert "M_{Hpp}" in combined_output
    assert "canonical name" in combined_output
