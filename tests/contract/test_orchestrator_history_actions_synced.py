from __future__ import annotations

import json
import tomllib
from pathlib import Path


REPRO_HISTORY_ACTIONS = {
    "literature_complete",
    "literature_updated",
    "reproduction_run_complete",
    "reproduction_run_failed",
}


def _history_action_literals(schema: dict) -> set[str]:
    action_schema = schema["$defs"]["history_entry"]["properties"]["action"]
    literals = set(action_schema.get("enum", []))
    for branch in action_schema.get("anyOf", []):
        literals.update(branch.get("enum", []))
    return literals


def test_reproduction_history_actions_synced_across_orchestrator_surfaces(
    repo_root: Path,
) -> None:
    schema = json.loads(
        (repo_root / "schemas" / "manifest.schema.json").read_text(encoding="utf-8")
    )
    schema_actions = _history_action_literals(schema)
    assert REPRO_HISTORY_ACTIONS <= schema_actions

    repro_claude_text = (
        repo_root / ".claude" / "agents" / "repro-orchestrator.md"
    ).read_text(encoding="utf-8")
    repro_codex_text = tomllib.loads(
        (repo_root / ".codex" / "agents" / "repro-orchestrator.toml").read_text(
            encoding="utf-8"
        )
    )["developer_instructions"]
    hep_claude_text = (
        repo_root / ".claude" / "agents" / "hep-orchestrator.md"
    ).read_text(encoding="utf-8")
    hep_codex_text = tomllib.loads(
        (repo_root / ".codex" / "agents" / "hep-orchestrator.toml").read_text(
            encoding="utf-8"
        )
    )["developer_instructions"]
    contract_text = (
        repo_root / "docs" / "contracts" / "manifest-history-actions.md"
    ).read_text(encoding="utf-8")

    for action in REPRO_HISTORY_ACTIONS:
        for text in (
            repro_claude_text,
            repro_codex_text,
            hep_claude_text,
            hep_codex_text,
            contract_text,
        ):
            assert action in text

    for text in (hep_claude_text, hep_codex_text):
        normalized = " ".join(text.split())
        assert "Recognize these actions when reading a shared manifest" in normalized
        assert "do not emit them" in normalized
        assert (
            "do not reject a manifest solely because it contains them" in normalized
        )
