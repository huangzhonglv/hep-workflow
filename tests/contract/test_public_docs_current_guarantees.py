from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree


def test_readme_describes_current_fail_closed_guarantees(repo_root: Path) -> None:
    readme = (repo_root / "README.md").read_text(encoding="utf-8")
    normalized = " ".join(readme.split())

    required_fragments = (
        "## Current guarantees and limits",
        "docs/contracts/strict-json.md",
        "docs/contracts/skill-agent-division.md",
        "docs/contracts/transactional-state-publication.md",
        "docs/contracts/numerics-manifest-ownership.md",
        "docs/contracts/reproduction-readiness.md",
        "`benchmark_point` (exactly one reference",
        "`keyed_benchmark_set`",
        "`parametric_curve`",
        "`reference_faces`",
        "raw source table",
        "canonical-unit table",
        "normalization record",
        "Windows, NFS, SMB",
    )
    for fragment in required_fragments:
        assert fragment in normalized, f"README.md is missing {fragment!r}"


def test_contributing_has_current_ownership_and_recovery_workflows(
    repo_root: Path,
) -> None:
    contributing = (repo_root / "CONTRIBUTING.md").read_text(encoding="utf-8")
    normalized = " ".join(contributing.split())

    required_fragments = (
        "### Manifest ownership and version 2",
        "scripts/migrate_manifest_v2.py",
        "### Transactional publication and recovery",
        "scripts/recover_publication_transactions.py",
        "--recover --format json",
        "Do not delete `.hep-workflow-transactions` manually",
        "### Extending reproduction comparisons",
        "scripts/check_reproduction_readiness.py",
        "it does **not** mean every target is ready",
        "every target `disposition`",
        "scripts/_compare_metrics.py",
        "incomplete-coverage",
        "one exact slice",
        "`.claude/agents/<name>.md` and `.codex/agents/<name>.toml`",
        "If you added a multi-path writer",
        "a zero helper exit code is not treated as scientific readiness",
    )
    for fragment in required_fragments:
        assert fragment in normalized, f"CONTRIBUTING.md is missing {fragment!r}"

    stale_fragments = (
        "crash-transactional multi-file publication, and an explicit stochastic RNG",
        "If you touched the orchestrator, both",
    )
    for fragment in stale_fragments:
        assert fragment not in normalized, (
            f"CONTRIBUTING.md retains stale guidance {fragment!r}"
        )


def test_architecture_svg_names_current_evidence_boundaries(
    repo_root: Path,
) -> None:
    svg_path = repo_root / "docs" / "assets" / "hep-workflow-architecture-current.svg"
    root = ElementTree.parse(svg_path).getroot()
    namespace = "{http://www.w3.org/2000/svg}"

    assert root.get("viewBox") == "0 0 1800 1040"
    assert root.find(f"{namespace}title") is not None
    assert root.find(f"{namespace}desc") is not None

    visible_text = " ".join(
        " ".join("".join(element.itertext()).split())
        for element in root.iter(f"{namespace}text")
    )
    for fragment in (
        "owner-published, contract-verified evidence",
        "manifest-v2 scans + figures",
        "typed readiness + comparison",
        "validation + recovery",
        "manifest v2 + owner writes",
        "strict JSON + canonical IDs",
        "exact-byte provenance",
        "transactions + recovery",
        "typed readiness + honest comparison",
        "transactional evidence + immutable runs",
    ):
        assert fragment in visible_text, f"architecture SVG is missing {fragment!r}"

    for stale_fragment in (
        "manifest.json + artifact buckets",
        "HRP reproduction",
        "status + validation",
    ):
        assert stale_fragment not in visible_text
