from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest

from scripts._reproduction_result_validation import (
    reproduction_result_semantic_errors,
)
from tests.unit.compare_reference_fixtures import (
    load_result,
    make_compare_project,
    run_compare,
)


def _workspace_validation(
    repo_root: Path,
    project_dir: Path,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(repo_root / "scripts" / "validate_workspace_projects.py"),
            "--workspace-root",
            str(project_dir.parent),
            project_dir.name,
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )


def _project_snapshot(project_dir: Path) -> tuple[tuple[Any, ...], ...]:
    """Capture files, directories, and symlinks without following links."""

    entries: list[tuple[Any, ...]] = []
    for path in sorted(
        project_dir.rglob("*"),
        key=lambda candidate: candidate.relative_to(project_dir).as_posix(),
    ):
        relative = path.relative_to(project_dir).as_posix()
        if path.is_symlink():
            entries.append((relative, "symlink", os.readlink(path)))
        elif path.is_dir():
            entries.append((relative, "directory"))
        else:
            entries.append(
                (
                    relative,
                    "file",
                    hashlib.sha256(path.read_bytes()).hexdigest(),
                )
            )
    return tuple(entries)


def _prepared_numerics_project(
    tmp_path: Path,
    project_copy_factory,
    rebind_calculation_result,
    rebind_scan_result,
) -> Path:
    project_dir = project_copy_factory(tmp_path)
    rebind_calculation_result(project_dir)
    rebind_scan_result(project_dir)
    return project_dir


@pytest.mark.parametrize(
    "script_name",
    [
        "init_analysis.py",
        "validate_scan_config.py",
        "run_scan.py",
        "make_figures.py",
    ],
)
def test_numerics_clis_reject_non_ascii_analysis_id_without_side_effects(
    tmp_path: Path,
    project_copy_factory,
    repo_root: Path,
    script_name: str,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    before = _project_snapshot(project_dir)
    script = (
        repo_root
        / ".agents"
        / "skills"
        / "hep-numerics"
        / "scripts"
        / script_name
    )

    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--project-dir",
            str(project_dir),
            "--analysis-id",
            "analysis-٠٠١",
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert "analysis" in (completed.stdout + completed.stderr).lower()
    assert _project_snapshot(project_dir) == before


@pytest.mark.parametrize(
    "invalid_flag,invalid_value",
    [
        ("--analysis-id", "analysis-٠٠١"),
        ("--repro-id", "../run-001"),
    ],
)
def test_compare_cli_rejects_invalid_ids_without_side_effects(
    tmp_path: Path,
    project_copy_factory,
    repo_root: Path,
    invalid_flag: str,
    invalid_value: str,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    arguments = {
        "--analysis-id": "analysis-001",
        "--repro-id": "run-001",
    }
    arguments[invalid_flag] = invalid_value
    before = _project_snapshot(project_dir)

    completed = subprocess.run(
        [
            sys.executable,
            str(repo_root / "scripts" / "compare_to_reference.py"),
            "--project-dir",
            str(project_dir),
            "--analysis-id",
            arguments["--analysis-id"],
            "--repro-id",
            arguments["--repro-id"],
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert _project_snapshot(project_dir) == before


@pytest.mark.parametrize(
    "script_name",
    ["validate_scan_config.py", "run_scan.py", "make_figures.py"],
)
def test_scan_config_path_payload_and_stem_mismatch_has_no_side_effects(
    tmp_path: Path,
    project_copy_factory,
    repo_root: Path,
    script_name: str,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    canonical_path = (
        project_dir
        / "numerics"
        / "scan-configs"
        / "analysis-001.json"
    )
    mismatched_path = canonical_path.with_name("analysis-002.json")
    mismatched_path.write_bytes(canonical_path.read_bytes())
    before = _project_snapshot(project_dir)
    script = (
        repo_root
        / ".agents"
        / "skills"
        / "hep-numerics"
        / "scripts"
        / script_name
    )

    completed = subprocess.run(
        [sys.executable, str(script), "--scan-config", str(mismatched_path)],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )

    combined = completed.stdout + completed.stderr
    assert completed.returncode != 0
    assert "analysis-001" in combined
    assert "named" in combined or "filename stem" in combined
    assert _project_snapshot(project_dir) == before


def test_calculation_exact_byte_mutation_invalidates_persisted_graph(
    tmp_path: Path,
    project_copy_factory,
    rebind_calculation_result,
    rebind_scan_result,
    repo_root: Path,
) -> None:
    project_dir = _prepared_numerics_project(
        tmp_path,
        project_copy_factory,
        rebind_calculation_result,
        rebind_scan_result,
    )
    baseline = _workspace_validation(repo_root, project_dir)
    assert baseline.returncode == 0, baseline.stdout + baseline.stderr

    backend = project_dir / "calculations" / "task-001" / "result-python.py"
    backend.write_bytes(backend.read_bytes() + b"\n# exact-byte drift\n")

    completed = _workspace_validation(repo_root, project_dir)
    combined = completed.stdout + completed.stderr
    assert completed.returncode != 0
    assert "calculations/task-001/result-meta.json: input provenance" in combined
    assert "does not match current exact bytes" in combined


def test_scan_exact_byte_mutation_invalidates_persisted_graph(
    tmp_path: Path,
    project_copy_factory,
    rebind_calculation_result,
    rebind_scan_result,
    repo_root: Path,
) -> None:
    project_dir = _prepared_numerics_project(
        tmp_path,
        project_copy_factory,
        rebind_calculation_result,
        rebind_scan_result,
    )
    baseline = _workspace_validation(repo_root, project_dir)
    assert baseline.returncode == 0, baseline.stdout + baseline.stderr

    constraints = project_dir / "constraints" / "constraints-data.json"
    constraints.write_bytes(constraints.read_bytes() + b" ")

    completed = _workspace_validation(repo_root, project_dir)
    combined = completed.stdout + completed.stderr
    assert completed.returncode != 0
    assert "scan input provenance" in combined
    assert "does not match current exact bytes" in combined


def test_reproduction_exact_byte_mutation_invalidates_persisted_graph(
    tmp_path: Path,
    repo_root: Path,
) -> None:
    project_dir = make_compare_project(tmp_path)
    completed = run_compare(repo_root, project_dir, "run-001")
    assert completed.returncode == 0, completed.stdout + completed.stderr
    payload = load_result(project_dir, "run-001")
    run_dir = project_dir / "reproduction" / "runs" / "run-001"
    baseline_errors = reproduction_result_semantic_errors(
        payload,
        project_dir=project_dir,
        expected_run_dir=run_dir,
        scientific_project_dir=project_dir,
    )
    assert baseline_errors == []

    paper_extract = project_dir / "literature" / "paper-extract.json"
    paper_extract.write_bytes(paper_extract.read_bytes() + b" ")

    errors = reproduction_result_semantic_errors(
        payload,
        project_dir=project_dir,
        expected_run_dir=run_dir,
        scientific_project_dir=project_dir,
    )
    assert any(
        "input_provenance" in error and "current exact bytes" in error
        for error in errors
    ), errors
