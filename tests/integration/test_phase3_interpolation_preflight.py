from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


def _prepare_case(
    project_dir: Path,
    read_json,
    write_json,
    *,
    analysis_id: str,
    table: str,
    x_unit: str = "GeV",
    y_unit: str = "GeV",
) -> None:
    constraints_path = project_dir / "constraints" / "constraints-data.json"
    constraints = read_json(constraints_path)
    constraints["constraints"].append(
        {
            "id": "c-940",
            "name": "Phase 3 interpolation ceiling",
            "type": "upper_limit",
            "observable": "M_Hpp",
            "limit_value": 200.0,
            "unit": "GeV",
            "source": "pytest synthetic fixture",
            "implementation_status": "interpolated",
            "notes": "Exercises interpolation trust-boundary validation.",
            "computed_by": {
                "type": "external",
                "note": "Values come from the local synthetic table.",
            },
            "interpolation": {
                "file": "constraints/phase3-interpolation.csv",
                "x_parameter": "M_Hpp",
                "x_column": "mass",
                "x_unit": x_unit,
                "y_quantity": "M_Hpp",
                "y_column": "limit",
                "y_unit": y_unit,
                "method": "linear",
                "valid_range": [100.0, 200.0],
                "extrapolation_policy": "forbidden",
            },
        }
    )
    write_json(constraints_path, constraints)
    (project_dir / "constraints" / "phase3-interpolation.csv").write_text(
        table,
        encoding="utf-8",
    )

    manifest = read_json(project_dir / "manifest.json")
    write_json(
        project_dir / "numerics" / "scan-configs" / f"{analysis_id}.json",
        {
            "analysis_id": analysis_id,
            "model_name": "Toy Numerics Contract Model",
            "description": "Phase 3 interpolation preflight test.",
            "depends_on": {
                "model_version": manifest["active_model_version"],
                "model_checksum": manifest["artifacts"]["model"]["checksum"],
                "task_ids": [],
            },
            "scan_parameters": [
                {
                    "canonical_name": "M_Hpp",
                    "range": [100.0, 200.0],
                    "grid": 3,
                    "scale": "linear",
                }
            ],
            "fixed_parameters": [
                {"canonical_name": "v_Delta", "value": 1.0e-3},
                {"canonical_name": "m_lightest", "value": 0.01},
            ],
            "observables": [],
            "constraints_used": ["c-940"],
            "figures": [],
            "allow_formula_fallback": False,
            "seed": 17,
            "parallelism": 1,
        },
    )


def _run(script: Path, project_dir: Path, analysis_id: str):
    return subprocess.run(
        [
            sys.executable,
            str(script),
            "--project-dir",
            str(project_dir),
            "--analysis-id",
            analysis_id,
        ],
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize(
    ("table", "x_unit", "y_unit", "message"),
    [
        (
            "mass,limit\n100,150\n150,170\n200,190\n",
            "MeV",
            "GeV",
            "does not match model-spec unit",
        ),
        (
            "mass,limit\n120,150\n150,170\n180,190\n",
            "GeV",
            "GeV",
            "do not cover declared valid_range",
        ),
        (
            "mass,limit\n100,150\n100,170\n200,190\n",
            "GeV",
            "GeV",
            "x nodes must be unique",
        ),
        (
            "mass,limit\n100,150\n150,170\n200,190\n",
            "GeV",
            "MeV",
            "does not match constraint unit",
        ),
    ],
)
def test_advertised_validator_and_runtime_share_interpolation_failures(
    tmp_path,
    project_copy_factory,
    read_json,
    write_json,
    run_scan_script,
    repo_root,
    table,
    x_unit,
    y_unit,
    message,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    analysis_id = "analysis-940"
    _prepare_case(
        project_dir,
        read_json,
        write_json,
        analysis_id=analysis_id,
        table=table,
        x_unit=x_unit,
        y_unit=y_unit,
    )
    validator_script = (
        repo_root
        / ".agents"
        / "skills"
        / "hep-numerics"
        / "scripts"
        / "validate_scan_config.py"
    )

    for completed in (
        _run(validator_script, project_dir, analysis_id),
        _run(run_scan_script, project_dir, analysis_id),
    ):
        output = completed.stdout + completed.stderr
        assert completed.returncode == 1
        assert "NUM-PREFLIGHT-008" in output
        assert message in output
    assert not (
        project_dir / "numerics" / "scan-results" / analysis_id
    ).exists()


def test_valid_interpolation_contract_executes_without_custom_observables(
    tmp_path,
    project_copy_factory,
    read_json,
    write_json,
    run_scan_script,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    analysis_id = "analysis-941"
    _prepare_case(
        project_dir,
        read_json,
        write_json,
        analysis_id=analysis_id,
        table="mass,limit\n100,150\n150,170\n200,190\n",
    )

    completed = _run(run_scan_script, project_dir, analysis_id)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert (
        project_dir
        / "numerics"
        / "scan-results"
        / analysis_id
        / "scan.meta.json"
    ).is_file()
