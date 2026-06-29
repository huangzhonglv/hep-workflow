from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

from jsonschema import Draft202012Validator


ALLOWED_EXACT_ACTIONS = {
    "idea_complete",
    "model_updated",
    "constraints_complete",
    "constraints_updated",
    "benchmarks_updated",
    "literature_complete",
    "literature_updated",
    "reproduction_run_complete",
    "reproduction_run_failed",
    "numerics_analysis_complete",
    "numerics_analysis_rerun",
    "numerics_figures_regenerated",
    "calculations_updated",
}

ALLOWED_ACTION_PATTERNS = (
    re.compile(r"^model_complete_v\d+$"),
    re.compile(r"^calc_task_task-\d{3}_(complete|revised)$"),
)

LEGACY_CALCULATION_ACTION_PATTERNS = (
    re.compile(r"^calculation_complete_"),
    re.compile(r"^calculations_(partial|backend|weak_)"),
    re.compile(r"^task_\d{3}_"),
    re.compile(r"^timelike_python_backend_complete$"),
)


def _is_allowed_action(action: str) -> bool:
    return action in ALLOWED_EXACT_ACTIONS or any(
        pattern.match(action) for pattern in ALLOWED_ACTION_PATTERNS
    )


def _manifest_with_history_action(repo_root: Path, action: str, **extra: str) -> dict:
    manifest = json.loads(
        (repo_root / "schemas" / "examples" / "manifest.example.json").read_text(
            encoding="utf-8"
        )
    )
    manifest["history"] = [
        {
            "action": action,
            "timestamp": "2026-05-09T00:00:00Z",
            "by": "pytest",
            **extra,
        }
    ]
    return manifest


def test_manifest_schema_rejects_unknown_history_action(repo_root: Path) -> None:
    schema = json.loads(
        (repo_root / "schemas" / "manifest.schema.json").read_text(encoding="utf-8")
    )
    manifest = _manifest_with_history_action(repo_root, "typo_action")

    errors = list(Draft202012Validator(schema).iter_errors(manifest))

    assert errors
    assert any(list(error.absolute_path) == ["history", 0, "action"] for error in errors)


def test_manifest_schema_accepts_dynamic_history_action_patterns(
    repo_root: Path,
) -> None:
    schema = json.loads(
        (repo_root / "schemas" / "manifest.schema.json").read_text(encoding="utf-8")
    )
    validator = Draft202012Validator(schema)

    for action in ("model_complete_v2", "calc_task_task-001_revised"):
        manifest = _manifest_with_history_action(repo_root, action)
        assert list(validator.iter_errors(manifest)) == []


def test_manifest_schema_requires_note_for_calculations_updated(
    repo_root: Path,
) -> None:
    schema = json.loads(
        (repo_root / "schemas" / "manifest.schema.json").read_text(encoding="utf-8")
    )
    manifest = _manifest_with_history_action(repo_root, "calculations_updated")

    errors = list(Draft202012Validator(schema).iter_errors(manifest))

    assert errors
    assert any(list(error.absolute_path) == ["history", 0] for error in errors)


def test_workspace_manifest_history_actions_use_canonical_names(
    repo_root: Path,
) -> None:
    failures: list[str] = []
    for manifest_path in sorted((repo_root / "workspace" / "projects").glob("*/manifest.json")):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for index, entry in enumerate(manifest.get("history", [])):
            if not isinstance(entry, dict):
                continue
            action = entry.get("action")
            if not isinstance(action, str):
                failures.append(f"{manifest_path}:{index}: missing string action")
                continue
            if any(pattern.match(action) for pattern in LEGACY_CALCULATION_ACTION_PATTERNS):
                failures.append(f"{manifest_path}:{index}: legacy calculation action {action!r}")
            if not _is_allowed_action(action):
                failures.append(f"{manifest_path}:{index}: noncanonical action {action!r}")
            if action == "calculations_updated" and not entry.get("note"):
                failures.append(f"{manifest_path}:{index}: calculations_updated requires note")

    assert not failures, "Manifest history action naming failures:\n" + "\n".join(failures)


def test_calculation_history_actions_synced_across_prompts_and_contracts(
    repo_root: Path,
) -> None:
    claude_orchestrator = (
        repo_root / ".claude" / "agents" / "hep-orchestrator.md"
    ).read_text(encoding="utf-8")
    codex_orchestrator = tomllib.loads(
        (repo_root / ".codex" / "agents" / "hep-orchestrator.toml").read_text(
            encoding="utf-8"
        )
    )["developer_instructions"]
    contract = (
        repo_root / "docs" / "contracts" / "manifest-history-actions.md"
    ).read_text(encoding="utf-8")
    claude_package_scribe = (
        repo_root / ".claude" / "skills" / "package-scribe" / "SKILL.md"
    ).read_text(encoding="utf-8")
    agents_package_scribe = (
        repo_root / ".agents" / "skills" / "package-scribe" / "SKILL.md"
    ).read_text(encoding="utf-8")

    surfaces = {
        "claude orchestrator": claude_orchestrator,
        "codex orchestrator": codex_orchestrator,
        "manifest history contract": contract,
        "claude package-scribe": claude_package_scribe,
        "agents package-scribe": agents_package_scribe,
    }
    required_fragments = {
        "calc_task_{task_id}_complete",
        "calc_task_task-001_complete",
        "calc_task_{task_id}_revised",
        "calculations_updated",
    }

    failures = [
        f"{surface_name}: missing {fragment}"
        for surface_name, text in surfaces.items()
        for fragment in required_fragments
        if fragment not in text
    ]

    assert not failures, "Calculation history action docs are out of sync:\n" + "\n".join(failures)
