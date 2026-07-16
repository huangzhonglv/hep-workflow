from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

import pytest


@pytest.fixture
def smoke_e2e_fixture_path(repo_root: Path) -> Path:
    return (repo_root / "workspace" / "projects" / "smoke-e2e").resolve()


@pytest.fixture
def smoke_e2e_project(tmp_path: Path, smoke_e2e_fixture_path: Path) -> Path:
    destination = tmp_path / "workspace" / "projects" / "smoke-e2e"
    shutil.copytree(smoke_e2e_fixture_path, destination)
    return destination


@pytest.fixture
def wolframscript_required() -> str:
    wolframscript_path = shutil.which("wolframscript")
    if wolframscript_path is None:
        pytest.fail(
            "wolframscript is required for E2E tests but was not found on PATH"
        )
    return wolframscript_path


@pytest.fixture
def scan_config_factory(
    write_json: Callable[[Path, Any], None],
    read_json: Callable[[Path], Any],
) -> Callable[[Path, str], Path]:
    def make_scan_config(
        project_dir: Path,
        analysis_id: str,
        *,
        grid: int = 2,
        overrides: dict[str, Any] | None = None,
    ) -> Path:
        manifest = read_json(project_dir / "manifest.json")
        model_artifact = manifest["artifacts"]["model"]
        scan_config = {
            "analysis_id": analysis_id,
            "model_name": "smoke-e2e toy model",
            "description": "E2E smoke test scan",
            "depends_on": {
                "model_version": model_artifact["version"],
                "model_checksum": model_artifact["checksum"],
                "task_ids": ["task-001"],
            },
            "scan_parameters": [
                {
                    "canonical_name": "M_Hpp",
                    "range": [100.0, 1000.0],
                    "grid": grid,
                    "scale": "linear",
                },
                {
                    "canonical_name": "v_Delta",
                    "range": [1.0e-3, 1.0],
                    "grid": grid,
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
                    "observables": ["BR_toy"],
                    "fixed": {"v_Delta": 1.0e-3},
                    "overlay_constraint_bands": True,
                },
            ],
            "allow_formula_fallback": True,
            "seed": 0,
            "parallelism": 1,
        }
        if overrides is not None:
            scan_config.update(overrides)

        scan_config_path = (
            project_dir / "numerics" / "scan-configs" / f"{analysis_id}.json"
        )
        write_json(scan_config_path, scan_config)
        return scan_config_path

    return make_scan_config


@pytest.fixture
def run_cli(tmp_path: Path) -> Callable[..., subprocess.CompletedProcess[str]]:
    def _run(
        cmd: list[str | Path],
        *,
        expect_success: bool = True,
        env_extra: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        if env_extra is not None:
            env.update(env_extra)

        command = [sys.executable, *[str(part) for part in cmd]]
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=120,
            cwd=tmp_path,
            env=env,
        )
        if expect_success and result.returncode != 0:
            pytest.fail(
                "command failed with return code "
                f"{result.returncode}: {' '.join(command)}\n"
                f"stdout:\n{result.stdout}\n"
                f"stderr:\n{result.stderr}"
            )
        return result

    return _run
