from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_workspace_validator_checks_scan_meta_row_count(
    repo_root: Path,
    project_copy_factory,
    read_json,
    write_json,
    tmp_path: Path,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    workspace_root = project_dir.parent
    scan_meta_path = (
        project_dir
        / "numerics"
        / "scan-results"
        / "analysis-001"
        / "scan.meta.json"
    )
    scan_meta = read_json(scan_meta_path)
    scan_meta["n_points"] = scan_meta["n_points"] + 1
    write_json(scan_meta_path, scan_meta)

    result = subprocess.run(
        [
            sys.executable,
            "scripts/validate_workspace_projects.py",
            "--workspace-root",
            str(workspace_root),
            project_dir.name,
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1, result.stdout + result.stderr
    assert (
        "n_allowed + n_excluded + n_skipped" in result.stdout
        or "data row count" in result.stdout
    )


def test_scan_meta_schema_requires_formula_fallbacks(repo_root: Path) -> None:
    from jsonschema import Draft202012Validator

    schema = json.loads(
        (repo_root / "schemas" / "scan-meta.schema.json").read_text(encoding="utf-8")
    )
    example = json.loads(
        (repo_root / "schemas" / "examples" / "scan-meta.example.json").read_text(
            encoding="utf-8"
        )
    )
    example.pop("formula_fallbacks")

    errors = list(Draft202012Validator(schema).iter_errors(example))

    assert errors
