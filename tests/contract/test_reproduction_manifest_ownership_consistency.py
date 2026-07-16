from __future__ import annotations

from pathlib import Path


def _normalized(path: Path) -> str:
    return " ".join(path.read_text(encoding="utf-8").split())


def test_honest_reproduction_manifest_writer_matches_ownership_contracts(
    repo_root: Path,
) -> None:
    contracts = repo_root / "docs" / "contracts"
    honest = _normalized(contracts / "honest-reproduction-principle.md")
    division = _normalized(contracts / "skill-agent-division.md")
    history = _normalized(contracts / "manifest-history-actions.md")
    transaction = _normalized(contracts / "transactional-state-publication.md")

    section = honest.split("### 4. Compare script mechanical boundary", 1)[1].split(
        "### 5. Repro-orchestrator dispatch reminder", 1
    )[0]

    for fragment in (
        "mechanically derive and enforce typed readiness",
        "transactionally publish the manifest projection",
        "`artifacts.reproduction`",
        "`reproduction_run_complete` history event",
        "preserve unrelated owner state",
        "publish `manifest.json` last",
        "must not choose or dispatch prerequisite owners",
        "must not perform a second manifest merge",
    ):
        assert fragment in section, f"honest reproduction boundary is missing {fragment!r}"

    assert "updating manifest state" not in section
    assert "`compare_to_reference.py` owns that manifest projection" in division
    assert (
        "`compare_to_reference.py` mechanically owns `reproduction_run_complete`"
        in history
    )
    assert (
        "write the manifest last when it indexes other candidate paths" in transaction
    )
