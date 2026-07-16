from __future__ import annotations

import difflib
from pathlib import Path


IGNORED_NAMES = {
    ".DS_Store",
}
IGNORED_PARTS = {
    "__pycache__",
}
IGNORED_SUFFIXES = {
    ".pyc",
}


def _is_comparable_file(path: Path) -> bool:
    if path.name in IGNORED_NAMES:
        return False
    if any(part in IGNORED_PARTS for part in path.parts):
        return False
    if path.suffix in IGNORED_SUFFIXES:
        return False
    return path.is_file()


def _collect_files(root: Path) -> dict[Path, Path]:
    return {
        path.relative_to(root): path
        for path in root.rglob("*")
        if _is_comparable_file(path)
    }


def _diff_summary(left: bytes, right: bytes) -> str:
    if len(left) != len(right):
        prefix = f"bytes {len(left)} != {len(right)}"
    else:
        prefix = f"bytes {len(left)}"

    try:
        left_text = left.decode("utf-8")
        right_text = right.decode("utf-8")
    except UnicodeDecodeError:
        return prefix

    diff_lines = list(
        difflib.unified_diff(
            left_text.splitlines(),
            right_text.splitlines(),
            lineterm="",
        )
    )
    added = sum(
        1 for line in diff_lines if line.startswith("+") and not line.startswith("+++")
    )
    removed = sum(
        1 for line in diff_lines if line.startswith("-") and not line.startswith("---")
    )
    return f"{prefix}; +{added}/-{removed} lines"


def test_all_skill_trees_match_between_claude_and_agents(repo_root: Path) -> None:
    claude_root = repo_root / ".claude" / "skills"
    agents_root = repo_root / ".agents" / "skills"

    claude_skills = sorted(
        path.name
        for path in claude_root.iterdir()
        if path.is_dir() and path.name not in IGNORED_PARTS
    )
    agents_skills = sorted(
        path.name
        for path in agents_root.iterdir()
        if path.is_dir() and path.name not in IGNORED_PARTS
    )

    assert claude_skills == agents_skills

    failures: list[str] = []
    for skill_name in claude_skills:
        claude_files = _collect_files(claude_root / skill_name)
        agents_files = _collect_files(agents_root / skill_name)

        for relative_path in sorted(set(claude_files) | set(agents_files)):
            claude_path = claude_files.get(relative_path)
            agents_path = agents_files.get(relative_path)
            label = f"{skill_name}/{relative_path.as_posix()}"
            if claude_path is None:
                failures.append(f"{label}: missing in .claude")
                continue
            if agents_path is None:
                failures.append(f"{label}: missing in .agents")
                continue

            claude_bytes = claude_path.read_bytes()
            agents_bytes = agents_path.read_bytes()
            if claude_bytes != agents_bytes:
                failures.append(
                    f"{label}: {_diff_summary(claude_bytes, agents_bytes)}"
                )

    assert not failures, "Skill mirror mismatch:\n" + "\n".join(failures)
