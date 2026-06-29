"""CLAUDE.md / AGENTS.md must not duplicate contract bodies.

Detect by looking for long-form signatures that signal the body has been
copied back instead of linked. The forbidden list below intentionally
excludes any short string that legitimately appears in the summary table
(e.g., we do NOT forbid the canonical regex because the top-level summary
uses prose, not the regex itself)."""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Strings that should appear ONLY in docs/contracts/, never in CLAUDE.md/AGENTS.md.
# Each entry is a multi-word phrase / long clause that constitutes "copied
# contract body" rather than a brief pointer.
FORBIDDEN_IN_TOP_LEVEL = [
    # Full mirror clause (paraphrased from CLAUDE.md §"Mirroring invariants" #1)
    "byte-identical except for hardcoded installation-path strings",
    # Full history-action recipe phrase
    "schemas/manifest.schema.json (if constrained there)",
    # Long-form canonical-name explanation (the contract file has it; top level should not)
    "All machine-readable identifiers (parameters, observables, file names",
]

def test_top_level_docs_no_contract_bodies():
    for name in ("CLAUDE.md", "AGENTS.md"):
        text = (REPO_ROOT / name).read_text(encoding="utf-8")
        for forbidden in FORBIDDEN_IN_TOP_LEVEL:
            assert forbidden not in text, (
                f"{name} contains contract body '{forbidden[:60]}...'. "
                f"Move to docs/contracts/ and link instead."
            )
