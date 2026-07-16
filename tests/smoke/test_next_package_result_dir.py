from __future__ import annotations

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def run_next_package_result_dir(
    repo_root: Path,
    base_dir: Path | None,
    *,
    cwd: Path | None = None,
) -> Path:
    script_path = (
        repo_root
        / ".agents"
        / "skills"
        / "package-scribe"
        / "scripts"
        / "next_package_result_dir.py"
    )
    command = [sys.executable, str(script_path)]
    if base_dir is not None:
        command.append(str(base_dir))
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        cwd=cwd,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return Path(result.stdout.strip())


def test_next_package_result_dir_allocates_sequential_directories(
    repo_root,
    tmp_path,
) -> None:
    empty_base = tmp_path / "empty"
    empty_base.mkdir()

    first = run_next_package_result_dir(repo_root, empty_base)

    assert first == empty_base / "workspace" / "package-scribe" / "package-result001"
    assert first.name == "package-result001"
    assert first.is_dir()
    assert not (empty_base / "package-result").exists()

    existing_base = tmp_path / "with-existing"
    existing_results = existing_base / "workspace" / "package-scribe"
    existing_results.mkdir(parents=True)
    (existing_results / "package-result001").touch()

    second = run_next_package_result_dir(repo_root, existing_base)

    assert second == existing_results / "package-result002"
    assert second.name == "package-result002"
    assert second.is_dir()
    assert not (existing_base / "package-result").exists()


def test_next_package_result_dir_defaults_to_current_directory(
    repo_root,
    tmp_path,
) -> None:
    base_dir = tmp_path / "current"
    base_dir.mkdir()

    next_dir = run_next_package_result_dir(repo_root, None, cwd=base_dir)

    assert next_dir == base_dir / "workspace" / "package-scribe" / "package-result001"


def test_next_package_result_dir_uses_repo_workspace_from_project_root(
    repo_root,
    tmp_path,
) -> None:
    fake_repo = tmp_path / "fake-repo"
    project_root = fake_repo / "workspace" / "projects" / "smoke-e2e"
    project_root.mkdir(parents=True)

    next_dir = run_next_package_result_dir(repo_root, project_root)

    assert next_dir == fake_repo / "workspace" / "package-scribe" / "package-result001"
    assert not (fake_repo / "package-result").exists()


def test_concurrent_package_result_allocations_are_unique_and_owned(
    repo_root,
    tmp_path,
) -> None:
    base_dir = tmp_path / "concurrent"
    base_dir.mkdir()

    with ThreadPoolExecutor(max_workers=16) as pool:
        paths = list(
            pool.map(
                lambda _: run_next_package_result_dir(repo_root, base_dir),
                range(16),
            )
        )

    assert len(set(paths)) == 16
    assert {path.name for path in paths} == {
        f"package-result{index:03d}" for index in range(1, 17)
    }
    attempt_ids: set[str] = set()
    for path in paths:
        reservation = json.loads((path / ".reservation.json").read_text(encoding="utf-8"))
        assert reservation["version"] == 1
        assert reservation["kind"] == "package-scribe-interactive-result"
        assert reservation["resource_id"] == path.name
        assert reservation["state"] == "reserved"
        assert reservation["owner"]["pid"] > 0
        attempt_ids.add(reservation["attempt_id"])
    assert len(attempt_ids) == 16


def test_abandoned_package_reservation_is_never_recycled(repo_root, tmp_path) -> None:
    base_dir = tmp_path / "abandoned"
    results_root = base_dir / "workspace" / "package-scribe"
    abandoned = results_root / "package-result001"
    abandoned.mkdir(parents=True)
    # Even a crash before metadata publication occupies the typed ID.
    allocated = run_next_package_result_dir(repo_root, base_dir)
    assert allocated.name == "package-result002"


def test_allocator_rejects_symlinked_package_result_root(repo_root, tmp_path) -> None:
    base_dir = tmp_path / "symlink-root"
    workspace = base_dir / "workspace"
    workspace.mkdir(parents=True)
    outside = tmp_path / "outside-owner"
    outside.mkdir()
    result_root = workspace / "package-scribe"
    result_root.symlink_to(outside, target_is_directory=True)
    script = (
        repo_root
        / ".agents"
        / "skills"
        / "package-scribe"
        / "scripts"
        / "next_package_result_dir.py"
    )

    completed = subprocess.run(
        [sys.executable, str(script), str(base_dir)],
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert "must be a real directory, not a symlink" in completed.stderr
    assert list(outside.iterdir()) == []
    assert result_root.is_symlink()


def test_allocator_uses_lower_free_id_when_a_high_suffix_exists(
    repo_root,
    tmp_path,
) -> None:
    base_dir = tmp_path / "sparse"
    results_root = base_dir / "workspace" / "package-scribe"
    (results_root / "package-result999").mkdir(parents=True)

    allocated = run_next_package_result_dir(repo_root, base_dir)

    assert allocated.name == "package-result001"


def test_package_allocator_json_output_and_bounded_exhaustion(repo_root, tmp_path) -> None:
    script = (
        repo_root
        / ".agents"
        / "skills"
        / "package-scribe"
        / "scripts"
        / "next_package_result_dir.py"
    )
    json_base = tmp_path / "json"
    json_base.mkdir()
    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--format",
            "json",
            "--attempt-id",
            "owned-attempt-001",
            str(json_base),
        ],
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["attempt_id"] == "owned-attempt-001"
    assert Path(payload["path"]).name == "package-result001"

    exhausted_base = tmp_path / "exhausted"
    results_root = exhausted_base / "workspace" / "package-scribe"
    results_root.mkdir(parents=True)
    for index in range(1, 1000):
        (results_root / f"package-result{index:03d}").mkdir()
    exhausted = subprocess.run(
        [sys.executable, str(script), str(exhausted_base)],
        capture_output=True,
        text=True,
    )
    assert exhausted.returncode != 0
    assert "package-result001..package-result999" in exhausted.stderr
    assert not (results_root / "package-result1000").exists()
