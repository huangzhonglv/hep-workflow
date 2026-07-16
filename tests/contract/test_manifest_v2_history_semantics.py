from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def run_validator(repo_root: Path, project_dir: Path) -> subprocess.CompletedProcess[str]:
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


def current_project(
    tmp_path: Path,
    project_copy_factory,
    rebind_calculation_result,
    rebind_scan_result,
) -> Path:
    project_dir = project_copy_factory(tmp_path)
    rebind_calculation_result(project_dir)
    rebind_scan_result(project_dir)
    return project_dir


def test_legacy_numerics_history_without_event_id_remains_readable(
    tmp_path: Path,
    project_copy_factory,
    rebind_calculation_result,
    rebind_scan_result,
    repo_root: Path,
) -> None:
    project_dir = current_project(
        tmp_path,
        project_copy_factory,
        rebind_calculation_result,
        rebind_scan_result,
    )

    completed = run_validator(repo_root, project_dir)

    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_workspace_rejects_duplicate_history_event_ids(
    tmp_path: Path,
    project_copy_factory,
    read_json,
    write_json,
    rebind_calculation_result,
    rebind_scan_result,
    repo_root: Path,
) -> None:
    project_dir = current_project(
        tmp_path,
        project_copy_factory,
        rebind_calculation_result,
        rebind_scan_result,
    )
    manifest_path = project_dir / "manifest.json"
    manifest = read_json(manifest_path)
    event_id = "1" * 32
    manifest["history"].extend(
        [
            {
                "action": "numerics_analysis_rerun",
                "analysis_id": "analysis-001",
                "event_id": event_id,
                "timestamp": "2026-07-13T00:00:00Z",
                "by": "pytest",
                "note": "analysis_id=analysis-001 first",
            },
            {
                "action": "numerics_figures_regenerated",
                "analysis_id": "analysis-001",
                "event_id": event_id,
                "timestamp": "2026-07-13T00:00:01Z",
                "by": "pytest",
                "note": "analysis_id=analysis-001 second",
            },
        ]
    )
    write_json(manifest_path, manifest)

    completed = run_validator(repo_root, project_dir)

    assert completed.returncode != 0
    assert "duplicate event_id" in completed.stdout + completed.stderr


def test_workspace_rejects_unknown_numerics_history_link(
    tmp_path: Path,
    project_copy_factory,
    read_json,
    write_json,
    rebind_calculation_result,
    rebind_scan_result,
    repo_root: Path,
) -> None:
    project_dir = current_project(
        tmp_path,
        project_copy_factory,
        rebind_calculation_result,
        rebind_scan_result,
    )
    manifest_path = project_dir / "manifest.json"
    manifest = read_json(manifest_path)
    manifest["history"].append(
        {
            "action": "numerics_analysis_rerun",
            "analysis_id": "analysis-999",
            "event_id": "2" * 32,
            "timestamp": "2026-07-13T00:00:00Z",
            "by": "pytest",
            "note": "analysis_id=analysis-999",
        }
    )
    write_json(manifest_path, manifest)

    completed = run_validator(repo_root, project_dir)

    assert completed.returncode != 0
    assert "references unknown numerics analysis 'analysis-999'" in (
        completed.stdout + completed.stderr
    )


def test_workspace_rejects_published_scan_without_manifest_owner(
    tmp_path: Path,
    project_copy_factory,
    read_json,
    write_json,
    rebind_calculation_result,
    rebind_scan_result,
    repo_root: Path,
) -> None:
    project_dir = current_project(
        tmp_path,
        project_copy_factory,
        rebind_calculation_result,
        rebind_scan_result,
    )
    manifest_path = project_dir / "manifest.json"
    manifest = read_json(manifest_path)
    manifest["artifacts"]["numerics"] = {
        "status": "not_started",
        "files": [],
        "analyses": [],
        "produced_by": None,
        "timestamp": None,
    }
    manifest["history"] = [
        entry
        for entry in manifest["history"]
        if not str(entry.get("action", "")).startswith("numerics_")
    ]
    write_json(manifest_path, manifest)

    completed = run_validator(repo_root, project_dir)

    assert completed.returncode != 0
    assert "published scan-result directories lack evidence-bearing manifest owners" in (
        completed.stdout + completed.stderr
    )


