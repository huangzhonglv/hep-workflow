from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path


def _add_constraint(project_dir: Path, read_json, write_json, *, constraint_id: str) -> None:
    path = project_dir / "constraints" / "constraints-data.json"
    payload = read_json(path)
    payload["constraints"].append(
        {
            "id": constraint_id,
            "name": "Phase 3 synthetic observable ceiling",
            "type": "upper_limit",
            "observable": "phase3_observable",
            "limit_value": 10.0,
            "unit": "dimensionless",
            "source": "pytest synthetic fixture",
            "implementation_status": "direct",
            "notes": "Exercises explicit custom execution contracts.",
        }
    )
    write_json(path, payload)


def _scan_config(
    project_dir: Path,
    read_json,
    *,
    analysis_id: str,
    constraint_id: str,
    seed: int,
    task_ids: list[str] | None = None,
) -> dict:
    manifest = read_json(project_dir / "manifest.json")
    source: dict = {
        "type": "custom",
        "function": "phase3_observable",
        "canonical_unit": "dimensionless",
    }
    if task_ids:
        source["task_ids"] = task_ids
    return {
        "analysis_id": analysis_id,
        "model_name": "Toy Numerics Contract Model",
        "description": "Phase 3 custom/RNG contract test.",
        "depends_on": {
            "model_version": manifest["active_model_version"],
            "model_checksum": manifest["artifacts"]["model"]["checksum"],
            "task_ids": task_ids or [],
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
        "observables": [
            {"observable": "phase3_observable", "source": source}
        ],
        "constraints_used": [constraint_id],
        "figures": [],
        "allow_formula_fallback": bool(task_ids),
        "seed": seed,
        "parallelism": 1,
    }


def _run_scan(run_scan_script: Path, project_dir: Path, analysis_id: str):
    return subprocess.run(
        [
            sys.executable,
            str(run_scan_script),
            "--project-dir",
            str(project_dir),
            "--analysis-id",
            analysis_id,
        ],
        capture_output=True,
        text=True,
    )


def _observable_values(project_dir: Path, analysis_id: str) -> list[float]:
    path = (
        project_dir
        / "numerics"
        / "scan-results"
        / analysis_id
        / "scan.csv"
    )
    with path.open("r", encoding="utf-8", newline="") as handle:
        return [float(row["phase3_observable"]) for row in csv.DictReader(handle)]


def test_explicit_local_rng_is_seeded_and_smoke_order_independent(
    tmp_path,
    project_copy_factory,
    read_json,
    write_json,
    run_scan_script,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    _add_constraint(project_dir, read_json, write_json, constraint_id="c-930")
    (project_dir / "numerics" / "custom_observables.py").write_text(
        "from __future__ import annotations\n\n"
        "def phase3_observable(*, rng, M_Hpp: float) -> float:\n"
        "    return float(rng.random())\n",
        encoding="utf-8",
    )

    for analysis_id, seed in (
        ("analysis-930", 12345),
        ("analysis-931", 12345),
        ("analysis-932", 54321),
    ):
        write_json(
            project_dir / "numerics" / "scan-configs" / f"{analysis_id}.json",
            _scan_config(
                project_dir,
                read_json,
                analysis_id=analysis_id,
                constraint_id="c-930",
                seed=seed,
            ),
        )
        completed = _run_scan(run_scan_script, project_dir, analysis_id)
        assert completed.returncode == 0, completed.stdout + completed.stderr

    first = _observable_values(project_dir, "analysis-930")
    repeated = _observable_values(project_dir, "analysis-931")
    different = _observable_values(project_dir, "analysis-932")
    assert first == repeated
    assert first != different

    meta = read_json(
        project_dir
        / "numerics"
        / "scan-results"
        / "analysis-930"
        / "scan.meta.json"
    )
    assert meta["rng"] == {
        "algorithm": "numpy.random.PCG64",
        "algorithm_version": "pcg64-v1",
        "substream_scheme": "numpy-seedsequence-v1",
        "seed": 12345,
        "substreams": {"smoke": 0, "scan": 1},
        "consumers": ["phase3_observable"],
    }


def test_declared_task_outputs_are_delivered_and_validator_matches_runtime(
    tmp_path,
    project_copy_factory,
    ensure_task_result,
    read_json,
    write_json,
    run_scan_script,
    repo_root,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    ensure_task_result(
        project_dir,
        task_id="task-001",
        observable="Br_mu_to_egamma",
        function_name="compute_br_mu_to_egamma",
        python_body=(
            "from __future__ import annotations\n\n"
            "def compute_br_mu_to_egamma(*, M_Hpp: float, v_Delta: float) -> float:\n"
            "    return float(M_Hpp * v_Delta)\n"
        ),
        parameter_specs=[
            {"canonical_name": "M_Hpp", "role": "scan", "unit": "GeV"},
            {"canonical_name": "v_Delta", "role": "scan", "unit": "GeV"},
        ],
    )
    _add_constraint(project_dir, read_json, write_json, constraint_id="c-931")
    custom_path = project_dir / "numerics" / "custom_observables.py"
    custom_path.write_text(
        "from __future__ import annotations\n\n"
        "def phase3_observable(*, task_outputs, M_Hpp: float, v_Delta: float) -> float:\n"
        "    return float(task_outputs['task-001'](M_Hpp=M_Hpp, v_Delta=v_Delta) / M_Hpp)\n",
        encoding="utf-8",
    )
    valid_id = "analysis-933"
    write_json(
        project_dir / "numerics" / "scan-configs" / f"{valid_id}.json",
        _scan_config(
            project_dir,
            read_json,
            analysis_id=valid_id,
            constraint_id="c-931",
            seed=7,
            task_ids=["task-001"],
        ),
    )

    completed = _run_scan(run_scan_script, project_dir, valid_id)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert _observable_values(project_dir, valid_id) == [0.001, 0.001, 0.001]

    invalid_id = "analysis-934"
    invalid = _scan_config(
        project_dir,
        read_json,
        analysis_id=invalid_id,
        constraint_id="c-931",
        seed=7,
        task_ids=["task-001"],
    )
    invalid["observables"][0]["source"].pop("task_ids")
    write_json(
        project_dir / "numerics" / "scan-configs" / f"{invalid_id}.json",
        invalid,
    )
    runtime = _run_scan(run_scan_script, project_dir, invalid_id)
    validator = subprocess.run(
        [
            sys.executable,
            str(
                repo_root
                / ".agents"
                / "skills"
                / "hep-numerics"
                / "scripts"
                / "validate_scan_config.py"
            ),
            "--project-dir",
            str(project_dir),
            "--analysis-id",
            invalid_id,
        ],
        capture_output=True,
        text=True,
    )
    assert runtime.returncode != 0
    assert validator.returncode != 0
    for output in (
        runtime.stdout + runtime.stderr,
        validator.stdout + validator.stderr,
    ):
        assert "NUM-PREFLIGHT-007" in output
        assert "declares task_outputs but source.task_ids is absent" in output


def test_ambient_rng_fails_preflight_before_outputs(
    tmp_path,
    project_copy_factory,
    read_json,
    write_json,
    run_scan_script,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    _add_constraint(project_dir, read_json, write_json, constraint_id="c-932")
    (project_dir / "numerics" / "custom_observables.py").write_text(
        "import numpy as np\n\n"
        "def phase3_observable(*, M_Hpp: float) -> float:\n"
        "    return float(np.random.random())\n",
        encoding="utf-8",
    )
    analysis_id = "analysis-935"
    write_json(
        project_dir / "numerics" / "scan-configs" / f"{analysis_id}.json",
        _scan_config(
            project_dir,
            read_json,
            analysis_id=analysis_id,
            constraint_id="c-932",
            seed=1,
        ),
    )

    completed = _run_scan(run_scan_script, project_dir, analysis_id)

    assert completed.returncode != 0
    assert "ambient NumPy RNG" in completed.stdout + completed.stderr
    assert not (
        project_dir / "numerics" / "scan-results" / analysis_id
    ).exists()
