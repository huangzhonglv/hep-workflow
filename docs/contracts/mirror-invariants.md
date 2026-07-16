# Mirror Invariants

Project-level rule: certain pairs of files must remain in sync. Violations
break the build through contract tests.

## The five mirror rules

1. **Skill mirror**: `.claude/skills/<name>/` and `.agents/skills/<name>/` must
   be **byte-identical**. Always edit both trees in the same change. When a
   shared skill file discusses its installed path, name both supported layouts
   in the same text rather than varying the mirrored copies.

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

5. **Vendored shared helpers**: these canonical root helpers and their
   standalone copies under `.claude/skills/hep-numerics/scripts/` and
   `.agents/skills/hep-numerics/scripts/` must be **byte-identical**:
   `_strict_json.py`, `_identity.py`, `_dependency_graph.py`,
   `_workflow_dependencies.py`, and `_scan_artifact_validation.py`. The root
   `scripts/` copy is the source of truth for this helper set. The canonical
   `_publication_transaction.py` helper is additionally vendored byte-for-byte
   into both `.claude/skills/package-scribe/scripts/` and
   `.agents/skills/package-scribe/scripts/`, because both standalone writer
   skills use the same failure-atomic publication protocol.

## Enforcement

| Rule | Contract test |
| --- | --- |
| 1 | `tests/contract/test_skill_tree_mirrors.py` |
| 2 | `tests/contract/test_hep_orchestrator_codex_role.py` and per-agent equivalents |
| 3 | `scripts/validate_examples.py` + `scripts/validate_workspace_projects.py` |
| 4 | `tests/contract/test_top_level_docs_byte_identical.py` |
| 5 | `scripts/sync_skill_mirrors.py --check` and `tests/contract/test_strict_json_helper_mirrors.py` |

## Discipline when editing

- Editing one side of a pair without the other is a workflow bug, even if
  the contract test happens not to catch the specific edit.
- Edit a vendored shared helper in root `scripts/`. Run either explicit sync
  mode to refresh the standalone copies; choose `--from-claude` versus
  `--from-agents` only according to the authoritative side of any simultaneous
  skill-tree edits. Both modes refresh helpers from root, and neither
  skill-tree helper copy is authoritative.
- When adding a new mirror pair (e.g., a new skill), follow the
  [Adding a new skill](../../CONTRIBUTING.md#adding-a-new-skill) checklist.
  The generic mirror test discovers new skill files automatically; add a
  per-skill contract test only for semantics it does not already cover.
- For format-equivalent (not byte-identical) pairs (rule 2), use a parser-
  based test that compares semantic structure, not raw text.
