from __future__ import annotations

import csv
import hashlib
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


def _write_synthetic_scan(
    project_dir: Path,
    repo_root: Path,
    analysis_id: str = "analysis-001",
) -> None:
    config_dir = project_dir / "numerics" / "scan-configs"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / f"{analysis_id}.json").write_text(
        json.dumps(
            {
                "analysis_id": analysis_id,
                "model_name": "smoke-e2e toy model",
                "depends_on": {
                    "model_version": "v1",
                    "model_checksum": "sha256:c1f3ba396d020c60408998c90681fec754b76a36ba763088dd3285e58334fdd1",
                    "task_ids": ["task-001"],
                },
                "scan_parameters": [
                    {
                        "canonical_name": "M_Hpp",
                        "range": [100.0, 1000.0],
                        "grid": 10,
                        "scale": "linear",
                    },
                    {
                        "canonical_name": "v_Delta",
                        "range": [0.001, 1.0],
                        "grid": 10,
                        "scale": "log",
                    },
                ],
                "fixed_parameters": [],
                "observables": [
                    {
                        "observable": "BR_toy",
                        "source": {"type": "task", "task_id": "task-001"},
                    }
                ],
                "constraints_used": ["c-001"],
                "figures": [],
                "allow_formula_fallback": True,
                "seed": 0,
                "parallelism": 1,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            sys.executable,
            str(
                repo_root
                / ".agents"
                / "skills"
                / "hep-numerics"
                / "scripts"
                / "run_scan.py"
            ),
            "--project-dir",
            str(project_dir),
            "--analysis-id",
            analysis_id,
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


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
    for extension in ("pdf", "png"):
        relpath = pair[extension]
        path = project_dir / relpath
        assert path.exists(), f"missing generated figure: {path}"
        assert path.stat().st_size > 0, f"empty generated figure: {path}"
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert pair[f"{extension}_sha256"] == f"sha256:{digest}"


def test_compare_to_reference_minimal_smoke_fixture(
    tmp_path,
    project_copy_factory,
    smoke_e2e_fixture_path: Path,
    repo_root: Path,
) -> None:
    project_dir = _smoke_project(tmp_path, project_copy_factory, smoke_e2e_fixture_path)
    _write_synthetic_scan(project_dir, repo_root)

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
    assert set(target_002["generated_files"]) == {"overlay"}
    _assert_file_pair_exists(project_dir, target_002["generated_files"]["overlay"])

    assert payload["run_summary"] == {
        "derivation_independence_aggregate": "independent_manual",
        "n_targets_total": 2,
        "n_targets_pass": 0,
        "n_targets_fail": 0,
        "n_targets_needs_human_review": 1,
        "n_targets_blocked": 1,
    }
