from __future__ import annotations

import stat
import subprocess
from pathlib import Path

import pytest


MAKE_TARGET_COMMANDS = {
    "validate": [
        "python3 scripts/validate_examples.py",
        "python3 scripts/validate_workspace_projects.py",
        "python3 -m pytest -q",
    ],
    "test": ["python3 -m pytest -q"],
    "contract": ["python3 -m pytest -q tests/contract"],
    "e2e": ["python3 -m pytest -q tests/e2e --run-e2e"],
}


@pytest.mark.parametrize(("target", "expected"), MAKE_TARGET_COMMANDS.items())
def test_make_targets_expand_to_canonical_commands(
    repo_root: Path,
    target: str,
    expected: list[str],
) -> None:
    result = subprocess.run(
        ["make", "-n", "PYTHON=python3", target],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines() == expected


def test_ci_runs_mirror_check_and_canonical_gate_on_supported_pythons(
    repo_root: Path,
) -> None:
    workflow = (repo_root / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )

    assert 'python-version: ["3.11", "3.12", "3.13"]' in workflow
    assert "python-version: ${{ matrix.python-version }}" in workflow
    commands = [
        "python scripts/sync_skill_mirrors.py --check",
        "python -m pip install -r requirements-dev.txt",
        "python scripts/validate_examples.py",
        "python scripts/validate_workspace_projects.py",
        "python -m pytest -q",
    ]
    positions = [workflow.index(command) for command in commands]
    assert positions == sorted(positions)


def test_smoke_wrappers_delegate_without_mutating_the_environment(
    repo_root: Path,
) -> None:
    baseline_path = repo_root / "scripts" / "smoke_hep_numerics.sh"
    e2e_path = repo_root / "scripts" / "smoke_hep_numerics_e2e.sh"
    baseline = baseline_path.read_text(encoding="utf-8")
    e2e = e2e_path.read_text(encoding="utf-8")

    assert 'exec make -C "$repo_root" validate' in baseline
    assert 'exec make -C "$repo_root" validate e2e' in e2e
    assert "command -v wolframscript" in e2e
    for source in (baseline, e2e):
        assert "-m venv" not in source
        assert "pip install" not in source
        assert "source .venv" not in source
        assert "Prompt 8" not in source

    assert baseline_path.stat().st_mode & stat.S_IXUSR
    assert e2e_path.stat().st_mode & stat.S_IXUSR


def test_new_skill_checklist_is_linked_from_the_mirror_contract(
    repo_root: Path,
) -> None:
    contributing = (repo_root / "CONTRIBUTING.md").read_text(encoding="utf-8")
    mirror_contract = (
        repo_root / "docs" / "contracts" / "mirror-invariants.md"
    ).read_text(encoding="utf-8")

    assert "## Adding a new skill" in contributing
    assert "[automated" in contributing
    assert "[mixed]" in contributing
    assert "make validate" in contributing
    assert "../../CONTRIBUTING.md#adding-a-new-skill" in mirror_contract
