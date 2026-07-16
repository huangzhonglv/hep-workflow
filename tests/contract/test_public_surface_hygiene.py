from __future__ import annotations

from pathlib import Path


PRIVATE_HISTORY_SURFACES = (
    "docs/contracts/mirror-invariants.md",
    "docs/contracts/manifest-history-actions.md",
    "docs/contracts/honest-reproduction-principle.md",
    ".claude/skills/hep-paper-formalize/SKILL.md",
    ".agents/skills/hep-paper-formalize/SKILL.md",
    ".claude/agents/repro-orchestrator.md",
    ".codex/agents/repro-orchestrator.toml",
    "workspace/projects/smoke-e2e/literature/paper-extract.json",
)

PRIVATE_HISTORY_TERMS = (
    "PR-1",
    "PR-2",
    "§13",
    "§4.6",
    "§3.2",
    "§5.7",
    "see §8",
    "§6.5",
    "§6.3",
    "risk H",
    "risk D / Lc",
    "(Lc)",
    "user decision J",
    "resolved_mapping",
    "_label_was",
)

PACKAGE_SCRIBE_CONVENTION_SURFACES = (
    "SKILL.md",
    "references/standard-theories.md",
    "examples/electroweak-minimal-examples.md",
)

PUBLIC_RELEASE_ROOTS = (
    "AGENTS.md",
    "CLAUDE.md",
    "CONTRIBUTING.md",
    "README.md",
    ".agents",
    ".claude",
    ".codex",
    ".github",
    "docs",
    "schemas",
    "scripts",
    "tests",
    "workspace/projects/smoke-e2e",
)

PROMPT_HISTORY_GLOB = "codex-prompts-*.md"
PROMPT_HISTORY_MARKER = b"codex-prompts-"
LOCAL_ARTIFACT_DIRS = {"__pycache__", ".pytest_cache"}
LOCAL_ARTIFACT_NAMES = {".DS_Store"}
LOCAL_ARTIFACT_SUFFIXES = {".pyc", ".pyo"}


def is_local_artifact(path: Path) -> bool:
    return (
        any(part in LOCAL_ARTIFACT_DIRS for part in path.parts)
        or path.name in LOCAL_ARTIFACT_NAMES
        or path.suffix in LOCAL_ARTIFACT_SUFFIXES
    )


def public_release_files(repo_root: Path) -> list[Path]:
    files = {
        path
        for path in repo_root.iterdir()
        if path.is_file() and not is_local_artifact(path)
    }
    for relative_root in PUBLIC_RELEASE_ROOTS:
        root = repo_root / relative_root
        if root.is_file() and not is_local_artifact(root):
            files.add(root)
        elif root.is_dir():
            files.update(
                path
                for path in root.rglob("*")
                if path.is_file() and not is_local_artifact(path)
            )
    return sorted(files)


def test_top_level_docs_describe_all_shipped_workflow_components(
    repo_root: Path,
) -> None:
    for filename in ("CLAUDE.md", "AGENTS.md"):
        text = (repo_root / filename).read_text(encoding="utf-8")
        for component in (
            "`hep-orchestrator`",
            "`repro-orchestrator`",
            "`hep-idea`",
            "`hep-paper-formalize`",
            "`package-scribe`",
            "`hep-numerics`",
            "literature/",
            "reproduction/",
        ):
            assert component in text, f"{filename} is missing {component}"
        assert "future entry skills" not in text


def test_public_contract_surfaces_do_not_reference_private_design_history(
    repo_root: Path,
) -> None:
    for relative_path in PRIVATE_HISTORY_SURFACES:
        text = (repo_root / relative_path).read_text(encoding="utf-8")
        for term in PRIVATE_HISTORY_TERMS:
            assert term not in text, f"{relative_path} still contains {term!r}"


def test_public_release_does_not_ship_execution_prompt_history(
    repo_root: Path,
) -> None:
    current_test = Path(__file__).resolve()
    failures: list[str] = []
    for path in public_release_files(repo_root):
        if path.resolve() == current_test:
            continue
        relative_path = path.relative_to(repo_root)
        if path.match(PROMPT_HISTORY_GLOB):
            failures.append(f"{relative_path}: forbidden prompt-history filename")
            continue
        if PROMPT_HISTORY_MARKER in path.read_bytes():
            failures.append(f"{relative_path}: references prompt-history files")

    assert not failures, "Prompt execution history leaked into the public tree:\n" + "\n".join(failures)


def test_package_scribe_does_not_reference_missing_sm_feynman_rules_pdf(
    repo_root: Path,
) -> None:
    for skill_tree in (".claude", ".agents"):
        skill_root = repo_root / skill_tree / "skills" / "package-scribe"
        for relative_path in PACKAGE_SCRIBE_CONVENTION_SURFACES:
            text = (skill_root / relative_path).read_text(encoding="utf-8")
            assert "SM-FeynmanRules.pdf" not in text


def test_orphaned_root_scripts_remain_removed(repo_root: Path) -> None:
    for filename in ("init_project.py", "migrate_legacy_numerics.py"):
        assert not (repo_root / "scripts" / filename).exists()
