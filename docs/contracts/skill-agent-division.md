# Skill / Agent / Script Division Rule

| Layer | Physical form | Runtime | Determinism | Responsibility |
| --- | --- | --- | --- | --- |
| **Script** | `.py` file | Python interpreter | Deterministic | Mechanical computation (numerics, validation, file generation); may update the manifest through contract-bound helper APIs |
| **Skill** | `SKILL.md` + references + templates | Loaded into LLM context | Non-deterministic | Generation / interpretation that requires LLM judgment |
| **Agent** | `.md` file (top-level entry) | Loaded into LLM context | Non-deterministic | Multi-step dispatch, state management, orchestration decisions |

## Selection Rules

| Task type | Required layer |
| --- | --- |
| Numerical computation (error metrics, interpolation, statistics, grid scans) | Script |
| Extract structured information from natural language / PDF | Skill |
| Write Mathematica / Python or similar code | Skill |
| Decide dispatch order and read the manifest to choose the next skill | Agent |
| File existence / format validation (mechanical) | Script |

## Manifest Write Authority

`manifest.json` is **not** private agent data; it is the source of truth for
workspace state. All three layers may read it, but write authority is divided
as follows:

- **Script** may write the manifest only through contract-bound helper APIs (for
  example
  `.claude/skills/hep-numerics/scripts/_manifest.py:build_manifest_for_numerics`),
  and only for fields that are mechanically derivable, such as new artifact file
  lists, scan metadata pointers, and fixed-template history entry fields. This is
  the existing pattern implemented by `run_scan.py` / `make_figures.py`.
- **Skill** may author skill-owned manifest fields and history entries only in
  a private candidate when that candidate is part of the skill's documented
  output contract. `hep-idea` and `hep-paper-formalize` follow this pattern.
  They preserve unrelated fields and prior history; the contract-bound
  foundation finalizer validates and transactionally publishes the candidate.
- **Agent** owns orchestration decisions. It must not directly edit state that
  can be derived mechanically from an owned publication. In particular,
  `repro-orchestrator` verifies but never writes reproduction state;
  `compare_to_reference.py` owns that manifest projection. Likewise,
  `hep-orchestrator` verifies Package-Scribe completion but does not perform a
  second calculation manifest merge; `finalize_package_result.py` owns it.
- **Script** also owns conservative state invalidation implied by a publication.
  In particular, the foundation finalizer derives `calculations.status =
  "stale"` for changed load-bearing model/task/benchmark inputs and derives the
  affected numerics projection. Skills and agents must not author these stale
  transitions in their candidates or repair them after publication.

Writer ownership is narrow: a skill or agent must not update another layer's
owned artifact state merely because it can edit the shared manifest. Candidate
writes use structured JSON handling, append each history event exactly once,
and must pass their contract-bound finalizer before success is reported. No
skill or agent may copy a candidate into live paths itself.

## Anti-Patterns

- Agent directly calls numpy to compute metrics -> must go through a script
- Skill embeds regex / complex-structure parsing -> move it to a script and let
  the skill call it
- Script decides orchestration by itself ("if task-001 is done, jump to
  numerics") -> belongs to the agent
- Agent writes final deliverable Python code -> this is skill work

## Cross-Layer Call Pattern

```
User
  |
Agent --(Skill tool)--> Skill ----------------> private candidate + owned event intent
  |                      |
  |                      +--(Bash tool)------> Script --> transactional publication
  +----(Bash tool)---------------------------> Script
  +------------------------------------------> routing decisions (read-only state)
```

An agent may call a script directly (bypassing a skill) if and only if:
1. The task is purely mechanical (already constrained by the script contract)
2. The input has been confirmed valid by a prior skill or prior validation

## Exceptions And Existing Practice

The `hep-numerics` skill calls `run_scan.py` / `make_figures.py` through the Bash
tool, and those scripts write numerics state through helpers in `_manifest.py`.
This remains the standard pattern for script-written manifest state. When a new
script needs to write the manifest, reuse a contract-bound helper instead of
letting each script hand-assemble history entry strings.

`hep-idea` and `hep-paper-formalize` write their skill-owned artifact entries
only into candidates allocated by `init_foundation_attempt.py`.
`finalize_foundation_attempt.py` enforces owner/mode scope and publishes those
artifact files plus the manifest as one generation. `hep-paper-formalize` owns
the content of `literature_*` history actions; the finalizer publishes the
event without inventing a second action.
`compare_to_reference.py` owns `reproduction_run_complete` and publishes the
immutable run, figures, and manifest projection in one transaction with the
manifest last. `repro-orchestrator` validates that publication and never edits
the manifest directly. `reproduction_run_failed` remains reserved until a
mechanical failure recorder has a contract-bound publication path.

Package-Scribe generates a complete batch candidate in an owned attempt.
`finalize_package_result.py` mechanically validates that candidate and owns the
task-scoped `calc_task_*_(complete|revised)` event, publishing the task tree and
manifest in one transaction with the manifest last. The LLM skill does not edit
the canonical task directory or manifest piecemeal.
