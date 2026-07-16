from __future__ import annotations

import json
import shutil
import subprocess
import sys
from copy import deepcopy
from pathlib import Path


def _run_refresh(
    repo_root: Path,
    project_dir: Path,
    *,
    write: bool = False,
) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        str(repo_root / "scripts" / "refresh_numerics_staleness.py"),
        "--project-dir",
        str(project_dir),
    ]
    if write:
        command.append("--write")
    return subprocess.run(command, capture_output=True, text=True)


def _copy_project(repo_root: Path, tmp_path: Path) -> Path:
    source = (
        repo_root
        / "tests"
        / "fixtures"
        / "workspace-projects"
        / "numerics-contract"
    )
    destination = tmp_path / "workspace" / "projects" / "numerics-contract"
    shutil.copytree(source, destination)
    return destination


def test_refresh_is_read_only_by_default_and_idempotently_publishes_stale(
    repo_root: Path,
    tmp_path: Path,
) -> None:
    project_dir = _copy_project(repo_root, tmp_path)
    constraints_path = project_dir / "constraints" / "constraints-data.json"
    constraints = json.loads(constraints_path.read_text(encoding="utf-8"))
    constraints["constraints"][0]["notes"] += " Upstream revision."
    constraints_path.write_text(
        json.dumps(constraints, indent=2) + "\n",
        encoding="utf-8",
    )
    manifest_path = project_dir / "manifest.json"
    before_bytes = manifest_path.read_bytes()
    before = json.loads(before_bytes)

    diagnosed = _run_refresh(repo_root, project_dir)

    assert diagnosed.returncode == 1
    assert "NEEDS REFRESH" in diagnosed.stdout
    assert manifest_path.read_bytes() == before_bytes

    published = _run_refresh(repo_root, project_dir, write=True)

    assert published.returncode == 0, published.stdout + published.stderr
    assert "REFRESHED" in published.stdout
    after_bytes = manifest_path.read_bytes()
    after = json.loads(after_bytes)
    assert after["artifacts"]["numerics"]["status"] == "stale"
    assert after["artifacts"]["numerics"]["analyses"][0]["status"] == "stale"
    assert (
        after["artifacts"]["numerics"]["analyses"][0]["depends_on"]
        == before["artifacts"]["numerics"]["analyses"][0]["depends_on"]
    )
    assert after["history"] == before["history"]

    repeated = _run_refresh(repo_root, project_dir, write=True)

    assert repeated.returncode == 0, repeated.stdout + repeated.stderr
    assert "OK" in repeated.stdout
    assert manifest_path.read_bytes() == after_bytes

    validated = subprocess.run(
        [
            sys.executable,
            str(repo_root / "scripts" / "validate_workspace_projects.py"),
            project_dir.name,
            "--workspace-root",
            str(project_dir.parent),
        ],
        capture_output=True,
        text=True,
    )
    assert validated.returncode == 0, validated.stdout + validated.stderr


def test_refresh_rejects_manifest_model_metadata_that_does_not_match_live_bytes(
    repo_root: Path,
    tmp_path: Path,
) -> None:
    project_dir = _copy_project(repo_root, tmp_path)
    model_path = project_dir / "model" / "model-spec.json"
    model = json.loads(model_path.read_text(encoding="utf-8"))
    model["tags"] = [*model["tags"], "unpublished_model_mutation"]
    model_path.write_text(json.dumps(model, indent=2) + "\n", encoding="utf-8")
    manifest_path = project_dir / "manifest.json"
    before = manifest_path.read_bytes()

    result = _run_refresh(repo_root, project_dir, write=True)

    assert result.returncode != 0
    assert "checksum does not match" in result.stderr
    assert manifest_path.read_bytes() == before


def test_refresh_rejects_constraints_payload_for_a_different_model_version(
    repo_root: Path,
    tmp_path: Path,
) -> None:
    project_dir = _copy_project(repo_root, tmp_path)
    constraints_path = project_dir / "constraints" / "constraints-data.json"
    constraints = json.loads(constraints_path.read_text(encoding="utf-8"))
    constraints["model_version"] = "v2"
    constraints_path.write_text(
        json.dumps(constraints, indent=2) + "\n",
        encoding="utf-8",
    )
    manifest_path = project_dir / "manifest.json"
    before = manifest_path.read_bytes()

    result = _run_refresh(repo_root, project_dir, write=True)

    assert result.returncode != 0
    assert "model_version does not match the active model version" in result.stderr
    assert manifest_path.read_bytes() == before


def test_refresh_rejects_corrupt_historical_scan_evidence_before_writing(
    repo_root: Path,
    tmp_path: Path,
) -> None:
    project_dir = _copy_project(repo_root, tmp_path)
    constraints_path = project_dir / "constraints" / "constraints-data.json"
    constraints = json.loads(constraints_path.read_text(encoding="utf-8"))
    constraints["constraints"][0]["notes"] += " Upstream revision."
    constraints_path.write_text(
        json.dumps(constraints, indent=2) + "\n",
        encoding="utf-8",
    )
    scan_path = (
        project_dir
        / "numerics"
        / "scan-results"
        / "analysis-001"
        / "scan.csv"
    )
    scan_path.write_bytes(scan_path.read_bytes() + b"\n")
    manifest_path = project_dir / "manifest.json"
    before = deepcopy(json.loads(manifest_path.read_text(encoding="utf-8")))

    result = _run_refresh(repo_root, project_dir, write=True)

    assert result.returncode != 0
    assert "historical evidence is invalid" in result.stderr
    assert json.loads(manifest_path.read_text(encoding="utf-8")) == before
