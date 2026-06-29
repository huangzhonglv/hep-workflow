"""CLAUDE.md and AGENTS.md must be byte-identical.

This is a project-wide invariant from docs/contracts/mirror-invariants.md.
"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_claude_and_agents_md_byte_identical():
    claude = (REPO_ROOT / "CLAUDE.md").read_bytes()
    agents = (REPO_ROOT / "AGENTS.md").read_bytes()
    assert claude == agents, (
        "CLAUDE.md and AGENTS.md have diverged. "
        "Edit them together (they must be byte-identical) and re-run."
    )


def test_top_level_docs_use_lf_only():
    """Avoid CRLF/LF mixing that would break byte-identity on Windows."""
    for name in ("CLAUDE.md", "AGENTS.md"):
        content = (REPO_ROOT / name).read_bytes()
        assert b"\r\n" not in content, f"{name} contains CRLF; convert to LF"
