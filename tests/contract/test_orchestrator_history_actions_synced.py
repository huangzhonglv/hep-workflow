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

    claude_text = (
        repo_root / ".claude" / "agents" / "repro-orchestrator.md"
    ).read_text(encoding="utf-8")
    codex_text = tomllib.loads(
        (repo_root / ".codex" / "agents" / "repro-orchestrator.toml").read_text(
            encoding="utf-8"
        )
    )["developer_instructions"]
    contract_text = (
        repo_root / "docs" / "contracts" / "manifest-history-actions.md"
    ).read_text(encoding="utf-8")

    for action in REPRO_HISTORY_ACTIONS:
        assert action in claude_text
        assert action in codex_text
        assert action in contract_text
