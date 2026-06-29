from __future__ import annotations

from pathlib import Path

import pytest


def _combined_output(proc) -> str:
    return (proc.stdout or "") + "\n" + (proc.stderr or "")


def _assert_contains_any(combined: str, keywords: tuple[str, ...]) -> None:
    assert any(keyword in combined for keyword in keywords), (
        f"expected one of {keywords!r} in combined output:\n{combined}"
    )


def _assert_scan_outputs_absent(project_dir: Path, analysis_id: str) -> None:
    results_dir = project_dir / "numerics" / "scan-results" / analysis_id
    scan_csv_path = results_dir / "scan.csv"
    scan_meta_path = results_dir / "scan.meta.json"
    assert not scan_csv_path.exists(), (
        "scan.csv should not be created after preflight failure: "
        f"{scan_csv_path}"
    )
    assert not scan_meta_path.exists(), (
        "scan.meta.json should not be created after preflight failure: "
        f"{scan_meta_path}"
    )
    if results_dir.exists():
        assert not any(results_dir.iterdir()), (
            f"scan-results directory should be empty after preflight failure: "
            f"{results_dir}"
        )


@pytest.mark.e2e
def test_stale_model_checksum_aborts_smoke_e2e(
    smoke_e2e_project: Path,
    scan_config_factory,
    run_cli,
    run_scan_script: Path,
    read_json,
    write_json,
) -> None:
    project_dir = smoke_e2e_project
    analysis_id = "analysis-001"
    scan_config_path = scan_config_factory(project_dir, analysis_id, grid=2)
    scan_config = read_json(scan_config_path)
    scan_config["depends_on"]["model_checksum"] = "sha256:" + ("0" * 64)
    write_json(scan_config_path, scan_config)

    proc = run_cli(
        [
            run_scan_script,
            "--project-dir",
            project_dir,
            "--analysis-id",
            analysis_id,
        ],
        expect_success=False,
    )
    combined = _combined_output(proc)
    assert proc.returncode != 0, (
        f"stale checksum run_scan should fail, combined output:\n{combined}"
    )
    _assert_contains_any(combined, ("stale", "checksum", "depends_on"))
    _assert_scan_outputs_absent(project_dir, analysis_id)


@pytest.mark.e2e
def test_canonical_name_rejects_latex_smoke_e2e(
    smoke_e2e_project: Path,
    scan_config_factory,
    run_cli,
    run_scan_script: Path,
    read_json,
    write_json,
) -> None:
    project_dir = smoke_e2e_project
    analysis_id = "analysis-001"
    scan_config_path = scan_config_factory(project_dir, analysis_id, grid=2)
    scan_config = read_json(scan_config_path)
    scan_config["scan_parameters"][0]["canonical_name"] = "M_{H++}"
    write_json(scan_config_path, scan_config)

    proc = run_cli(
        [
            run_scan_script,
            "--project-dir",
            project_dir,
            "--analysis-id",
            analysis_id,
        ],
        expect_success=False,
    )
    combined = _combined_output(proc)
    assert proc.returncode != 0, (
        "latex canonical_name run_scan should fail, combined output:\n"
        f"{combined}"
    )
    _assert_contains_any(combined, ("canonical_name", "ASCII", "pattern"))
    _assert_scan_outputs_absent(project_dir, analysis_id)


@pytest.mark.e2e
def test_missing_result_python_aborts_smoke_e2e(
    smoke_e2e_project: Path,
    scan_config_factory,
    run_cli,
    run_scan_script: Path,
    read_json,
) -> None:
    project_dir = smoke_e2e_project
    analysis_id = "analysis-001"
    scan_config_factory(project_dir, analysis_id, grid=2)
    initial_history_length = len(
        read_json(project_dir / "manifest.json")["history"]
    )
    result_python_path = (
        project_dir / "calculations" / "task-001" / "result-python.py"
    )
    result_python_path.unlink()

    proc = run_cli(
        [
            run_scan_script,
            "--project-dir",
            project_dir,
            "--analysis-id",
            analysis_id,
        ],
        expect_success=False,
    )
    combined = _combined_output(proc)
    assert proc.returncode != 0, (
        "missing result-python.py run_scan should fail, combined output:\n"
        f"{combined}"
    )
    _assert_contains_any(
        combined, ("result-python.py", "python_file", "task-001")
    )
    _assert_scan_outputs_absent(project_dir, analysis_id)

    manifest = read_json(project_dir / "manifest.json")
    assert len(manifest["history"]) == initial_history_length, (
        "manifest history should not change after missing python preflight "
        f"failure: before={initial_history_length}, "
        f"after={len(manifest['history'])}"
    )