def test_workspace_accepts_owned_partial_scan_with_incomplete_figure_coverage(
    tmp_path: Path,
    project_copy_factory,
    read_json,
    write_json,
    rebind_calculation_result,
    rebind_scan_result,
    repo_root: Path,
) -> None:
    project_dir = current_project(
        tmp_path,
        project_copy_factory,
        rebind_calculation_result,
        rebind_scan_result,
    )
    config_path = project_dir / "numerics" / "scan-configs" / "analysis-001.json"
    config = read_json(config_path)
    config["figures"] = [
        {
            "kind": "scan_1d",
            "x": "M_Hpp",
            "observables": ["Br_mu_to_egamma"],
            "overlay_constraint_bands": True,
        }
    ]
    write_json(config_path, config)
    rebind_scan_result(project_dir)
    manifest_path = project_dir / "manifest.json"
    manifest = read_json(manifest_path)
    manifest["artifacts"]["numerics"]["analyses"][0]["status"] = "partial"
    manifest["artifacts"]["numerics"]["status"] = "partial"
    write_json(manifest_path, manifest)

    completed = run_validator(repo_root, project_dir)

    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_workspace_rejects_reproduction_dependency_on_unknown_analysis(
    tmp_path: Path,
    project_copy_factory,
    read_json,
    write_json,
    rebind_calculation_result,
    rebind_scan_result,
    repo_root: Path,
) -> None:
    project_dir = current_project(
        tmp_path,
        project_copy_factory,
        rebind_calculation_result,
        rebind_scan_result,
    )
    manifest_path = project_dir / "manifest.json"
    manifest = read_json(manifest_path)
    model = manifest["artifacts"]["model"]
    manifest["artifacts"]["reproduction"] = {
        "status": "in_progress",
        "runs": [],
        "depends_on": {
            "model": {"version": model["version"], "checksum": model["checksum"]},
            "literature": {"checksum": "sha256:" + "0" * 64},
            "numerics": {"analyses": ["analysis-999"]},
        },
        "produced_by": None,
        "timestamp": None,
    }
    write_json(manifest_path, manifest)

    completed = run_validator(repo_root, project_dir)

    assert completed.returncode != 0
    assert "references unknown analysis 'analysis-999'" in (
        completed.stdout + completed.stderr
    )


def test_workspace_rejects_reproduction_dependency_on_partial_analysis(
    tmp_path: Path,
    project_copy_factory,
    read_json,
    write_json,
    rebind_calculation_result,
    rebind_scan_result,
    repo_root: Path,
) -> None:
    project_dir = current_project(
        tmp_path,
        project_copy_factory,
        rebind_calculation_result,
        rebind_scan_result,
    )
    manifest_path = project_dir / "manifest.json"
    manifest = read_json(manifest_path)
    manifest["artifacts"]["numerics"]["analyses"][0]["status"] = "partial"
    manifest["artifacts"]["numerics"]["status"] = "partial"
    model = manifest["artifacts"]["model"]
    manifest["artifacts"]["reproduction"] = {
        "status": "in_progress",
        "runs": [],
        "depends_on": {
            "model": {"version": model["version"], "checksum": model["checksum"]},
            "literature": {"checksum": "sha256:" + "0" * 64},
            "numerics": {"analyses": ["analysis-001"]},
        },
        "produced_by": None,
        "timestamp": None,
    }
    write_json(manifest_path, manifest)

    completed = run_validator(repo_root, project_dir)

    assert completed.returncode != 0
    assert "analysis 'analysis-001' is not consumable: status='partial'" in (
        completed.stdout + completed.stderr
    )


def test_workspace_rejects_unlisted_immutable_reproduction_run(
    tmp_path: Path,
    project_copy_factory,
    rebind_calculation_result,
    rebind_scan_result,
    repo_root: Path,
) -> None:
    project_dir = current_project(
        tmp_path,
        project_copy_factory,
        rebind_calculation_result,
        rebind_scan_result,
    )
    (project_dir / "reproduction" / "runs" / "run-001").mkdir(parents=True)

    completed = run_validator(repo_root, project_dir)

    assert completed.returncode != 0
    assert "immutable run directory must appear exactly once" in (
        completed.stdout + completed.stderr
    )


def test_workspace_requires_one_identified_completion_event_per_listed_run(
    tmp_path: Path,
    project_copy_factory,
    read_json,
    write_json,
    rebind_calculation_result,
    rebind_scan_result,
    repo_root: Path,
) -> None:
    project_dir = current_project(
        tmp_path,
        project_copy_factory,
        rebind_calculation_result,
        rebind_scan_result,
    )
    (project_dir / "reproduction" / "runs" / "run-001").mkdir(parents=True)
    manifest_path = project_dir / "manifest.json"
    manifest = read_json(manifest_path)
    model = manifest["artifacts"]["model"]
    manifest["artifacts"]["reproduction"] = {
        "status": "in_progress",
        "runs": ["run-001"],
        "depends_on": {
            "model": {
                "version": model["version"],
                "checksum": model["checksum"],
            },
            "literature": {"checksum": "sha256:" + "0" * 64},
            "numerics": {"analyses": ["analysis-001"]},
        },
        "produced_by": "pytest-fixture",
        "timestamp": "2026-06-23T00:00:00Z",
    }
    manifest["history"].append(
        {
            "action": "reproduction_run_complete",
            "repro_id": "run-001",
            "timestamp": "2026-07-13T00:00:00Z",
            "by": "pytest",
        }
    )
    write_json(manifest_path, manifest)

    completed = run_validator(repo_root, project_dir)

    assert completed.returncode != 0
    assert "completion event for 'run-001' requires event_id" in (
        completed.stdout + completed.stderr
    )
