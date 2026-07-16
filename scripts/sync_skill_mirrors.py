#!/usr/bin/env python3
"""Check or synchronize the two mirrored skill installation trees."""

from __future__ import annotations

import argparse
import difflib
import shutil
import stat
import sys
from dataclasses import dataclass
from pathlib import Path


IGNORED_NAMES = frozenset({".DS_Store"})
IGNORED_PARTS = frozenset({"__pycache__"})
IGNORED_SUFFIXES = frozenset({".pyc"})
SHARED_HELPER_SKILLS = {
    "_strict_json.py": ("hep-numerics",),
    "_identity.py": ("hep-numerics",),
    "_dependency_graph.py": ("hep-numerics",),
    "_workflow_dependencies.py": ("hep-numerics",),
    "_scan_artifact_validation.py": ("hep-numerics",),
    "_publication_transaction.py": ("hep-numerics", "package-scribe"),
}


def shared_helper_paths(repo_root: Path, helper_name: str) -> tuple[Path, ...]:
    skills = SHARED_HELPER_SKILLS.get(helper_name)
    if skills is None:
        raise KeyError(f"unknown shared helper: {helper_name}")
    return (
        repo_root / "scripts" / helper_name,
        *(
            repo_root / tree / "skills" / skill / "scripts" / helper_name
            for skill in skills
            for tree in (".claude", ".agents")
        ),
    )


@dataclass(frozen=True)
class SyncStats:
    copied: int
    removed: int


def is_comparable_file(path: Path) -> bool:
    if path.name in IGNORED_NAMES:
        return False
    if any(part in IGNORED_PARTS for part in path.parts):
        return False
    if path.suffix in IGNORED_SUFFIXES:
        return False
    return path.is_file()


def collect_files(root: Path) -> dict[Path, Path]:
    return {
        path.relative_to(root): path
        for path in root.rglob("*")
        if is_comparable_file(path)
    }


def collect_skill_names(root: Path) -> set[str]:
    return {
        path.name
        for path in root.iterdir()
        if path.is_dir() and path.name not in IGNORED_PARTS
    }


def diff_summary(left: bytes, right: bytes) -> str:
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


def compare_skill_trees(
    claude_root: Path,
    agents_root: Path,
) -> list[str]:
    for root in (claude_root, agents_root):
        if not root.is_dir():
            raise FileNotFoundError(f"skill root not found: {root}")

    failures: list[str] = []
    claude_skills = collect_skill_names(claude_root)
    agents_skills = collect_skill_names(agents_root)
    for skill_name in sorted(claude_skills - agents_skills):
        failures.append(f"{skill_name}/: missing in .agents")
    for skill_name in sorted(agents_skills - claude_skills):
        failures.append(f"{skill_name}/: missing in .claude")

    claude_files = collect_files(claude_root)
    agents_files = collect_files(agents_root)
    for relative_path in sorted(set(claude_files) | set(agents_files)):
        claude_path = claude_files.get(relative_path)
        agents_path = agents_files.get(relative_path)
        label = relative_path.as_posix()
        if claude_path is None:
            failures.append(f"{label}: missing in .claude")
            continue
        if agents_path is None:
            failures.append(f"{label}: missing in .agents")
            continue

        claude_bytes = claude_path.read_bytes()
        agents_bytes = agents_path.read_bytes()
        if claude_bytes != agents_bytes:
            failures.append(f"{label}: {diff_summary(claude_bytes, agents_bytes)}")

    return failures


def compare_shared_helpers(repo_root: Path) -> list[str]:
    """Check helpers intentionally vendored into standalone skill installs."""

    failures: list[str] = []
    for helper_name in SHARED_HELPER_SKILLS:
        paths = shared_helper_paths(repo_root, helper_name)
        missing = [path for path in paths if not path.is_file()]
        failures.extend(
            f"shared helper missing: {path.relative_to(repo_root).as_posix()}"
            for path in missing
        )
        if missing:
            continue
        canonical = paths[0].read_bytes()
        failures.extend(
            f"shared helper drift: {path.relative_to(repo_root).as_posix()}"
            for path in paths[1:]
            if path.read_bytes() != canonical
        )
    return failures


