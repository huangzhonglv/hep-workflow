from __future__ import annotations

import subprocess
import sys


def test_custom_observable_not_implemented_fails_preflight(
    tmp_path,
    project_copy_factory,
    read_json,
    write_json,
    run_scan_script,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    constraints_path = project_dir / "constraints" / "constraints-data.json"
    constraints = read_json(constraints_path)
    constraints["constraints"].append(
        {
            "id": "c-904",
            "name": "Broken observable readiness test",
            "type": "upper_limit",
            "observable": "broken_obs",
            "limit_value": 1.0,
            "unit": "dimensionless",
            "source": "pytest synthetic fixture",
            "implementation_status": "direct",
            "notes": "Forces custom smoke validation before any scan output.",
        }
    )
    write_json(constraints_path, constraints)
    (project_dir / "numerics" / "custom_observables.py").write_text(
        "from __future__ import annotations\n\n"
        "def broken_obs(*, M_Hpp: float, **kwargs) -> float:\n"
        "    raise NotImplementedError('broken_obs is not ready')\n",
        encoding="utf-8",
    )

    manifest = read_json(project_dir / "manifest.json")
    write_json(
        project_dir / "numerics" / "scan-configs" / "analysis-104.json",
        {
            "analysis_id": "analysis-104",
            "model_name": "Minimal Type II Seesaw (scalar triplet extension)",
            "description": "Custom observable preflight failure",
            "depends_on": {
                "model_version": manifest["active_model_version"],
                "model_checksum": manifest["artifacts"]["model"]["checksum"],
                "task_ids": [],
            },
            "scan_parameters": [
                {"canonical_name": "M_Hpp", "range": [100.0, 200.0], "grid": 3, "scale": "linear"}
            ],
            "fixed_parameters": [],
            "observables": [
                {"observable": "broken_obs", "source": {"type": "custom", "function": "broken_obs", "canonical_unit": "dimensionless"}}
            ],
            "constraints_used": ["c-904"],
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
            "analysis-104",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    combined_output = result.stdout + result.stderr
    assert "custom observable readiness" in combined_output
    assert "broken_obs" in combined_output
    assert "not implemented" in combined_output
