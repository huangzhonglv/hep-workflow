from __future__ import annotations

import subprocess
import sys


def test_stale_translation_status_aborts(
    tmp_path,
    project_copy_factory,
    ensure_task_result,
    read_json,
    write_json,
    run_scan_script,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    ensure_task_result(
        project_dir,
        task_id="task-001",
        observable="Br_mu_to_egamma",
        function_name="compute_br_mu_to_egamma",
        translation_status="partial",
        parameter_specs=[
            {"canonical_name": "M_Hpp", "role": "scan", "unit": "GeV"},
            {"canonical_name": "v_Delta", "role": "scan", "unit": "GeV"},
        ],
    )

    manifest = read_json(project_dir / "manifest.json")
    write_json(
        project_dir / "numerics" / "scan-configs" / "analysis-102.json",
        {
            "analysis_id": "analysis-102",
            "model_name": "Minimal Type II Seesaw (scalar triplet extension)",
            "description": "Translation-status failure fixture",
            "depends_on": {
                "model_version": manifest["active_model_version"],
                "model_checksum": manifest["artifacts"]["model"]["checksum"],
                "task_ids": ["task-001"],
            },
            "scan_parameters": [
                {"canonical_name": "M_Hpp", "range": [100.0, 200.0], "grid": 3, "scale": "linear"}
            ],
            "fixed_parameters": [
                {"canonical_name": "v_Delta", "value": 1.0e-3}
            ],
            "observables": [
                {"observable": "Br_mu_to_egamma", "source": {"type": "task", "task_id": "task-001"}}
            ],
            "constraints_used": ["c-001"],
            "figures": [],
            "seed": 0,
            "parallelism": 1,
        },
    )

    result = subprocess.run(
        [
            sys.executable,
            str(run_scan_script),
            "--project-dir",
            str(project_dir),
            "--analysis-id",
            "analysis-102",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    combined_output = result.stdout + result.stderr
    assert "task-001" in combined_output
    assert "translation_status" in combined_output
    assert "partial" in combined_output
