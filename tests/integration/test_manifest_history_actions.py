from __future__ import annotations

import subprocess
import sys


def test_manifest_history_actions_distinguish_full_rerun_and_replot(
    tmp_path,
    project_copy_factory,
    ensure_task_result,
    read_json,
    write_json,
    run_scan_script,
    make_figures_script,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    ensure_task_result(
        project_dir,
        task_id="task-001",
        observable="Br_mu_to_egamma",
        function_name="compute_br_mu_to_egamma",
        python_body="""
from __future__ import annotations


def compute_br_mu_to_egamma(*, M_Hpp: float, v_Delta: float = 1.0e-3, **kwargs) -> float:
    safe_mass = max(float(M_Hpp), 1.0)
    return float(5.0e-13 * (100.0 / safe_mass) ** 2 * (1.0 + 100.0 * float(v_Delta)))
""".strip()
        + "\n",
        parameter_specs=[
            {"canonical_name": "M_Hpp", "role": "scan", "unit": "GeV"},
            {"canonical_name": "v_Delta", "role": "fixed", "unit": "GeV"},
        ],
    )

    manifest = read_json(project_dir / "manifest.json")
    write_json(
        project_dir / "numerics" / "scan-configs" / "analysis-201.json",
        {
            "analysis_id": "analysis-201",
            "model_name": "Minimal Type II Seesaw (scalar triplet extension)",
            "description": "Manifest history action test",
            "depends_on": {
                "model_version": manifest["active_model_version"],
                "model_checksum": manifest["artifacts"]["model"]["checksum"],
                "task_ids": ["task-001"],
            },
            "scan_parameters": [
                {"canonical_name": "M_Hpp", "range": [100.0, 400.0], "grid": 4, "scale": "linear"}
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
        },
    )

    run_result = subprocess.run(
        [
            sys.executable,
            str(run_scan_script),
            "--project-dir",
            str(project_dir),
            "--analysis-id",
            "analysis-201",
        ],
        capture_output=True,
        text=True,
    )
    assert run_result.returncode == 0, run_result.stdout + run_result.stderr
    assert "history-action: numerics_analysis_complete" in run_result.stdout
    manifest = read_json(project_dir / "manifest.json")
    history_length_after_run = len(manifest["history"])
    assert manifest["history"][-1]["action"] == "numerics_analysis_complete"

    figure_result = subprocess.run(
        [
            sys.executable,
            str(make_figures_script),
            "--project-dir",
            str(project_dir),
            "--analysis-id",
            "analysis-201",
        ],
        capture_output=True,
        text=True,
    )
    assert figure_result.returncode == 0, figure_result.stdout + figure_result.stderr
    assert "history action: none (manifest already updated by run_scan)" in figure_result.stdout

    manifest = read_json(project_dir / "manifest.json")
    assert manifest["history"][-1]["action"] == "numerics_analysis_complete"
    assert len(manifest["history"]) == history_length_after_run

    rerun_result = subprocess.run(
        [
            sys.executable,
            str(run_scan_script),
            "--project-dir",
            str(project_dir),
            "--analysis-id",
            "analysis-201",
        ],
        capture_output=True,
        text=True,
    )
    assert rerun_result.returncode == 0, rerun_result.stdout + rerun_result.stderr
    assert "history-action: numerics_analysis_rerun" in rerun_result.stdout
    manifest = read_json(project_dir / "manifest.json")
    history_length_after_rerun = len(manifest["history"])
    assert manifest["history"][-1]["action"] == "numerics_analysis_rerun"

    rerun_figures = subprocess.run(
        [
            sys.executable,
            str(make_figures_script),
            "--project-dir",
            str(project_dir),
            "--analysis-id",
            "analysis-201",
        ],
        capture_output=True,
        text=True,
    )
    assert rerun_figures.returncode == 0, rerun_figures.stdout + rerun_figures.stderr
    assert "history action: none (manifest already updated by run_scan)" in rerun_figures.stdout

    manifest = read_json(project_dir / "manifest.json")
    assert manifest["history"][-1]["action"] == "numerics_analysis_rerun"
    assert len(manifest["history"]) == history_length_after_rerun

    replot_result = subprocess.run(
        [
            sys.executable,
            str(make_figures_script),
            "--project-dir",
            str(project_dir),
            "--analysis-id",
            "analysis-201",
            "--overwrite",
        ],
        capture_output=True,
        text=True,
    )
    assert replot_result.returncode == 0, replot_result.stdout + replot_result.stderr
    assert "history action: numerics_figures_regenerated" in replot_result.stdout

    manifest = read_json(project_dir / "manifest.json")
    assert manifest["history"][-1]["action"] == "numerics_figures_regenerated"
