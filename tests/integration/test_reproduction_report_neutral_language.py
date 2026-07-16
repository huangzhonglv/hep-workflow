from __future__ import annotations

from pathlib import Path

from tests.integration.test_compare_to_reference_minimal import (
    _run_compare,
    _smoke_project,
    _write_synthetic_scan,
)


FORBIDDEN_REPORT_PHRASES = [
    "very close",
    "close enough",
    "approximately matches",
    "perfectly reproduces",
    "successful reproduction",
]


def lint_report_text(text: str) -> list[str]:
    return [phrase for phrase in FORBIDDEN_REPORT_PHRASES if phrase in text]


def test_report_lint_accepts_clean_report() -> None:
    clean = (
        "# Reproduction Diagnostic\n\n"
        "- verdict: `needs_human_review`\n"
        "- metrics: `{\"max_relative_error\": 0.1}`\n"
    )
    assert lint_report_text(clean) == []


def test_report_lint_catches_close_language() -> None:
    assert lint_report_text("The result is very close; mark it as pass.") == ["very close"]


def test_report_lint_catches_perfect_reproduction_claim() -> None:
    assert lint_report_text("This run perfectly reproduces the paper figure.") == ["perfectly reproduces"]


def test_generated_compare_diagnostic_uses_neutral_language(
    tmp_path,
    project_copy_factory,
    smoke_e2e_fixture_path: Path,
    repo_root: Path,
) -> None:
    project_dir = _smoke_project(tmp_path, project_copy_factory, smoke_e2e_fixture_path)
    _write_synthetic_scan(project_dir, repo_root)

    result = _run_compare(repo_root, project_dir)
    assert result.returncode == 0, result.stdout + result.stderr

    diagnostic = (
        project_dir / "reproduction" / "runs" / "run-001" / "diagnostic.md"
    )
    assert diagnostic.exists()
    assert lint_report_text(diagnostic.read_text(encoding="utf-8")) == []
