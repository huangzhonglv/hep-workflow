from __future__ import annotations

from pathlib import Path


REQUIRED_FORBIDDEN_BEHAVIORS = [
    "Adjusting `tolerance` to flip a `fail` verdict to `pass`",
    "Editing `reproduction-result.json` after `compare_to_reference.py` writes it",
    'Re-running `compare_to_reference.py` with different tolerance to "see if it passes now"',
    "Computing metrics inline",
    "Using subjective hedging language",
    "Deciding the final verdict for the user when verdict is `needs_human_review`",
    "Auto-loosening provenance check",
    "Claiming reproduction success unless derivation is `independent`, reference evidence is `independent_snapshot`, comparison evidence is `machine_verifiable`, and the fixed metric verdict is `pass`",
]


def _normalize_whitespace(text: str) -> str:
    return " ".join(text.split())


def test_repro_orchestrator_forbidden_behaviors_are_listed(repo_root: Path) -> None:
    text = (repo_root / ".claude" / "agents" / "repro-orchestrator.md").read_text(
        encoding="utf-8"
    )
    normalized = _normalize_whitespace(text)

    assert "docs/contracts/honest-reproduction-principle.md" in text
    for behavior in REQUIRED_FORBIDDEN_BEHAVIORS:
        assert _normalize_whitespace(behavior) in normalized
