from __future__ import annotations

import stat
from pathlib import Path

import pytest

from scripts import sync_skill_mirrors


def write_file(path: Path, content: bytes, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    path.chmod(mode)


def test_compare_skill_trees_reports_drift_and_ignores_junk(tmp_path: Path) -> None:
    claude_root = tmp_path / ".claude" / "skills"
    agents_root = tmp_path / ".agents" / "skills"
    write_file(claude_root / "demo" / "SKILL.md", b"claude\n")
    write_file(agents_root / "demo" / "SKILL.md", b"agents\n")
    write_file(claude_root / "demo" / "only-claude.txt", b"source\n")
    write_file(claude_root / "__pycache__" / "root-cache.pyc", b"ignored\n")
    write_file(agents_root / "demo" / ".DS_Store", b"ignored\n")
    write_file(agents_root / "demo" / "__pycache__" / "cache.pyc", b"ignored\n")

    failures = sync_skill_mirrors.compare_skill_trees(claude_root, agents_root)

    assert any("demo/SKILL.md: bytes" in failure for failure in failures)
    assert "demo/only-claude.txt: missing in .agents" in failures
    assert all(".DS_Store" not in failure for failure in failures)
    assert all("__pycache__" not in failure for failure in failures)


def test_sync_skill_trees_copies_modes_and_removes_stale_files(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    destination_root = tmp_path / "destination"
    write_file(source_root / "demo" / "SKILL.md", b"current\n")
    write_file(source_root / "demo" / "scripts" / "run.py", b"#!/usr/bin/env python3\n", 0o755)
    write_file(destination_root / "demo" / "SKILL.md", b"stale\n")
    write_file(destination_root / "demo" / "obsolete.txt", b"obsolete\n")
    write_file(destination_root / "removed-skill" / "SKILL.md", b"removed\n")
    write_file(destination_root / "demo" / ".DS_Store", b"ignored\n")

    stats = sync_skill_mirrors.sync_skill_trees(source_root, destination_root)

    assert stats.copied == 2
    assert stats.removed == 2
    assert (destination_root / "demo" / "SKILL.md").read_bytes() == b"current\n"
    copied_mode = stat.S_IMODE(
        (destination_root / "demo" / "scripts" / "run.py").stat().st_mode
    )
    assert copied_mode == 0o755
    assert not (destination_root / "demo" / "obsolete.txt").exists()
    assert not (destination_root / "removed-skill").exists()
    assert (destination_root / "demo" / ".DS_Store").exists()


def test_parse_args_defaults_to_check_and_rejects_two_directions() -> None:
    assert sync_skill_mirrors.parse_args([]).mode == "check"
    assert sync_skill_mirrors.parse_args(["--check"]).mode == "check"
    assert sync_skill_mirrors.parse_args(["--from-claude"]).mode == "from-claude"
    assert sync_skill_mirrors.parse_args(["--from-agents"]).mode == "from-agents"

    with pytest.raises(SystemExit):
        sync_skill_mirrors.parse_args(["--from-claude", "--from-agents"])


def test_publication_helper_is_vendored_to_both_writer_skills(tmp_path: Path) -> None:
    paths = sync_skill_mirrors.shared_helper_paths(
        tmp_path, "_publication_transaction.py"
    )

    assert paths == (
        tmp_path / "scripts" / "_publication_transaction.py",
        tmp_path
        / ".claude"
        / "skills"
        / "hep-numerics"
        / "scripts"
        / "_publication_transaction.py",
        tmp_path
        / ".agents"
        / "skills"
        / "hep-numerics"
        / "scripts"
        / "_publication_transaction.py",
        tmp_path
        / ".claude"
        / "skills"
        / "package-scribe"
        / "scripts"
        / "_publication_transaction.py",
        tmp_path
        / ".agents"
        / "skills"
        / "package-scribe"
        / "scripts"
        / "_publication_transaction.py",
    )