def sync_shared_helpers_from_root(repo_root: Path) -> int:
    """Vendor root helpers into every declared standalone skill tree."""

    copied = 0
    for helper_name in SHARED_HELPER_SKILLS:
        source, *destinations = shared_helper_paths(repo_root, helper_name)
        if not source.is_file():
            raise FileNotFoundError(f"shared helper source not found: {source}")
        for destination in destinations:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if _same_file_content_and_mode(source, destination):
                continue
            shutil.copy2(source, destination)
            copied += 1
    return copied


def _same_file_content_and_mode(source: Path, destination: Path) -> bool:
    if not destination.is_file():
        return False
    source_mode = stat.S_IMODE(source.stat().st_mode)
    destination_mode = stat.S_IMODE(destination.stat().st_mode)
    return (
        source.read_bytes() == destination.read_bytes()
        and source_mode == destination_mode
    )


def sync_skill_trees(source_root: Path, destination_root: Path) -> SyncStats:
    if not source_root.is_dir():
        raise FileNotFoundError(f"source skill root not found: {source_root}")
    if not destination_root.is_dir():
        raise FileNotFoundError(f"destination skill root not found: {destination_root}")

    copied = 0
    removed = 0
    source_skills = collect_skill_names(source_root)
    destination_skills = collect_skill_names(destination_root)

    for skill_name in sorted(destination_skills - source_skills):
        stale_skill_dir = destination_root / skill_name
        removed += len(collect_files(stale_skill_dir))
        shutil.rmtree(stale_skill_dir)

    for skill_name in sorted(source_skills):
        (destination_root / skill_name).mkdir(parents=True, exist_ok=True)

    source_files = collect_files(source_root)
    destination_files = collect_files(destination_root)
    for relative_path in sorted(set(destination_files) - set(source_files)):
        destination_files[relative_path].unlink()
        removed += 1

    for relative_path, source_path in sorted(source_files.items()):
        destination_path = destination_root / relative_path
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        if _same_file_content_and_mode(source_path, destination_path):
            continue
        if destination_path.exists() and destination_path.is_dir():
            shutil.rmtree(destination_path)
        shutil.copy2(source_path, destination_path)
        copied += 1

    return SyncStats(copied=copied, removed=removed)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check or synchronize .claude/skills and .agents/skills.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--check",
        action="store_const",
        const="check",
        dest="mode",
        help="Check for drift without writing files (default).",
    )
    mode.add_argument(
        "--from-claude",
        action="store_const",
        const="from-claude",
        dest="mode",
        help="Synchronize .agents/skills from .claude/skills.",
    )
    mode.add_argument(
        "--from-agents",
        action="store_const",
        const="from-agents",
        dest="mode",
        help="Synchronize .claude/skills from .agents/skills.",
    )
    parser.set_defaults(mode="check")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    repo_root = Path(__file__).resolve().parent.parent
    claude_root = repo_root / ".claude" / "skills"
    agents_root = repo_root / ".agents" / "skills"

    try:
        if args.mode == "check":
            failures = [
                *compare_skill_trees(claude_root, agents_root),
                *compare_shared_helpers(repo_root),
            ]
            if failures:
                print("FAIL Skill mirror mismatch:", file=sys.stderr)
                for failure in failures:
                    print(f"  - {failure}", file=sys.stderr)
                return 1
            print(f"OK   skill mirrors match ({len(collect_files(claude_root))} files)")
            return 0

        shared_copied = sync_shared_helpers_from_root(repo_root)

        if args.mode == "from-claude":
            source_root, destination_root = claude_root, agents_root
            direction = ".claude/skills -> .agents/skills"
        else:
            source_root, destination_root = agents_root, claude_root
            direction = ".agents/skills -> .claude/skills"

        stats = sync_skill_trees(source_root, destination_root)
        failures = [
            *compare_skill_trees(claude_root, agents_root),
            *compare_shared_helpers(repo_root),
        ]
        if failures:
            print("FAIL Skill mirrors still differ after synchronization:", file=sys.stderr)
            for failure in failures:
                print(f"  - {failure}", file=sys.stderr)
            return 1
        print(
            f"OK   synced {direction}: "
            f"{stats.copied + shared_copied} copied, {stats.removed} removed"
        )
        return 0
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
