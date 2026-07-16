from __future__ import annotations

from pathlib import Path


CANONICAL_REGEX = "^[A-Za-z_][A-Za-z0-9_]*$"
CANONICAL_CONTRACT = "docs/contracts/canonical-name-convention.md"

CANONICAL_RULE_SURFACES = (
    ".claude/skills/hep-idea/SKILL.md",
    ".claude/skills/hep-idea/references/model-spec-json-contract.md",
    ".claude/skills/hep-numerics/SKILL.md",
    ".claude/skills/hep-numerics/references/scan-config-json-contract.md",
    ".claude/skills/package-scribe/SKILL.md",
    ".claude/skills/hep-paper-formalize/SKILL.md",
    ".claude/agents/hep-orchestrator.md",
    ".codex/agents/hep-orchestrator.toml",
)

REMOVED_LONG_FORM_MARKERS = {
    ".claude/skills/hep-idea/SKILL.md": (
        "No LaTeX commands, Unicode, prime symbols, or curly braces.",
    ),
    ".claude/skills/hep-numerics/SKILL.md": ("Conversion flow:",),
    ".claude/skills/hep-numerics/references/scan-config-json-contract.md": (
        "Use canonical names in:",
    ),
    ".claude/skills/package-scribe/SKILL.md": (
        "Automatic rewriting, aliases, and case transformations",
    ),
    ".claude/skills/hep-paper-formalize/SKILL.md": (
        "All parameter names satisfy canonical-name compliance",
    ),
}


def frontmatter_keys(path: Path) -> set[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "---", f"{path} is missing opening frontmatter delimiter"
    end = lines.index("---", 1)

    keys: set[str] = set()
    for line in lines[1:end]:
        if not line or line[0].isspace():
            continue
        key, separator, _ = line.partition(":")
        assert separator, f"invalid top-level frontmatter line in {path}: {line!r}"
        keys.add(key)
    return keys


def test_canonical_rule_surfaces_use_one_local_rule_and_contract_pointer(
    repo_root: Path,
) -> None:
    for relative_path in CANONICAL_RULE_SURFACES:
        text = (repo_root / relative_path).read_text(encoding="utf-8")
        assert text.count(CANONICAL_REGEX) == 1, relative_path
        assert CANONICAL_CONTRACT in text, relative_path


def test_long_form_canonical_rule_restatements_stay_removed(repo_root: Path) -> None:
    for relative_path, markers in REMOVED_LONG_FORM_MARKERS.items():
        text = (repo_root / relative_path).read_text(encoding="utf-8")
        for marker in markers:
            assert marker not in text, f"{relative_path} still contains {marker!r}"


def test_skill_frontmatter_uses_one_project_convention(repo_root: Path) -> None:
    skill_files = sorted((repo_root / ".claude" / "skills").glob("*/SKILL.md"))
    assert skill_files
    for path in skill_files:
        assert frontmatter_keys(path) == {"name", "description"}, path


def test_compact_orchestrator_rule_keeps_validation_behavior(repo_root: Path) -> None:
    for relative_path in (
        ".claude/agents/hep-orchestrator.md",
        ".codex/agents/hep-orchestrator.toml",
    ):
        text = (repo_root / relative_path).read_text(encoding="utf-8")
        section = text.split(
            "### 3. Canonical name validation (after skill completes)",
            1,
        )[1].split("\n---", 1)[0]
        for fragment in (
            "calc-tasks.json",
            "result-meta.json",
            "constraints-data.json",
            "active scan config",
            "python3 scripts/validate_workspace_projects.py <project-name>",
            "numerics/custom_observables.py",
            "scan.meta.json",
            "Do not accept completion or patch `manifest.json`",
            "owner to correct the publication",
        ):
            assert fragment in section, f"{relative_path} is missing {fragment!r}"
