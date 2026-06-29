from __future__ import annotations

import time
from pathlib import Path

import pytest


@pytest.mark.e2e
def test_branch_iii_replot_only_smoke_e2e(
    smoke_e2e_project: Path,
    scan_config_factory,
    run_cli,
    run_scan_script: Path,
    make_figures_script: Path,
    read_json,
    write_json,
) -> None:
    project_dir = smoke_e2e_project
    analysis_id = "analysis-001"
    scan_config_path = scan_config_factory(project_dir, analysis_id, grid=2)

    run_cli(
        [
            run_scan_script,
            "--project-dir",
            project_dir,
            "--analysis-id",
            analysis_id,
        ]
    )
    run_cli(
        [
            make_figures_script,
            "--project-dir",
            project_dir,
            "--analysis-id",
            analysis_id,
        ]
    )

    scan_results_dir = project_dir / "numerics" / "scan-results" / analysis_id
    scan_csv_path = scan_results_dir / "scan.csv"
    scan_meta_path = scan_results_dir / "scan.meta.json"
    figures_dir = project_dir / "numerics" / "figures" / analysis_id
    assert scan_csv_path.exists(), (
        f"initial Branch I run did not create scan.csv: {scan_csv_path}"
    )
    assert scan_meta_path.exists(), (
        f"initial Branch I run did not create scan.meta.json: {scan_meta_path}"
    )
    assert figures_dir.exists(), (
        f"initial make_figures did not create figures dir: {figures_dir}"
    )
    csv_mtime = scan_csv_path.stat().st_mtime_ns
    before_figs = {
        path.name for path in figures_dir.iterdir() if path.is_file()
    }
    assert before_figs, (
        f"initial make_figures created no files in {figures_dir}"
    )

    scan_config = read_json(scan_config_path)
    scan_config["figures"].append(
        {
            "kind": "scan_1d",
            "x": "v_Delta",
            "observables": ["BR_toy"],
            "overlay_constraint_bands": True,
        }
    )
    write_json(scan_config_path, scan_config)

    time.sleep(0.05)

    run_cli(
        [
            make_figures_script,
            "--project-dir",
            project_dir,
            "--analysis-id",
            analysis_id,
            "--overwrite",
        ]
    )

    assert scan_csv_path.stat().st_mtime_ns == csv_mtime, (
        "Branch III replot changed scan.csv mtime; run_scan may have rerun: "
        f"before={csv_mtime}, after={scan_csv_path.stat().st_mtime_ns}"
    )
    scan_meta = read_json(scan_meta_path)
    assert scan_meta["history_action"] == "numerics_analysis_complete", (
        "make_figures should not rewrite scan.meta.json history_action; got "
        f"{scan_meta.get('history_action')!r}"
    )

    new_figure_paths = [
        figures_dir / "scan1d-v_Delta-BR_toy.pdf",
        figures_dir / "scan1d-v_Delta-BR_toy.png",
    ]
    for figure_path in new_figure_paths:
        assert figure_path.exists(), (
            f"Branch III replot did not create new figure: {figure_path}"
        )
        assert figure_path.stat().st_size > 0, (
            f"new Branch III figure is empty: {figure_path}"
        )

    after_figs = {
        path.name for path in figures_dir.iterdir() if path.is_file()
    }
    assert after_figs > before_figs, (
        "Branch III replot should preserve existing figures and add new ones: "
        f"before={before_figs}, after={after_figs}"
    )
    assert len(after_figs - before_figs) >= 2, (
        "Branch III replot should add at least two files for the new figure: "
        f"added={after_figs - before_figs}"
    )

    manifest = read_json(project_dir / "manifest.json")
    latest_action = manifest["history"][-1]["action"]
    assert latest_action == "numerics_figures_regenerated", (
        "manifest history[-1].action after replot mismatch: "
        f"{latest_action!r}"
    )

    files = manifest["artifacts"]["numerics"]["files"]
    new_relpaths = [
        f"numerics/figures/{analysis_id}/scan1d-v_Delta-BR_toy.pdf",
        f"numerics/figures/{analysis_id}/scan1d-v_Delta-BR_toy.png",
    ]
    for relpath in new_relpaths:
        assert files.count(relpath) == 1, (
            "manifest files should contain new figure "
            f"{relpath!r} once: {files}"
        )
