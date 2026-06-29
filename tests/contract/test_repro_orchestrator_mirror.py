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
        "target_will_be_blocked",
        "scripts/compare_to_reference.py",
    ]

    for text in [claude, codex]:
        for snippet in required_forbidden:
            assert snippet in text
        for snippet in required_dispatch:
            assert snippet in text
