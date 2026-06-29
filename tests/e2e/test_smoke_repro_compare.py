from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator


def _validate_reproduction_result(repo_root: Path, payload: dict) -> None:
    schema = json.loads(
        (repo_root / "schemas" / "reproduction-result.schema.json").read_text(
            encoding="utf-8"
        )
    )
    Draft202012Validator(schema).validate(payload)


def _assert_pair_exists(project_dir: Path, pair: dict[str, str]) -> None:
    for relpath in pair.values():
        path = project_dir / relpath
        assert path.exists(), f"missing reproduction figure: {path}"
        assert path.stat().st_size > 0, f"empty reproduction figure: {path}"


@pytest.mark.e2e
def test_smoke_repro_compare_after_scan(
    smoke_e2e_project: Path,
    scan_config_factory,
    run_cli,
    run_scan_script: Path,
    repo_root: Path,
) -> None:
    project_dir = smoke_e2e_project
    analysis_id = "analysis-001"
    scan_config_factory(project_dir, analysis_id, grid=10)

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
            repo_root / "scripts" / "compare_to_reference.py",
            "--project-dir",
            project_dir,
            "--analysis-id",
            analysis_id,
            "--repro-id",
            "run-001",
            "--blocked-targets",
            "target-002",
        ]
    )

    result_path = (
        project_dir
        / "reproduction"
        / "runs"
        / "run-001"
        / "reproduction-result.json"
    )
    assert result_path.exists()
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    _validate_reproduction_result(repo_root, payload)

    by_target = {item["target_id"]: item for item in payload["results"]}
    non_blocked = by_target["target-001"]
    blocked = by_target["target-002"]

    for pair in non_blocked["generated_files"].values():
        _assert_pair_exists(project_dir, pair)
    _assert_pair_exists(project_dir, blocked["generated_files"]["overlay"])
    for key in ["side_by_side", "residual"]:
        for relpath in blocked["generated_files"][key].values():
            assert not (project_dir / relpath).exists()

    assert blocked["verdict"] == "blocked"
    assert payload["run_summary"]["n_targets_blocked"] == 1

    manifest = json.loads((project_dir / "manifest.json").read_text(encoding="utf-8"))
    actions = [entry["action"] for entry in manifest["history"]]
    assert "literature_complete" in actions
    assert "reproduction_run_complete" not in actions
