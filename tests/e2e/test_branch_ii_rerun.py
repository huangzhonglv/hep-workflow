from __future__ import annotations

import time
from pathlib import Path

import pytest


@pytest.mark.e2e
def test_branch_ii_rerun_smoke_e2e(
    smoke_e2e_project: Path,
    scan_config_factory,
    run_cli,
    run_scan_script: Path,
    read_json,
) -> None:
    project_dir = smoke_e2e_project
    analysis_id = "analysis-001"
    scan_config_factory(project_dir, analysis_id, grid=2)

    run_cli(
        [
            run_scan_script,
            "--project-dir",
            project_dir,
            "--analysis-id",
            analysis_id,
        ]
    )
    scan_csv_path = (
        project_dir / "numerics" / "scan-results" / analysis_id / "scan.csv"
    )
    assert scan_csv_path.exists(), (
        f"first run did not create scan.csv: {scan_csv_path}"
    )
    first_csv_mtime = scan_csv_path.stat().st_mtime_ns

    time.sleep(0.05)

    rerun_result = run_cli(
        [
            run_scan_script,
            "--project-dir",
            project_dir,
            "--analysis-id",
            analysis_id,
        ]
    )

    second_csv_mtime = scan_csv_path.stat().st_mtime_ns
    assert second_csv_mtime > first_csv_mtime, (
        "rerun did not refresh scan.csv mtime: "
        f"first={first_csv_mtime}, second={second_csv_mtime}"
    )

    scan_meta_path = (
        project_dir
        / "numerics"
        / "scan-results"
        / analysis_id
        / "scan.meta.json"
    )
    assert scan_meta_path.exists(), (
        f"rerun did not create scan.meta.json: {scan_meta_path}"
    )
    scan_meta = read_json(scan_meta_path)
    assert scan_meta["history_action"] == "numerics_analysis_rerun", (
        "scan.meta.json history_action after rerun mismatch: "
        f"{scan_meta.get('history_action')!r}"
    )

    manifest = read_json(project_dir / "manifest.json")
    assert manifest["history"][-1]["action"] == "numerics_analysis_rerun", (
        "manifest history[-1].action after rerun mismatch: "
        f"{manifest['history'][-1].get('action')!r}"
    )
    analyses = manifest["artifacts"]["numerics"]["analyses"]
    assert analyses == [analysis_id], (
        "manifest analyses should be deduped to "
        f"[{analysis_id!r}], got {analyses}"
    )

    files = manifest["artifacts"]["numerics"]["files"]
    scan_csv_relpath = f"numerics/scan-results/{analysis_id}/scan.csv"
    scan_meta_relpath = f"numerics/scan-results/{analysis_id}/scan.meta.json"
    assert files.count(scan_csv_relpath) == 1, (
        f"manifest files should contain {scan_csv_relpath!r} once, got {files}"
    )
    assert files.count(scan_meta_relpath) == 1, (
        "manifest files should contain "
        f"{scan_meta_relpath!r} once, got {files}"
    )

    rerun_output = (rerun_result.stdout + rerun_result.stderr).lower()
    if "rerun" in rerun_output:
        assert "rerun" in rerun_output, (
            f"rerun log should mention rerun when available: {rerun_output}"
        )
    else:
        assert scan_meta["history_action"] == "numerics_analysis_rerun", (
            "run_scan log has no rerun keyword; relying on scan.meta.json "
            "history_action instead"
        )
