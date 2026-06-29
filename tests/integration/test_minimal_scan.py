from __future__ import annotations

import csv
import subprocess
import sys


def test_formula_fallback_requires_explicit_opt_in(
    tmp_path,
    project_copy_factory,
    ensure_task_result,
    read_json,
    write_json,
    run_scan_module,
    run_scan_script,
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
    scan_config = {
        "analysis_id": "analysis-100",
        "model_name": "Minimal Type II Seesaw (scalar triplet extension)",
        "description": "Fallback gate test",
        "depends_on": {
            "model_version": manifest["active_model_version"],
            "model_checksum": manifest["artifacts"]["model"]["checksum"],
            "task_ids": ["task-001"],
        },
        "scan_parameters": [
            {"canonical_name": "M_Hpp", "range": [100.0, 200.0], "grid": 2, "scale": "linear"}
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
    }
    write_json(project_dir / "numerics" / "scan-configs" / "analysis-100.json", scan_config)

    inputs = run_scan_module.load_inputs(project_dir=project_dir, analysis_id="analysis-100")
    validation = run_scan_module.validate(inputs)
    assert validation["report"].has_errors
    report_text = "\n".join(
        detail
        for check in validation["report"].checks
        for detail in check.details
    )
    assert "allow_formula_fallback" in report_text

    result = subprocess.run(
        [
            sys.executable,
            str(run_scan_script),
            "--project-dir",
            str(project_dir),
            "--analysis-id",
            "analysis-100",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    combined_output = result.stdout + result.stderr
    assert "allow_formula_fallback" in combined_output
    assert not (project_dir / "numerics" / "scan-results" / "analysis-100" / "scan.csv").exists()


def test_minimal_scan_runs_and_generates_figures(
    tmp_path,
    project_copy_factory,
    ensure_task_result,
    read_json,
    write_json,
    run_scan_module,
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
    scan_config = {
        "analysis_id": "analysis-101",
        "model_name": "Minimal Type II Seesaw (scalar triplet extension)",
        "description": "Integration test minimal scan",
        "depends_on": {
            "model_version": manifest["active_model_version"],
            "model_checksum": manifest["artifacts"]["model"]["checksum"],
            "task_ids": ["task-001"],
        },
        "scan_parameters": [
            {"canonical_name": "M_Hpp", "range": [100.0, 500.0], "grid": 2, "scale": "linear"},
            {"canonical_name": "v_Delta", "range": [1.0e-4, 1.0e-3], "grid": 2, "scale": "log"},
        ],
        "fixed_parameters": [],
        "observables": [
            {"observable": "Br_mu_to_egamma", "source": {"type": "task", "task_id": "task-001"}}
        ],
        "constraints_used": ["c-001"],
        "figures": [
            {
                "kind": "exclusion_2d",
                "x": "M_Hpp",
                "y": "v_Delta",
                "constraints": ["c-001"],
                "show_allowed_region": True,
            },
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
    scan_config_path = project_dir / "numerics" / "scan-configs" / "analysis-101.json"
    write_json(scan_config_path, scan_config)

    inputs = run_scan_module.load_inputs(project_dir=project_dir, analysis_id="analysis-101")
    validation = run_scan_module.validate(inputs)
    assert not validation["report"].has_errors
    runtime = run_scan_module.prepare_runtime(inputs, validation["runtime"])
    one_point = run_scan_module.evaluate_point(
        {"M_Hpp": 100.0, "v_Delta": 1.0e-3},
        inputs,
        runtime,
    )
    assert one_point["row"]["Br_mu_to_egamma"] is not None
    assert one_point["row"]["c-001_verdict"] in {"allowed", "excluded"}

    run_result = subprocess.run(
        [
            sys.executable,
            str(run_scan_script),
            "--project-dir",
            str(project_dir),
            "--analysis-id",
            "analysis-101",
        ],
        capture_output=True,
        text=True,
    )
    assert run_result.returncode == 0, run_result.stdout + run_result.stderr

    summary_path = project_dir / "numerics" / "analysis-summary-analysis-101.md"
    assert summary_path.exists()
    summary_text = summary_path.read_text(encoding="utf-8")
    assert "## Key findings" in summary_text
    assert "## Reproducibility" in summary_text
    assert "Integration test minimal scan" in summary_text
    assert "Br_mu_to_egamma" in summary_text
    assert "## Formula fallback provenance" in summary_text
    manifest = read_json(project_dir / "manifest.json")
    assert manifest["history"][-1]["action"] == "numerics_analysis_complete"
    assert manifest["artifacts"]["numerics"]["status"] in {"partial", "done"}
    assert "numerics/scan-results/analysis-101/scan.csv" in manifest["artifacts"]["numerics"]["files"]
    assert "numerics/scan-results/analysis-101/scan.meta.json" in manifest["artifacts"]["numerics"]["files"]
    assert "numerics/analysis-summary-analysis-101.md" in manifest["artifacts"]["numerics"]["files"]
    assert not any("/figures/analysis-101/" in path for path in manifest["artifacts"]["numerics"]["files"])
    scan_meta = read_json(project_dir / "numerics" / "scan-results" / "analysis-101" / "scan.meta.json")
    assert scan_meta["formula_fallbacks"][0]["task_id"] == "task-001"
    assert any("formula fallback enabled" in warning for warning in scan_meta["warnings"])

    figure_result = subprocess.run(
        [
            sys.executable,
            str(make_figures_script),
            "--project-dir",
            str(project_dir),
            "--analysis-id",
            "analysis-101",
        ],
        capture_output=True,
        text=True,
    )
    assert figure_result.returncode == 0, figure_result.stdout + figure_result.stderr

    summary_text = summary_path.read_text(encoding="utf-8")
    assert "exclusion-M_Hpp-v_Delta.pdf" in summary_text
    assert "scan1d-M_Hpp-Br_mu_to_egamma.pdf" in summary_text
    manifest = read_json(project_dir / "manifest.json")
    assert manifest["artifacts"]["numerics"]["status"] in {"partial", "done"}
    assert "numerics/analysis-summary-analysis-101.md" in manifest["artifacts"]["numerics"]["files"]
    assert "numerics/figures/analysis-101/exclusion-M_Hpp-v_Delta.pdf" in manifest["artifacts"]["numerics"]["files"]
    assert "numerics/figures/analysis-101/scan1d-M_Hpp-Br_mu_to_egamma.pdf" in manifest["artifacts"]["numerics"]["files"]

    scan_csv_path = project_dir / "numerics" / "scan-results" / "analysis-101" / "scan.csv"
    with scan_csv_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames is not None
        assert "c-001_skip_reason" in reader.fieldnames
        rows = list(reader)
    assert len(rows) == 4
    assert {row["c-001_skip_reason"] for row in rows} == {""}

    figures_dir = project_dir / "numerics" / "figures" / "analysis-101"
    assert (figures_dir / "exclusion-M_Hpp-v_Delta.pdf").exists()
    assert (figures_dir / "exclusion-M_Hpp-v_Delta.png").exists()
    assert (figures_dir / "scan1d-M_Hpp-Br_mu_to_egamma.pdf").exists()
    assert (figures_dir / "scan1d-M_Hpp-Br_mu_to_egamma.png").exists()
