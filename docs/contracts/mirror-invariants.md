# Mirror Invariants

Project-level rule: certain pairs of files must remain in sync. Violations
break the build through contract tests.

## The four mirror rules

1. **Skill mirror**: `.claude/skills/<name>/` and `.agents/skills/<name>/` must
   be **byte-identical** except for hardcoded installation-path strings. Always
   edit both trees in the same change.

2. **Orchestrator mirror**: every `.claude/agents/<name>.md` (Claude format)
   has a corresponding `.codex/agents/<name>.toml` (Codex format) and the two
   must remain **content-equivalent**. Format differences (Markdown vs TOML)
   are expected; semantic content must match.

3. **Schemas ↔ examples ↔ workspace fixtures**: schema changes must keep
   `schemas/examples/` and `workspace/projects/*` validating. Run both
   validators after any schema or contract edit.

4. **Top-level docs mirror**: `CLAUDE.md` and `AGENTS.md` at the repo root must
   be **byte-identical**. They serve different audiences (Claude Code reads
   `CLAUDE.md`; Codex and other tooling read `AGENTS.md`) but the rules they
   describe are the same project rules.

## Enforcement

| Rule | Contract test |
| --- | --- |
| 1 | `tests/contract/test_skill_tree_mirrors.py` plus targeted per-skill mirror tests |
| 2 | `tests/contract/test_hep_orchestrator_codex_role.py` and per-agent equivalents |
| 3 | `scripts/validate_examples.py` + `scripts/validate_workspace_projects.py` |
| 4 | `tests/contract/test_top_level_docs_byte_identical.py` (added in PR-1) |

## Discipline when editing

- Editing one side of a pair without the other is a workflow bug, even if
  the contract test happens not to catch the specific edit.
- When adding a new mirror pair (e.g., a new skill), add the corresponding
  contract test in the same PR.
- For format-equivalent (not byte-identical) pairs (rule 2), use a parser-
  based test that compares semantic structure, not raw text.
