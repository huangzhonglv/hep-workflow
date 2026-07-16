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
    assert "Coordinates project state" in role["description"]
    assert role["developer_instructions"].startswith("# HEP Orchestrator")
    assert "prompt" not in role

    claude_definition = (
        repo_root / ".claude" / "agents" / "hep-orchestrator.md"
    ).read_text(encoding="utf-8")
    assert "Coordinates project state" in claude_definition
    assert "Manages project state" not in claude_definition
    assert "Manages project state" not in role["description"]


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


def test_hep_orchestrator_uses_per_analysis_numerics_ownership(
    repo_root: Path,
) -> None:
    forbidden_fragments = [
        'status == "done"',
        "numerics.analyses[i].depends_on",
        "`numerics.analyses[]` is a string list",
        "`artifacts.numerics.depends_on`",
        "`scan.meta.json.observables`",
        "`analysis_id`, `scan_parameters`, and `history_action`",
        "`scan.meta.json.history_action` is\n  `numerics_figures_regenerated`",
    ]
    required_fragments = [
        "Each analysis entry owns `status`,",
        "there is no global",
        "Aggregate `numerics.files` is the exact sorted",
        "replot writer",
        "`schemas/scan-meta.schema.json`",
        "`scan_config_snapshot`",
        "`numerics_figures_regenerated` is recorded in `manifest.history[]`",
    ]

    for name, body in _orchestrator_bodies(repo_root).items():
        for fragment in forbidden_fragments:
            assert fragment not in body, f"{name} still describes object analyses"
        for fragment in required_fragments:
            assert fragment in body, f"{name} is missing {fragment}"


def test_hep_orchestrator_has_no_phantom_contract_terms_or_duplicate_triggers(
    repo_root: Path,
) -> None:
    for name, body in _orchestrator_bodies(repo_root).items():
        assert "`model/model-spec.md`" not in body, (
            f"{name} still requires the unproduced model-spec.md artifact"
        )
        assert '`"locked"`' not in body, f"{name} still describes a nonexistent lock state"
        assert body.count('"analyze XXX"') == 1, f"{name} duplicates the new-analysis trigger"
        assert body.count('"rerun analysis-002"') == 1, f"{name} duplicates the rerun trigger"
        assert body.count('"regenerate figures"') == 1, f"{name} duplicates the replot trigger"


def test_hep_orchestrator_respects_manifest_writer_ownership(
    repo_root: Path,
) -> None:
    required_fragments = [
        "route state changes through documented writers",
        "Directly edit skill/script-owned manifest fields or append their history events",
        "`hep-idea` owns the generated artifact content and history-event intent",
        "`finalize_foundation_attempt.py` owns the initial authoritative publication",
        "scripts/refresh_numerics_staleness.py --project-dir <project-dir>",
        "If it reports `NEEDS REFRESH`, invoke the same command with `--write`",
        "history, copy candidate paths, or perform a second manifest write",
        "must never treat candidate existence as completion",
        "Emission follows narrow writer ownership:",
        "`reproduction_run_complete`, owned by `compare_to_reference.py`",
        "This orchestrator has no general-purpose direct manifest write procedure",
        "Ad hoc agent\nread-modify-write is prohibited",
    ]
    forbidden_fragments = [
        "- Read and update manifest.json",
        "validate its outputs before updating manifest:",
        "Compute SHA-256 of model-spec.json, store as model checksum",
        "Populate `calculations.pending_tasks` from calc-tasks.json task list",
        "Model-first actions this orchestrator may emit",
        "Every time you update manifest.json",
        "Write the updated manifest.json back to disk",
        "exists, include it in the manifest",
        "`reproduction_run_complete` and `reproduction_run_failed`, owned by",
        "a contract-bound numerics writer must publish",
    ]

    for name, body in _orchestrator_bodies(repo_root).items():
        for fragment in required_fragments:
            assert fragment in body, f"{name} is missing ownership guard {fragment!r}"
        for fragment in forbidden_fragments:
            assert fragment not in body, f"{name} still grants direct writer authority"
