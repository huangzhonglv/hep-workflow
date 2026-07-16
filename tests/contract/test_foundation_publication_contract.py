from __future__ import annotations

from pathlib import Path


def _normalized(path: Path) -> str:
    return " ".join(path.read_text(encoding="utf-8").split())


def test_foundation_skills_require_private_attempts_and_mechanical_finalization(
    repo_root: Path,
) -> None:
    idea = _normalized(repo_root / ".claude" / "skills" / "hep-idea" / "SKILL.md")
    paper = _normalized(
        repo_root / ".claude" / "skills" / "hep-paper-formalize" / "SKILL.md"
    )
    idea_manifest_contract = _normalized(
        repo_root
        / ".claude"
        / "skills"
        / "hep-idea"
        / "references"
        / "manifest-json-contract.md"
    )

    for text in (idea, paper, idea_manifest_contract):
        assert "scripts/init_foundation_attempt.py" in text
        assert "scripts/finalize_foundation_attempt.py" in text
        assert "candidate_dir" in text
        assert "already_published" in text

    assert "--owner hep-idea --mode initialize" in idea
    assert "--mode revise" in idea
    assert "--mode direct" in idea
    assert "--owner hep-paper-formalize --mode setup" in paper
    assert "--owner hep-paper-formalize --mode formalize" in paper
    assert "must never write the authoritative project artifact paths directly" in idea
    assert "never edit live foundation artifacts or `manifest.json`" in paper


def test_foundation_and_staleness_writer_contracts_are_explicit(
    repo_root: Path,
) -> None:
    transaction = _normalized(
        repo_root / "docs" / "contracts" / "transactional-state-publication.md"
    )
    ownership = _normalized(
        repo_root / "docs" / "contracts" / "skill-agent-division.md"
    )
    numerics = _normalized(
        repo_root / "docs" / "contracts" / "numerics-manifest-ownership.md"
    )

    assert "### Foundation-skill results" in transaction
    assert "implicit deletion" in transaction
    assert "manifest.json` last" in transaction
    assert "calculation aggregate `stale`" in transaction
    assert "first successful rerun starts a new current generation" in transaction
    assert "only into candidates allocated by `init_foundation_attempt.py`" in ownership
    assert "foundation finalizer derives `calculations.status" in ownership
    assert "`scripts/refresh_numerics_staleness.py` is the only standalone repair path" in numerics
    assert "does not append a numerics history event" in numerics
    history = _normalized(
        repo_root / "docs" / "contracts" / "manifest-history-actions.md"
    )
    assert "exact required action set from the changed owner files" in history
    assert "misclassified candidate actions fail" in history
    assert "`calculations.status = \"stale\"`" in history


def test_foundation_publication_scripts_are_present_on_public_surface(
    repo_root: Path,
) -> None:
    for relative in (
        "scripts/init_foundation_attempt.py",
        "scripts/finalize_foundation_attempt.py",
        "scripts/refresh_numerics_staleness.py",
    ):
        path = repo_root / relative
        assert path.is_file(), f"missing contract-bound writer {relative}"

    readme = _normalized(repo_root / "README.md")
    contributing = _normalized(repo_root / "CONTRIBUTING.md")
    assert "scripts/refresh_numerics_staleness.py" in readme
    for script in (
        "scripts/init_foundation_attempt.py",
        "scripts/finalize_foundation_attempt.py",
        "scripts/refresh_numerics_staleness.py",
    ):
        assert script in contributing
