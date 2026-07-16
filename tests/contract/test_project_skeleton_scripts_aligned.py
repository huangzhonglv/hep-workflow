"""Guard shared logic in the two self-contained project skeleton scripts.

The related repository-root resolver in hep-numerics/run_scan.py has a different
runtime lifecycle and is intentionally outside this pairwise invariant.
"""

from __future__ import annotations

import re
from pathlib import Path


DIRECTORIES_BLOCK = re.compile(
    r"^PROJECT_SUBDIRECTORIES = \(\n(?:    .*\n)*\)\n",
    flags=re.MULTILINE,
)


def normalize_skeleton_script(path: Path, skill_name: str) -> str:
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines(keepends=True)
    assert len(lines) > 2
    assert lines[1].startswith('"""') and lines[1].rstrip().endswith('"""')
    lines[1] = '"""<skill-specific module docstring>"""\n'
    source = "".join(lines)

    source, directories_replaced = DIRECTORIES_BLOCK.subn(
        "PROJECT_SUBDIRECTORIES = (<skill-specific directories>)\n",
        source,
        count=1,
    )
    assert directories_replaced == 1

    skill_name_occurrences = source.count(skill_name)
    assert skill_name_occurrences == 4
    return source.replace(skill_name, "<skill-name>")


def test_project_skeleton_scripts_differ_only_in_allowlisted_details(
    repo_root: Path,
) -> None:
    skills_root = repo_root / ".claude" / "skills"
    hep_idea = normalize_skeleton_script(
        skills_root / "hep-idea" / "scripts" / "init_project_skeleton.py",
        "hep-idea",
    )
    paper_formalize = normalize_skeleton_script(
        skills_root
        / "hep-paper-formalize"
        / "scripts"
        / "init_paper_project_skeleton.py",
        "hep-paper-formalize",
    )

    assert hep_idea == paper_formalize
