from __future__ import annotations

import re
import tomllib
from pathlib import Path


def _frontmatter(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n", text, flags=re.DOTALL)
    assert match is not None, f"missing YAML frontmatter in {path}"
    return match.group(1)


def _frontmatter_name(path: Path) -> str:
    match = re.search(r"^name:\s*(\S+)\s*$", _frontmatter(path), flags=re.MULTILINE)
    assert match is not None, f"missing name in {path}"
    return match.group(1)


def _claude_body(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    _frontmatter, separator, body = text.partition("\n---\n")
    assert separator
    return body


def _normalize(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.strip().splitlines())


def test_codex_repro_orchestrator_role_uses_current_schema(repo_root: Path) -> None:
    role_path = repo_root / ".codex" / "agents" / "repro-orchestrator.toml"
    role = tomllib.loads(role_path.read_text(encoding="utf-8"))

    assert role["name"] == "repro-orchestrator"
    assert "PRECEDENCE" in role["description"]
    assert role["developer_instructions"].startswith("# Repro Orchestrator")
    assert "prompt" not in role


def test_codex_repro_orchestrator_matches_claude_definition(repo_root: Path) -> None:
    claude_path = repo_root / ".claude" / "agents" / "repro-orchestrator.md"
    codex_path = repo_root / ".codex" / "agents" / "repro-orchestrator.toml"
    role = tomllib.loads(codex_path.read_text(encoding="utf-8"))

    assert role["name"] == _frontmatter_name(claude_path)
    assert _normalize(role["developer_instructions"]) == _normalize(
        _claude_body(claude_path)
    )


def test_repro_orchestrator_forbidden_and_dispatch_sections_mirrored(
    repo_root: Path,
) -> None:
    claude = _claude_body(repo_root / ".claude" / "agents" / "repro-orchestrator.md")
    codex = tomllib.loads(
        (repo_root / ".codex" / "agents" / "repro-orchestrator.toml").read_text(
            encoding="utf-8"
        )
    )["developer_instructions"]

    required_forbidden = [
        "Adjusting `tolerance` to flip a `fail` verdict to `pass`",
        "Editing `reproduction-result.json` after `compare_to_reference.py` writes it",
        "Computing metrics inline (must go through the script)",
        "Auto-loosening provenance check",
    ]
    required_dispatch = [
        "Step 0",
        "Step 1",
        "Step 7",
        "Pre-numerics gate",
        "check_reproduction_readiness.py",
        "workflow_state=routable",
        "scripts/compare_to_reference.py",
        "`literature=missing|invalid|stale`",
        "`model=missing|invalid|stale`",
        "`calculations=missing|invalid|stale`",
        "`numerics=missing|invalid|stale`",
        "`numerics=blocked`",
        "`not_applicable`",
        "A nonzero exit, malformed JSON, or schema-invalid report fails closed",
        "Do not pass --blocked-targets in new workflows",
        "formula targets report calculations=not_applicable",
    ]

    for text in [claude, codex]:
        for snippet in required_forbidden:
            assert snippet in text
        normalized = " ".join(text.split())
        for snippet in required_dispatch:
            assert snippet in normalized


def test_repro_orchestrator_manifest_writer_boundaries(repo_root: Path) -> None:
    claude = _claude_body(repo_root / ".claude" / "agents" / "repro-orchestrator.md")
    codex = tomllib.loads(
        (repo_root / ".codex" / "agents" / "repro-orchestrator.toml").read_text(
            encoding="utf-8"
        )
    )["developer_instructions"]

    required = [
        "`hep-paper-formalize` authors `literature_*` history entries in its private foundation candidate",
        "`finalize_foundation_attempt.py` validates and publishes that event",
        "candidate existence is not completion",
        "`compare_to_reference.py` writes `reproduction_run_complete`",
        "`repro-orchestrator` never writes `manifest.json` directly",
        "The manifest is deliberately last",
        "`hep-numerics` `_manifest.py` helper remains scoped to numerics",
        "`python3 scripts/validate_workspace_projects.py <project-name>`",
    ]

    for text in (claude, codex):
        normalized = " ".join(text.split())
        for snippet in required:
            assert snippet in normalized
        assert "`scripts/_manifest.py`" not in normalized
        assert "extend the helper" not in normalized

    contract = " ".join(
        (
            repo_root / "docs" / "contracts" / "skill-agent-division.md"
        ).read_text(encoding="utf-8").split()
    )
    assert "**Skill** may author skill-owned manifest fields" in contract
    assert "**Agent** owns orchestration decisions" in contract
    assert "`compare_to_reference.py` owns `reproduction_run_complete`" in contract
    assert "`repro-orchestrator` validates that publication" in contract
