from __future__ import annotations

import subprocess
import sys


def test_interpolation_out_of_range_fails_closed_without_persisting_partial_scan(
    tmp_path,
    project_copy_factory,
    read_json,
    write_json,
    run_scan_script,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    constraints_path = project_dir / "constraints" / "constraints-data.json"
    constraints_data = read_json(constraints_path)
    constraints_data["constraints"].append(
        {
            "id": "c-900",
            "name": "Synthetic interpolated mass ceiling",
            "type": "upper_limit",
            "observable": "M_Hpp",
            "limit_value": 170.0,
            "unit": "GeV",
            "implementation_status": "interpolated",
            "source": "pytest synthetic fixture",
            "notes": "Synthetic table-backed constraint.",
            "computed_by": {
                "type": "external",
                "note": "Values supplied by the local synthetic table."
            },
            "interpolation": {
                "file": "constraints/interp-limit.csv",
                "x_parameter": "M_Hpp",
                "x_column": "M_Hpp",
                "x_unit": "GeV",
                "y_quantity": "M_Hpp",
                "y_column": "limit",
                "y_unit": "GeV",
                "method": "linear",
                "valid_range": [100.0, 200.0],
                "extrapolation_policy": "forbidden",
            },
        }
    )
    write_json(constraints_path, constraints_data)
    (project_dir / "constraints" / "interp-limit.csv").write_text(
        "M_Hpp,limit\n100,150\n150,170\n200,190\n",
        encoding="utf-8",
    )

    (project_dir / "numerics" / "custom_observables.py").write_text(
        "from __future__ import annotations\n\n"
        "def dummy_obs(*, M_Hpp: float, **kwargs) -> float:\n"
        "    return float(M_Hpp)\n",
        encoding="utf-8",
    )

    manifest = read_json(project_dir / "manifest.json")
    write_json(
        project_dir / "numerics" / "scan-configs" / "analysis-103.json",
        {
            "analysis_id": "analysis-103",
            "model_name": "Minimal Type II Seesaw (scalar triplet extension)",
            "description": "Interpolation range test",
            "depends_on": {
                "model_version": manifest["active_model_version"],
                "model_checksum": manifest["artifacts"]["model"]["checksum"],
                "task_ids": [],
            },
            "scan_parameters": [
                {"canonical_name": "M_Hpp", "range": [50.0, 250.0], "grid": 5, "scale": "linear"}
            ],
            "fixed_parameters": [],
            "observables": [
                {"observable": "dummy_obs", "source": {"type": "custom", "function": "dummy_obs", "canonical_unit": "dimensionless"}}
            ],
            "constraints_used": ["c-900"],
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
            "analysis-103",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    combined_output = result.stdout + result.stderr
    assert "incomplete scientific evidence" in combined_output
    assert "2 / 5 points had failed/skipped evaluations" in combined_output
    assert "No scan outputs or manifest history were written" in combined_output

    result_dir = project_dir / "numerics" / "scan-results" / "analysis-103"
    assert not (result_dir / "scan.csv").exists()
    assert not (result_dir / "scan.meta.json").exists()
    assert not (
        project_dir / "numerics" / "analysis-summary-analysis-103.md"
    ).exists()
    assert read_json(project_dir / "manifest.json") == manifest
