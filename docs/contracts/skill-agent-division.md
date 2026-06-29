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

## Manifest Write Authority (Key Boundary, Aligned With Current `hep-numerics` Practice)

`manifest.json` is **not** private agent data; it is the source of truth for
workspace state. All three layers may read it, but write authority is divided
as follows:

- **Script** may write the manifest only through contract-bound helper APIs (for
  example
  `.claude/skills/hep-numerics/scripts/_manifest.py:update_manifest_for_numerics`),
  and only for fields that are mechanically derivable, such as new artifact file
  lists, scan metadata pointers, and fixed-template history entry fields. This is
  the existing pattern implemented by `run_scan.py` / `make_figures.py`.
- **Agent** owns orchestration decisions: deciding when to dispatch which skill /
  script, which history action to use, and how to resolve cross-skill dependency
  and staleness issues.
- **Skill** usually does not write the manifest directly; skills either call a
  script helper or return results for the agent to write.

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
 ↓
Agent  ──(Skill tool)──► Skill  ──(Bash tool)──► Script  ──► files / manifest helper
         (Bash tool)──► Script
```

An agent may call a script directly (bypassing a skill) if and only if:
1. The task is purely mechanical (already constrained by the script contract)
2. The input has been confirmed valid by a prior skill or prior validation

## Exceptions And Existing Practice

The `hep-numerics` skill calls `run_scan.py` / `make_figures.py` through the Bash
tool, and those scripts write the manifest through helpers in `_manifest.py`.
This is the standard example for this rule. When a new script needs to write the
manifest, reuse the helper pattern instead of letting each script hand-assemble
history entry strings.
