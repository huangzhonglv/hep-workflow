from __future__ import annotations

import difflib
import re
from pathlib import Path


FORBIDDEN_SUBSTRINGS = (
    "hep-numerics-design.md",
    "hep-numerics-design",
    "architecture-design.md",
    "architecture-design",
)

SKILL_ROOTS = (
    Path(".agents") / "skills" / "hep-numerics",
    Path(".claude") / "skills" / "hep-numerics",
)

REPO_WIDE_SKIP_DIRS = {
    ".git",
    ".venv",
    ".pytest_cache",
    "tmp",
    "__pycache__",
    ".vendor",
}

MARKDOWN_LINK_RE = re.compile(r"(?<!!)\[([^\]\n]+)\]\(([^)\n]+)\)")
EXTERNAL_LINK_PREFIXES = ("http://", "https://", "mailto:")


def skill_markdown_files(repo_root: Path) -> list[Path]:
    files: list[Path] = []
    for root in SKILL_ROOTS:
        skill_root = repo_root / root
        files.append(skill_root / "SKILL.md")
        files.extend(sorted((skill_root / "references").glob("*.md")))
    return files


def repo_wide_design_doc_reference_files(repo_root: Path) -> list[Path]:
    current_test = Path(__file__).resolve()
    files: list[Path] = []
    for path in repo_root.rglob("*"):
        if not path.is_file():
            continue
        relative_path = path.relative_to(repo_root)
        if any(part in REPO_WIDE_SKIP_DIRS for part in relative_path.parts):
            continue
        if path.resolve() == current_test:
            continue
        if relative_path.match("codex-prompts-*.md"):
            continue
        files.append(path)
    return sorted(files)


def design_doc_reference_failures(paths: list[Path], repo_root: Path) -> list[str]:
    failures: list[str] = []
    for path in paths:
        text = path.read_bytes().decode("utf-8", errors="replace")
        for line_number, line in enumerate(text.splitlines(), start=1):
            for forbidden in FORBIDDEN_SUBSTRINGS:
                if forbidden in line:
                    snippet = line.strip()
                    if len(snippet) > 160:
                        snippet = snippet[:157] + "..."
                    failures.append(
                        f"{path.relative_to(repo_root)}:{line_number}: "
                        f"{forbidden!r} in {snippet!r}"
                    )
    return failures


def comparable_skill_files(repo_root: Path, root: Path) -> list[Path]:
    skill_root = repo_root / root
    files = [skill_root / "SKILL.md"]
    files.extend(sorted((skill_root / "references").glob("*.md")))
    files.extend(
        sorted(
            path
            for path in (skill_root / "templates").rglob("*")
            if path.is_file() and path.name != ".DS_Store"
        )
    )
    files.extend(sorted((skill_root / "scripts").glob("*.py")))
    return files


def first_diff_offset(left: bytes, right: bytes) -> int | None:
    for index, (left_byte, right_byte) in enumerate(zip(left, right, strict=False)):
        if left_byte != right_byte:
            return index
    if len(left) != len(right):
        return min(len(left), len(right))
    return None


def diff_stat(left: bytes, right: bytes) -> str:
    offset = first_diff_offset(left, right)
    prefix = f"bytes {len(left)} != {len(right)}; first diff offset {offset}"
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
    added = sum(1 for line in diff_lines if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in diff_lines if line.startswith("-") and not line.startswith("---"))
    return f"{prefix}; +{added}/-{removed} lines"


def strip_link_suffixes(target: str) -> str:
    target = target.strip()
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1]
    return re.split(r"[?#]", target, maxsplit=1)[0]


def test_no_design_doc_references(repo_root: Path) -> None:
    failures = design_doc_reference_failures(skill_markdown_files(repo_root), repo_root)

    assert not failures, "Forbidden design document references found:\n" + "\n".join(failures)


def test_no_design_doc_references_repo_wide(repo_root: Path) -> None:
    failures = design_doc_reference_failures(
        repo_wide_design_doc_reference_files(repo_root),
        repo_root,
    )

    assert not failures, "Forbidden design document references found:\n" + "\n".join(failures)


def test_skill_tree_byte_identical(repo_root: Path) -> None:
    agents_root, claude_root = SKILL_ROOTS
    agents_files = {
        path.relative_to(repo_root / agents_root): path
        for path in comparable_skill_files(repo_root, agents_root)
    }
    claude_files = {
        path.relative_to(repo_root / claude_root): path
        for path in comparable_skill_files(repo_root, claude_root)
    }

    failures: list[str] = []
    for relative_path in sorted(set(agents_files) | set(claude_files)):
        agents_path = agents_files.get(relative_path)
        claude_path = claude_files.get(relative_path)
        if agents_path is None:
            failures.append(f"{relative_path}: missing in .agents")
            continue
        if claude_path is None:
            failures.append(f"{relative_path}: missing in .claude")
            continue

        agents_bytes = agents_path.read_bytes()
        claude_bytes = claude_path.read_bytes()
        if agents_bytes != claude_bytes:
            failures.append(
                f"{relative_path}: {diff_stat(agents_bytes, claude_bytes)}"
            )

    assert not failures, "hep-numerics skill trees differ:\n" + "\n".join(failures)


def test_skill_md_keeps_runtime_rules(repo_root: Path) -> None:
    skill_text = (
        repo_root / ".agents" / "skills" / "hep-numerics" / "SKILL.md"
    ).read_text(encoding="utf-8").lower()

    required_groups = {
        "mode keyword 'batch'": ("batch",),
        "mode keyword 'interactive'": ("interactive",),
        "branch keyword 'Branch I'": ("branch i",),
        "branch keyword 'Branch II'": ("branch ii",),
        "branch keyword 'Branch III'": ("branch iii",),
        "canonical name rule": ("canonical",),
        "manifest update responsibility": ("manifest",),
        "validate_scan_config command": ("validate_scan_config",),
        "run_scan command": ("run_scan",),
        "make_figures command": ("make_figures",),
        "self-check checklist": ("checklist", "self-check"),
        "references index": ("references",),
    }

    missing = [
        label
        for label, alternatives in required_groups.items()
        if not any(keyword in skill_text for keyword in alternatives)
    ]

    assert not missing, "Missing runtime-rule keywords:\n" + "\n".join(missing)


def test_markdown_relative_links_resolve(repo_root: Path) -> None:
    failures: list[str] = []
    for path in skill_markdown_files(repo_root):
        lines = path.read_text(encoding="utf-8").splitlines()
        in_fenced_code = False
        for line_number, line in enumerate(lines, start=1):
            if line.lstrip().startswith("```"):
                in_fenced_code = not in_fenced_code
                continue
            if in_fenced_code:
                continue

            for match in MARKDOWN_LINK_RE.finditer(line):
                link_text = match.group(1)
                target = match.group(2).strip()
                if target.startswith(EXTERNAL_LINK_PREFIXES):
                    continue
                if target.startswith("#"):
                    continue

                path_part = strip_link_suffixes(target)
                if not path_part:
                    continue

                resolved = (path.parent / path_part).resolve()
                if not resolved.exists():
                    failures.append(
                        f"{path.relative_to(repo_root)}:{line_number}: "
                        f"[{link_text}] -> {resolved}"
                    )

    assert not failures, "Broken markdown relative links found:\n" + "\n".join(failures)
