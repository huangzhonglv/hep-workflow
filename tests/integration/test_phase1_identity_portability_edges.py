from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
import subprocess
import sys


def _snapshot(root: Path) -> tuple[tuple[str, str, str], ...]:
    entries: list[tuple[str, str, str]] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            entries.append((relative, "symlink", str(path.readlink())))
        elif path.is_dir():
            entries.append((relative, "directory", ""))
        else:
            entries.append(
                (
                    relative,
                    "file",
                    hashlib.sha256(path.read_bytes()).hexdigest(),
                )
            )
    return tuple(entries)


def _validate_scan_config(
    repo_root: Path,
    project_dir: Path,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(
                repo_root
                / ".claude"
                / "skills"
                / "hep-numerics"
                / "scripts"
                / "validate_scan_config.py"
            ),
            "--project-dir",
            str(project_dir),
            "--analysis-id",
            "analysis-001",
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )


def test_calculation_graph_does_not_require_absent_optional_benchmarks(
    tmp_path: Path,
    project_copy_factory,
    rebind_calculation_result,
    repo_root: Path,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    (project_dir / "model" / "benchmarks.json").unlink()
    rebind_calculation_result(project_dir)

    completed = _validate_scan_config(repo_root, project_dir)

    assert completed.returncode in {0, 2}, completed.stdout + completed.stderr
    assert "[FAIL]" not in completed.stdout
    assert "missing required roles: ['benchmarks']" not in (
        completed.stdout + completed.stderr
    )


def test_existing_optional_benchmarks_must_be_bound_by_calculation_graph(
    tmp_path: Path,
    project_copy_factory,
    rebind_calculation_result,
    repo_root: Path,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    benchmark_path = project_dir / "model" / "benchmarks.json"
    benchmark_bytes = benchmark_path.read_bytes()
    benchmark_path.unlink()
    rebind_calculation_result(project_dir)
    benchmark_path.write_bytes(benchmark_bytes)

    completed = _validate_scan_config(repo_root, project_dir)

    combined = completed.stdout + completed.stderr
    assert completed.returncode != 0
    assert "benchmarks" in combined
    assert (
        "missing expected entries" in combined
        or "missing required roles" in combined
    )


def test_init_analysis_exhausted_identifier_space_fails_without_side_effects(
    tmp_path: Path,
    project_copy_factory,
    repo_root: Path,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    for subdir in ("scan-configs", "scan-results", "figures"):
        target = project_dir / "numerics" / subdir
        shutil.rmtree(target)
        target.mkdir(parents=True)
    scan_configs_dir = project_dir / "numerics" / "scan-configs"
    for number in range(1, 1000):
        (scan_configs_dir / f"analysis-{number:03d}.json").write_text(
            "{}\n", encoding="utf-8"
        )

    before = _snapshot(project_dir)
    completed = subprocess.run(
        [
            sys.executable,
            str(
                repo_root
                / ".claude"
                / "skills"
                / "hep-numerics"
                / "scripts"
                / "init_analysis.py"
            ),
            "--project-dir",
            str(project_dir),
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )

    combined = completed.stdout + completed.stderr
    assert completed.returncode != 0
    assert "analysis-001..analysis-999" in combined
    assert not (scan_configs_dir / "analysis-1000.json").exists()
    assert _snapshot(project_dir) == before
