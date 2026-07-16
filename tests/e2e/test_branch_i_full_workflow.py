from __future__ import annotations

import csv
from pathlib import Path

import pytest


@pytest.mark.e2e
def test_branch_i_full_workflow_smoke_e2e(
    tmp_path: Path,
    smoke_e2e_project: Path,
    scan_config_factory,
    run_cli,
    init_analysis_script: Path,
    run_scan_script: Path,
    make_figures_script: Path,
    read_json,
    repo_root: Path,
) -> None:
    project_dir = smoke_e2e_project
    analysis_id = "analysis-001"

    scan_config_path = scan_config_factory(project_dir, analysis_id, grid=2)
    assert scan_config_path.exists(), (
        f"scan-config was not written: {scan_config_path}"
    )

    run_cli(
        [
            init_analysis_script,
            "--project-dir",
            project_dir,
            "--analysis-id",
            "analysis-002",
            "--allow-formula-fallback",
        ]
    )
    init_config_path = (
        project_dir / "numerics" / "scan-configs" / "analysis-002.json"
    )
    assert init_config_path.exists(), (
        f"init_analysis did not create draft config: {init_config_path}"
    )
    assert read_json(init_config_path)["allow_formula_fallback"] is True, (
        "the e2e fixture uses a manual-tree backend and must opt in explicitly"
    )

    run_cli(
        [
            run_scan_script,
            "--project-dir",
            project_dir,
            "--analysis-id",
            analysis_id,
        ]
    )

    scan_results_dir = project_dir / "numerics" / "scan-results" / analysis_id
    scan_csv_path = scan_results_dir / "scan.csv"
    assert scan_csv_path.exists(), (
        f"run_scan did not create scan.csv: {scan_csv_path}"
    )
    assert scan_csv_path.stat().st_size > 0, (
        f"scan.csv is empty: {scan_csv_path}"
    )

    with scan_csv_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
    assert reader.fieldnames is not None, (
        f"scan.csv has no header: {scan_csv_path}"
    )
    for column in ("M_Hpp", "v_Delta", "BR_toy", "c-001_verdict"):
        assert column in reader.fieldnames, (
            f"scan.csv header missing {column!r}: {reader.fieldnames}"
        )
    assert len(rows) == 4, (
        "scan.csv should have 4 rows for a 2x2 grid, "
        f"got {len(rows)}"
    )
    for index, row in enumerate(rows, start=1):
        assert row["c-001_verdict"] in {"allowed", "excluded"}, (
            "row "
            f"{index} has unexpected c-001_verdict: "
            f"{row['c-001_verdict']!r}"
        )

    scan_meta_path = scan_results_dir / "scan.meta.json"
    assert scan_meta_path.exists(), (
        f"run_scan did not create scan.meta.json: {scan_meta_path}"
    )
    scan_meta = read_json(scan_meta_path)
    assert scan_meta["history_action"] == "numerics_analysis_complete", (
        "scan.meta.json history_action mismatch: "
        f"{scan_meta.get('history_action')!r}"
    )

    summary_path = (
        project_dir / "numerics" / f"analysis-summary-{analysis_id}.md"
    )
    assert summary_path.exists(), (
        f"run_scan did not create analysis summary: {summary_path}"
    )
    summary_text = summary_path.read_text(encoding="utf-8")
    assert summary_text.startswith("#"), (
        f"analysis summary should start with '#': {summary_path}"
    )
    assert "BR_toy" in summary_text, (
        "analysis summary missing observable BR_toy"
    )
    assert analysis_id in summary_text, (
        f"analysis summary missing {analysis_id}"
    )

    manifest = read_json(project_dir / "manifest.json")
    numerics = manifest["artifacts"]["numerics"]
    files_after_scan = numerics["files"]
    assert manifest["history"][-1]["action"] == "numerics_analysis_complete", (
        "manifest history[-1].action after run_scan mismatch: "
        f"{manifest['history'][-1].get('action')!r}"
    )
    assert numerics["status"] in {"partial", "done"}, (
        "manifest numerics status after run_scan is unexpected: "
        f"{numerics['status']!r}"
    )
    assert analysis_id in {
        analysis["analysis_id"] for analysis in numerics["analyses"]
    }, (
        "manifest numerics analyses missing "
        f"{analysis_id}: {numerics['analyses']}"
    )
    expected_scan_files = {
        f"numerics/scan-results/{analysis_id}/scan.csv",
        f"numerics/scan-results/{analysis_id}/scan.meta.json",
        f"numerics/analysis-summary-{analysis_id}.md",
    }
    for relpath in expected_scan_files:
        assert relpath in files_after_scan, (
            f"manifest numerics files missing scan artifact {relpath!r}: "
            f"{files_after_scan}"
        )
    figure_prefix = f"numerics/figures/{analysis_id}/"
    has_figure_before_make = any(
        path.startswith(figure_prefix) for path in files_after_scan
    )
    assert not has_figure_before_make, (
        "manifest numerics files unexpectedly include figure artifacts before "
        f"make_figures: {files_after_scan}"
    )

    history_length_after_scan = len(manifest["history"])
    run_cli(
        [
            make_figures_script,
            "--project-dir",
            project_dir,
            "--analysis-id",
            analysis_id,
        ]
    )

    figure_paths = [
        project_dir
        / "numerics"
        / "figures"
        / analysis_id
        / "exclusion-M_Hpp-v_Delta.pdf",
        project_dir
        / "numerics"
        / "figures"
        / analysis_id
        / "exclusion-M_Hpp-v_Delta.png",
        project_dir
        / "numerics"
        / "figures"
        / analysis_id
        / "scan1d-M_Hpp-BR_toy.pdf",
        project_dir
        / "numerics"
        / "figures"
        / analysis_id
        / "scan1d-M_Hpp-BR_toy.png",
    ]
    for figure_path in figure_paths:
        assert figure_path.exists(), (
            f"make_figures did not create {figure_path}"
        )
        assert figure_path.stat().st_size > 0, (
            f"figure file is empty: {figure_path}"
        )

    manifest = read_json(project_dir / "manifest.json")
    numerics = manifest["artifacts"]["numerics"]
    assert manifest["history"][-1]["action"] == "numerics_analysis_complete", (
        "manifest history[-1].action after make_figures mismatch: "
        f"{manifest['history'][-1].get('action')!r}"
    )
    assert len(manifest["history"]) == history_length_after_scan, (
        "make_figures should not append a history entry on first figure pass "
        f"after run_scan; before={history_length_after_scan}, "
        f"after={len(manifest['history'])}"
    )
    expected_figure_files = {
        f"numerics/figures/{analysis_id}/exclusion-M_Hpp-v_Delta.pdf",
        f"numerics/figures/{analysis_id}/exclusion-M_Hpp-v_Delta.png",
        f"numerics/figures/{analysis_id}/scan1d-M_Hpp-BR_toy.pdf",
        f"numerics/figures/{analysis_id}/scan1d-M_Hpp-BR_toy.png",
    }
    for relpath in expected_figure_files:
        assert relpath in numerics["files"], (
            f"manifest numerics files missing figure artifact {relpath!r}: "
            f"{numerics['files']}"
        )

    run_cli(
        [
            repo_root / "scripts" / "validate_workspace_projects.py",
            "--workspace-root",
            tmp_path / "workspace" / "projects",
            "smoke-e2e",
        ]
    )
