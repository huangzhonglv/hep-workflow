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

    after_figs = {
        path.name for path in figures_dir.iterdir() if path.is_file()
    }
    assert after_figs == before_figs, (
        "exact-snapshot Branch III replot should replace the configured figure set: "
        f"before={before_figs}, after={after_figs}"
    )

    manifest = read_json(project_dir / "manifest.json")
    latest_action = manifest["history"][-1]["action"]
    assert latest_action == "numerics_figures_regenerated", (
        "manifest history[-1].action after replot mismatch: "
        f"{latest_action!r}"
    )

    files = manifest["artifacts"]["numerics"]["files"]
    for filename in after_figs:
        relpath = f"numerics/figures/{analysis_id}/{filename}"
        assert files.count(relpath) == 1, (
            f"manifest files should contain regenerated figure {relpath!r} once: {files}"
        )

    scan_csv_bytes = scan_csv_path.read_bytes()
    scan_meta_bytes = scan_meta_path.read_bytes()
    scan_input_provenance = scan_meta["input_provenance"]
    figure_meta_path = figures_dir / "figures.meta.json"
    figure_meta_before_title = read_json(figure_meta_path)

    scan_config = read_json(scan_config_path)
    scan_config["figures"][0]["title"] = "Renderer-only revised title"
    write_json(scan_config_path, scan_config)

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

    assert scan_csv_path.read_bytes() == scan_csv_bytes
    assert scan_meta_path.read_bytes() == scan_meta_bytes
    assert read_json(scan_meta_path)["input_provenance"] == scan_input_provenance
    figure_meta_after_title = read_json(figure_meta_path)
    assert (
        figure_meta_after_title["render_config_snapshot"]["figures"][0]["title"]
        == "Renderer-only revised title"
    )
    assert (
        figure_meta_after_title["scan_execution_sha256"]
        == figure_meta_before_title["scan_execution_sha256"]
    )
    assert (
        figure_meta_after_title["input_provenance"]["root_sha256"]
        != figure_meta_before_title["input_provenance"]["root_sha256"]
    )

    manifest_after_title_replot = read_json(project_dir / "manifest.json")
    scan_config = read_json(scan_config_path)
    scan_config["figures"].append(
        {
            "kind": "scan_1d",
            "x": "v_Delta",
            "observables": ["BR_toy"],
            "fixed": {"M_Hpp": 100.0},
            "overlay_constraint_bands": True,
        }
    )
    write_json(scan_config_path, scan_config)

    rejected = run_cli(
        [
            make_figures_script,
            "--project-dir",
            project_dir,
            "--analysis-id",
            analysis_id,
            "--overwrite",
        ],
        expect_success=False,
    )

    assert rejected.returncode != 0
    assert "scan_config_snapshot execution semantics do not match" in rejected.stderr
    assert scan_csv_path.stat().st_mtime_ns == csv_mtime
    assert scan_meta_path.read_bytes() == scan_meta_bytes
    assert read_json(project_dir / "manifest.json") == manifest_after_title_replot
    assert {
        path.name for path in figures_dir.iterdir() if path.is_file()
    } == after_figs
    assert not (figures_dir / "scan1d-v_Delta-BR_toy.pdf").exists()
    assert not (figures_dir / "scan1d-v_Delta-BR_toy.png").exists()
