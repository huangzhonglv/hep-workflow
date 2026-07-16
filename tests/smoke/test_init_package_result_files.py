from __future__ import annotations

import importlib.util
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

from scripts import _publication_transaction


TEST_WOLFRAMSCRIPT = "/opt/test-wolfram/bin/wolframscript"


def initializer_path(repo_root: Path) -> Path:
    return (
        repo_root
        / ".agents"
        / "skills"
        / "package-scribe"
        / "scripts"
        / "init_package_result_files.py"
    )


def load_initializer_module(repo_root: Path):
    path = initializer_path(repo_root)
    spec = importlib.util.spec_from_file_location(
        "package_result_initializer_cleanup_test",
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(path.parent))
    return module


def run_initializer(
    repo_root: Path,
    *args: str,
    env_overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["WOLFRAMSCRIPT_BIN"] = TEST_WOLFRAMSCRIPT
    env.update(env_overrides or {})
    return subprocess.run(
        [sys.executable, str(initializer_path(repo_root)), *args],
        capture_output=True,
        text=True,
        env=env,
    )


def output_paths(result: subprocess.CompletedProcess[str]) -> list[Path]:
    return [Path(line) for line in result.stdout.splitlines() if line]


def posix_command(instructions: str) -> str:
    marker = "## POSIX Shell Command\n\n```bash\n"
    assert marker in instructions
    command, separator, _ = instructions.split(marker, 1)[1].partition("\n```")
    assert separator
    assert command
    return command


def batch_allocation(result: subprocess.CompletedProcess[str]) -> dict[str, object]:
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def make_batch_task(tmp_path: Path, task_id: str = "task-001") -> Path:
    project_dir = tmp_path / "workspace" / "projects" / "package-project"
    task_dir = project_dir / "calculations" / task_id
    task_dir.mkdir(parents=True)
    (project_dir / "manifest.json").write_text(
        '{"manifest_version": 2}\n',
        encoding="utf-8",
    )
    return task_dir


def test_interactive_initializer_renders_deterministic_fields(
    repo_root,
    tmp_path,
) -> None:
    result_dir = tmp_path / "result & one"

    result = run_initializer(repo_root, str(result_dir))

    assert result.returncode == 0, result.stdout + result.stderr
    assert output_paths(result) == [
        result_dir / "request.md",
        result_dir / "result-summary.md",
        result_dir / "run-instructions.md",
    ]
    assert not (result_dir / "result-python.py").exists()
    assert not (result_dir / "result-meta.json").exists()

    request = (result_dir / "request.md").read_text(encoding="utf-8")
    assert "{{GENERATED_AT}}" not in request
    assert "{{RESULT_DIR}}" not in request
    assert f"- Result directory: `{result_dir}`" in request
    assert re.search(
        r"- Generated at: `\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} [+-]\d{4}`",
        request,
    )
    assert "{{USER_REQUEST}}" in request

    instructions = (result_dir / "run-instructions.md").read_text(encoding="utf-8")
    assert "{{RESULT_WL_PATH}}" not in instructions
    assert "{{RUN_COMMAND}}" not in instructions
    command = posix_command(instructions)
    assert shlex.split(command) == [
        TEST_WOLFRAMSCRIPT,
        "-file",
        str(result_dir / "result.wl"),
    ]
    assert command == shlex.join(
        [TEST_WOLFRAMSCRIPT, "-file", str(result_dir / "result.wl")]
    )
    assert command.split(" -file ", 1)[1] != str(result_dir / "result.wl")
    assert "WOLFRAMSCRIPT_BIN" in instructions
    assert "It is not a PowerShell or `cmd.exe` command" in instructions
    assert "directly to the process API with no shell" in instructions
    assert "/Applications/Wolfram.app" not in instructions


def test_posix_command_round_trips_and_executes_adversarial_paths(
    repo_root,
    tmp_path,
) -> None:
    executable_dir = tmp_path / "tool & dollar$ single' double\" unicode-λ"
    executable_dir.mkdir()
    executable = executable_dir / "wolframscript fake"
    executable.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$#\" \"$1\" \"$2\"\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    result_dir = tmp_path / "result & dollar$ single' double\" unicode-δ"

    result = run_initializer(
        repo_root,
        str(result_dir),
        env_overrides={"WOLFRAMSCRIPT_BIN": str(executable)},
    )

    assert result.returncode == 0, result.stdout + result.stderr
    instructions = (result_dir / "run-instructions.md").read_text(encoding="utf-8")
    command = posix_command(instructions)
    expected_argv = [str(executable), "-file", str(result_dir / "result.wl")]
    assert shlex.split(command) == expected_argv

    executed = subprocess.run(
        ["/bin/sh", "-c", command],
        capture_output=True,
        text=True,
    )
    assert executed.returncode == 0, executed.stdout + executed.stderr
    assert executed.stdout.splitlines() == [
        "2",
        "-file",
        str(result_dir / "result.wl"),
    ]


def test_interactive_initializer_preserves_existing_files(repo_root, tmp_path) -> None:
    result_dir = tmp_path / "existing"
    result_dir.mkdir()
    request_path = result_dir / "request.md"
    request_path.write_text("keep this request\n", encoding="utf-8")

    result = run_initializer(repo_root, str(result_dir))

    assert result.returncode == 0, result.stdout + result.stderr
    assert request_path.read_text(encoding="utf-8") == "keep this request\n"
    assert f"skip existing: {request_path}" in result.stderr
    assert output_paths(result) == [
        result_dir / "result-summary.md",
        result_dir / "run-instructions.md",
    ]


def test_batch_initializer_stages_templates_without_overwriting_last_good(
    repo_root,
    tmp_path,
) -> None:
    result_dir = make_batch_task(tmp_path)
    (result_dir / "request.md").write_text("stale\n", encoding="utf-8")

    result = run_initializer(
        repo_root,
        "--task-dir",
        str(result_dir),
        "--format",
        "json",
    )

    allocation = batch_allocation(result)
    attempt_dir = Path(str(allocation["path"]))
    assert allocation["final_task_dir"] == str(result_dir)
    assert allocation["output_paths"] == [
        str(attempt_dir / "request.md"),
        str(attempt_dir / "result-summary.md"),
        str(attempt_dir / "run-instructions.md"),
        str(attempt_dir / "result-python.py"),
        str(attempt_dir / "result-meta.json"),
    ]
    assert (result_dir / "request.md").read_text(encoding="utf-8") == "stale\n"

    templates = repo_root / ".agents" / "skills" / "package-scribe" / "templates"
    assert (attempt_dir / "result-python.py").read_bytes() == (
        templates / "result-python.py.tmpl"
    ).read_bytes()
    assert (attempt_dir / "result-meta.json").read_bytes() == (
        templates / "result-meta.json.tmpl"
    ).read_bytes()
    reservation = json.loads(
        (attempt_dir / ".reservation.json").read_text(encoding="utf-8")
    )
    assert reservation["attempt_id"] == allocation["attempt_id"]
    assert reservation["state"] == "initialized"


def test_blocked_batch_initializer_creates_only_required_markdown(
    repo_root,
    tmp_path,
) -> None:
    result_dir = make_batch_task(tmp_path)

    result = run_initializer(
        repo_root,
        "--task-dir",
        str(result_dir),
        "--blocked",
        "--format",
        "json",
    )

    allocation = batch_allocation(result)
    attempt_dir = Path(str(allocation["path"]))
    assert allocation["output_paths"] == [
        str(attempt_dir / "request.md"),
        str(attempt_dir / "result-summary.md"),
    ]
    assert list(result_dir.iterdir()) == []
    assert sorted(path.name for path in attempt_dir.iterdir()) == [
        ".reservation.json",
        "request.md",
        "result-summary.md",
    ]


def test_initializer_rejects_ambiguous_or_missing_output_directory(
    repo_root,
    tmp_path,
) -> None:
    missing = run_initializer(repo_root)
    ambiguous = run_initializer(
        repo_root,
        str(tmp_path / "interactive"),
        "--task-dir",
        str(tmp_path / "batch"),
    )

    assert missing.returncode == 1
    assert "usage:" in missing.stderr.lower()
    assert ambiguous.returncode == 1
    assert "provide exactly one" in ambiguous.stderr


def test_initializer_uses_portable_wolfram_resolution(repo_root) -> None:
    for skill_tree in (".claude", ".agents"):
        script = (
            repo_root
            / skill_tree
            / "skills"
            / "package-scribe"
            / "scripts"
            / "init_package_result_files.py"
        ).read_text(encoding="utf-8")
        assert "WOLFRAMSCRIPT_BIN" in script
        assert "wolframscript_argv" in script
        assert "shlex.join" in script
        assert 'f"{wolframscript_bin} -file {result_wl_path}"' not in script
        assert "/Applications/Wolfram.app" not in script


def test_batch_initializer_rolls_back_every_file_after_injected_failure(
    repo_root,
    tmp_path,
) -> None:
    result_dir = make_batch_task(tmp_path)
    managed_names = (
        "request.md",
        "result-summary.md",
        "run-instructions.md",
        "result-python.py",
        "result-meta.json",
    )
    for index, name in enumerate(managed_names):
        (result_dir / name).write_bytes(f"sentinel-{index}\n".encode())
    before = {name: (result_dir / name).read_bytes() for name in managed_names}

    failed = run_initializer(
        repo_root,
        "--task-dir",
        str(result_dir),
        env_overrides={
            "HEP_WORKFLOW_TEST_FAIL_PACKAGE_INIT_AFTER": "result-summary.md"
        },
    )

    assert failed.returncode != 0
    assert "injected package initializer failure" in failed.stderr
    assert {name: (result_dir / name).read_bytes() for name in managed_names} == before
    assert not [path for path in result_dir.iterdir() if path.name.startswith(".")]


def test_batch_initializer_refuses_to_replace_a_managed_directory(
    repo_root,
    tmp_path,
) -> None:
    result_dir = make_batch_task(tmp_path)
    protected = result_dir / "request.md"
    protected.mkdir(parents=True)
    (protected / "user-data.txt").write_text("preserve\n", encoding="utf-8")

    result = run_initializer(repo_root, "--task-dir", str(result_dir))

    assert result.returncode != 0
    assert "must be a regular file" in result.stderr
    assert (protected / "user-data.txt").read_text(encoding="utf-8") == "preserve\n"


def test_interactive_allocator_symlink_cannot_escape_or_bypass_token(
    repo_root,
    tmp_path,
) -> None:
    results_root = tmp_path / "workspace" / "package-scribe"
    results_root.mkdir(parents=True)
    outside = tmp_path / "external-owner"
    outside.mkdir()
    result_dir = results_root / "package-result001"
    result_dir.symlink_to(outside, target_is_directory=True)

    result = run_initializer(repo_root, str(result_dir))

    assert result.returncode != 0
    assert "must not be a symlink" in result.stderr
    assert list(outside.iterdir()) == []
    assert result_dir.is_symlink()


def test_batch_nested_manifest_cannot_split_project_lock_domain(
    repo_root,
    tmp_path,
) -> None:
    result_dir = make_batch_task(tmp_path)
    project_dir = result_dir.parent.parent
    (result_dir / "manifest.json").write_text("{}\n", encoding="utf-8")

    with _publication_transaction.publication_lock(project_dir, "test-owner"):
        process = subprocess.Popen(
            [
                sys.executable,
                str(initializer_path(repo_root)),
                "--task-dir",
                str(result_dir),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        time.sleep(0.1)
        assert process.poll() is None, (
            "batch initializer bypassed the project lock via nested manifest.json"
        )

    stdout, stderr = process.communicate(timeout=10)
    assert process.returncode == 0, stdout + stderr
    initialized = [Path(line) for line in stdout.splitlines() if line]
    assert initialized
    assert initialized[0].parent.parent == project_dir / ".hep-workflow-package-attempts"
    assert initialized[0].name == "request.md"
    assert not (result_dir / "request.md").exists()


def test_failed_allocated_result_requires_its_attempt_token_to_resume(
    repo_root,
    tmp_path,
) -> None:
    base_dir = tmp_path / "allocated"
    base_dir.mkdir()
    allocator = (
        repo_root
        / ".agents"
        / "skills"
        / "package-scribe"
        / "scripts"
        / "next_package_result_dir.py"
    )
    allocated = subprocess.run(
        [sys.executable, str(allocator), "--format", "json", str(base_dir)],
        capture_output=True,
        text=True,
    )
    assert allocated.returncode == 0, allocated.stdout + allocated.stderr
    allocation = json.loads(allocated.stdout)
    result_dir = Path(allocation["path"])
    attempt_id = allocation["attempt_id"]

    failed = run_initializer(
        repo_root,
        str(result_dir),
        "--attempt-id",
        attempt_id,
        env_overrides={"HEP_WORKFLOW_TEST_FAIL_PACKAGE_INIT_AFTER": "request.md"},
    )
    assert failed.returncode != 0
    reservation_path = result_dir / ".reservation.json"
    reservation = json.loads(reservation_path.read_text(encoding="utf-8"))
    assert reservation["state"] == "failed"
    assert not (result_dir / "request.md").exists()
    failed_reservation_bytes = reservation_path.read_bytes()

    unauthenticated = run_initializer(repo_root, str(result_dir))
    wrong_owner = run_initializer(
        repo_root,
        str(result_dir),
        "--attempt-id",
        "not-the-owner",
    )
    assert unauthenticated.returncode != 0
    assert wrong_owner.returncode != 0
    assert reservation_path.read_bytes() == failed_reservation_bytes
    assert json.loads(reservation_path.read_text(encoding="utf-8"))["attempt_id"] == attempt_id
    assert not (result_dir / "request.md").exists()

    resumed = run_initializer(
        repo_root,
        str(result_dir),
        "--attempt-id",
        attempt_id,
    )
    assert resumed.returncode == 0, resumed.stdout + resumed.stderr
    assert (result_dir / "request.md").is_file()
    assert json.loads(reservation_path.read_text(encoding="utf-8"))["state"] == "initialized"


def test_committed_initializer_cleanup_warning_is_success_without_retry(
    repo_root,
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    initializer = load_initializer_module(repo_root)
    result_dir = tmp_path / "committed-cleanup"
    original_commit = initializer.PublicationTransaction.commit

    def commit_then_report_pending_cleanup(self, *args, **kwargs):
        original_commit(self, *args, **kwargs)
        raise initializer.TransactionCommittedCleanupError(
            self.transaction_id,
            OSError("injected cleanup interruption"),
        )

    monkeypatch.setattr(
        initializer.PublicationTransaction,
        "commit",
        commit_then_report_pending_cleanup,
    )

    assert initializer.main([str(result_dir)]) == 0
    warning = capsys.readouterr().err
    assert "committed successfully" in warning
    assert "Do not retry" in warning
    assert "injected cleanup interruption" in warning
    assert (result_dir / "request.md").is_file()
    assert (result_dir / "result-summary.md").is_file()
    assert (result_dir / "run-instructions.md").is_file()
