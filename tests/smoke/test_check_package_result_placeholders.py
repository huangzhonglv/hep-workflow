from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def checker_path(repo_root: Path) -> Path:
    return (
        repo_root
        / ".agents"
        / "skills"
        / "package-scribe"
        / "scripts"
        / "check_package_result_placeholders.py"
    )


def run_checker(
    repo_root: Path,
    *args: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(checker_path(repo_root)), *args],
        capture_output=True,
        text=True,
    )


def test_placeholder_checker_accepts_completed_managed_files(
    repo_root,
    tmp_path,
) -> None:
    result_dir = tmp_path / "complete"
    result_dir.mkdir()
    (result_dir / "request.md").write_text("Complete request.\n", encoding="utf-8")
    (result_dir / "result-meta.json").write_text("{}\n", encoding="utf-8")

    result = run_checker(repo_root, str(result_dir))

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == (
        f"OK: no unresolved template placeholders found in {result_dir}"
    )
    assert result.stderr == ""


def test_placeholder_checker_reports_single_file_matches_without_filename(
    repo_root,
    tmp_path,
) -> None:
    result_dir = tmp_path / "single-file"
    result_dir.mkdir()
    (result_dir / "request.md").write_text(
        "Ready\n{{ STATUS_SUMMARY }}\n",
        encoding="utf-8",
    )

    result = run_checker(repo_root, str(result_dir))

    assert result.returncode == 1
    assert result.stdout == "2:{{ STATUS_SUMMARY }}\n"
    assert (
        f"ERROR: unresolved template placeholders found in {result_dir}"
        in result.stderr
    )


def test_placeholder_checker_prefixes_filename_for_multiple_managed_files(
    repo_root,
    tmp_path,
) -> None:
    result_dir = tmp_path / "multiple-files"
    result_dir.mkdir()
    (result_dir / "request.md").write_text("Ready\n", encoding="utf-8")
    summary_path = result_dir / "result-summary.md"
    summary_path.write_text("Summary\n{{NOTES}}\n", encoding="utf-8")

    result = run_checker(repo_root, str(result_dir))

    assert result.returncode == 1
    assert result.stdout == f"{summary_path}:2:{{{{NOTES}}}}\n"


def test_placeholder_checker_rejects_unfinalized_input_provenance(
    repo_root,
    tmp_path,
) -> None:
    result_dir = tmp_path / "unfinalized-provenance"
    result_dir.mkdir()
    template = (
        repo_root
        / ".agents"
        / "skills"
        / "package-scribe"
        / "templates"
        / "result-meta.json.tmpl"
    ).read_text(encoding="utf-8")
    meta_path = result_dir / "result-meta.json"
    meta_path.write_text(template, encoding="utf-8")

    result = run_checker(repo_root, str(result_dir))

    assert result.returncode == 1
    assert "{{input_provenance_status}}" in result.stdout
    assert (
        f"ERROR: unresolved template placeholders found in {result_dir}"
        in result.stderr
    )


def test_placeholder_checker_rejects_missing_or_empty_result_directory(
    repo_root,
    tmp_path,
) -> None:
    missing_dir = tmp_path / "missing"
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()

    missing = run_checker(repo_root, str(missing_dir))
    empty = run_checker(repo_root, str(empty_dir))

    assert missing.returncode == 1
    assert f"result directory not found: {missing_dir}" in missing.stderr
    assert empty.returncode == 1
    assert f"no managed result files found under: {empty_dir}" in empty.stderr


def test_placeholder_checker_rejects_invalid_cli_usage(repo_root) -> None:
    result = run_checker(repo_root)

    assert result.returncode == 1
    assert "usage:" in result.stderr.lower()
