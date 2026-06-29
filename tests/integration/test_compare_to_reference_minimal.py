from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

from jsonschema import Draft202012Validator


def _smoke_project(tmp_path, project_copy_factory, smoke_e2e_fixture_path: Path) -> Path:
    return project_copy_factory(
        tmp_path,
        "smoke-e2e",
        source_project_path=smoke_e2e_fixture_path,
    )


def _write_synthetic_scan(project_dir: Path, analysis_id: str = "analysis-001") -> None:
    results_dir = project_dir / "numerics" / "scan-results" / analysis_id
    results_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for mass in [100.0, 200.0, 300.0, 400.0, 500.0, 600.0, 700.0, 800.0, 900.0, 1000.0]:
        rows.append(
            {
                "M_Hpp": mass,
                "v_Delta": 0.001,
                "BR_toy": 1.0e-4 * (0.001 / mass) ** 2,
                "c-001_verdict": "allowed",
            }
        )
    with (results_dir / "scan.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["M_Hpp", "v_Delta", "BR_toy", "c-001_verdict"],
        )
        writer.writeheader()
        writer.writerows(rows)

    (results_dir / "scan.meta.json").write_text(
        json.dumps(
            {
                "analysis_id": analysis_id,
                "history_action": "numerics_analysis_complete",
                "scan_parameters": ["M_Hpp"],
                "observables": ["BR_toy"],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _run_compare(repo_root: Path, project_dir: Path, repro_id: str = "run-001"):
    return subprocess.run(
        [
            sys.executable,
            str(repo_root / "scripts" / "compare_to_reference.py"),
            "--project-dir",
            str(project_dir),
            "--analysis-id",
            "analysis-001",
            "--repro-id",
            repro_id,
            "--blocked-targets",
            "target-002",
        ],
        capture_output=True,
        text=True,
        cwd=repo_root,
    )


def _validate_result(repo_root: Path, payload: dict) -> None:
    schema = json.loads(
        (repo_root / "schemas" / "reproduction-result.schema.json").read_text(
            encoding="utf-8"
        )
    )
    Draft202012Validator(schema).validate(payload)


def _assert_file_pair_exists(project_dir: Path, pair: dict[str, str]) -> None:
    for relpath in pair.values():
        path = project_dir / relpath
        assert path.exists(), f"missing generated figure: {path}"
        assert path.stat().st_size > 0, f"empty generated figure: {path}"


def test_compare_to_reference_minimal_smoke_fixture(
    tmp_path,
    project_copy_factory,
    smoke_e2e_fixture_path: Path,
    repo_root: Path,
) -> None:
    project_dir = _smoke_project(tmp_path, project_copy_factory, smoke_e2e_fixture_path)
    _write_synthetic_scan(project_dir)

    result = _run_compare(repo_root, project_dir)
    assert result.returncode == 0, result.stdout + result.stderr

    result_path = project_dir / "reproduction" / "runs" / "run-001" / "reproduction-result.json"
    assert result_path.exists()
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    _validate_result(repo_root, payload)

    by_target = {item["target_id"]: item for item in payload["results"]}
    target_001 = by_target["target-001"]
    target_002 = by_target["target-002"]

    assert target_001["derivation_independence"] == "independent_manual"
    assert target_001["verdict"] == "needs_human_review"
    assert target_001["tasks_used"] == ["task-001"]
    for pair in target_001["generated_files"].values():
        _assert_file_pair_exists(project_dir, pair)

    assert target_002["derivation_independence"] == "independent_manual"
    assert target_002["verdict"] == "blocked"
    assert any("blocked_by_orchestrator" in warning for warning in target_002["warnings"])
    _assert_file_pair_exists(project_dir, target_002["generated_files"]["overlay"])
    for key in ["side_by_side", "residual"]:
        for relpath in target_002["generated_files"][key].values():
            assert not (project_dir / relpath).exists()

    assert payload["run_summary"] == {
        "derivation_independence_aggregate": "independent_manual",
        "n_targets_total": 2,
        "n_targets_pass": 0,
        "n_targets_fail": 0,
        "n_targets_needs_human_review": 1,
        "n_targets_blocked": 1,
    }
