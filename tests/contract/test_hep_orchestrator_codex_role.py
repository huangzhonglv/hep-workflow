from __future__ import annotations

import tomllib
from pathlib import Path


def _claude_body(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    _frontmatter, separator, body = text.partition("\n---\n")
    assert separator
    return body


def _normalize_orchestrator_body(text: str) -> str:
    replacements = {
        "\u2192": "->",
        "\u2705": "[done]",
        "\U0001f504": "[in progress]",
        "\u2b1c": "[pending]",
        "\u26a0\ufe0f": "[warning]",
        "\u26a0": "[warning]",
    }
    for source, replacement in replacements.items():
        text = text.replace(source, replacement)
    return "\n".join(line.rstrip() for line in text.strip().splitlines())


def _orchestrator_bodies(repo_root: Path) -> dict[str, str]:
    codex_role = tomllib.loads(
        (repo_root / ".codex" / "agents" / "hep-orchestrator.toml").read_text(
            encoding="utf-8"
        )
    )
    return {
        "claude": _claude_body(
            repo_root / ".claude" / "agents" / "hep-orchestrator.md"
        ),
        "codex": codex_role["developer_instructions"],
    }


def test_codex_hep_orchestrator_role_uses_current_schema(repo_root: Path) -> None:
    role_path = repo_root / ".codex" / "agents" / "hep-orchestrator.toml"
    role = tomllib.loads(role_path.read_text(encoding="utf-8"))

    assert role["name"] == "hep-orchestrator"
    assert role["description"]
    assert role["developer_instructions"].startswith("# HEP Orchestrator")
    assert "prompt" not in role


def test_codex_hep_orchestrator_matches_claude_definition(repo_root: Path) -> None:
    claude_body = _claude_body(
        repo_root / ".claude" / "agents" / "hep-orchestrator.md"
    )
    codex_role = tomllib.loads(
        (repo_root / ".codex" / "agents" / "hep-orchestrator.toml").read_text(
            encoding="utf-8"
        )
    )

    assert _normalize_orchestrator_body(
        codex_role["developer_instructions"]
    ) == _normalize_orchestrator_body(claude_body)


def test_hep_orchestrator_uses_string_list_numerics_analyses(
    repo_root: Path,
) -> None:
    forbidden_fragments = [
        'status == "done"',
        "analyses[].status",
        "analysis.depends_on",
        "numerics.analyses[i].depends_on",
        "`scan.meta.json.observables`",
        "`analysis_id`, `scan_parameters`, and `history_action`",
        "`scan.meta.json.history_action` is\n  `numerics_figures_regenerated`",
    ]
    required_fragments = [
        "`numerics.analyses[]` is a string list",
        "`artifacts.numerics.depends_on`",
        "`numerics/scan-configs/{analysis_id}.json.depends_on.model_version`",
        "`schemas/scan-meta.schema.json`",
        "`scan_config_snapshot`",
        "`numerics_figures_regenerated` is recorded in `manifest.history[]`",
    ]

    for name, body in _orchestrator_bodies(repo_root).items():
        for fragment in forbidden_fragments:
            assert fragment not in body, f"{name} still describes object analyses"
        for fragment in required_fragments:
            assert fragment in body, f"{name} is missing {fragment}"
