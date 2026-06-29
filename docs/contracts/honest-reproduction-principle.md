# Honest Reproduction Principle

When this project compares its own outputs against external references
(literature formulas, paper figures, benchmark points), three rules apply
across all skills, scripts, and agents:

## 1. Independent derivation

All comparisons must be made against outputs produced by this project's
independent derivation pipeline (Package-X for symbolic; our own scan
pipeline for numerics). External formulas, digitized curves, and benchmark
data are **comparison targets only**, never computational backends.

The existing `package-scribe` benchmark isolation rule
(`.claude/skills/package-scribe/SKILL.md` §"Benchmark Isolation Hard Constraint") is the
canonical implementation of this principle for symbolic calculations and
remains in force.

## 2. Honest reporting

Disagreement between our outputs and external references must be reported
as-is. Forbidden:

- Adjusting tolerance after seeing the metric
- Dropping or re-sampling data points to align curves
- Using subjective hedging ("approximately matches", "close enough") in place
  of the actual numerical metric
- Claiming reproduction success when our derivation is not independent

## 3. Immutable run history

When our derivation has a bug and the user fixes it, the re-run is recorded
as a **new run with a new repro-id**; old runs remain on disk and unmodified
for audit. Verdict, metrics, and provenance fields of any persisted run
result must not be edited after the run completes.

## Scope

This principle is load-bearing across:

- `.claude/skills/package-scribe/` (existing rules; reaffirmed)
- `.claude/skills/hep-paper-formalize/` (PR-2)
- `.claude/agents/repro-orchestrator.{md,toml}` (PR-2)
- `scripts/compare_to_reference.py` (PR-2)
- Any future skill / script / agent that performs comparison against
  external references

Implementation specifics (forbidden output paths, verdict ceiling rules,
tainted detection, lint of report language) are detailed in PR-2.

## Implementation landing points

The 3 principles above are realized through the following 10 mandatory
implementation points. Any new comparison-related code must satisfy all of
them.

### 1. Top-level contract file

This file (`docs/contracts/honest-reproduction-principle.md`) is the
load-bearing contract; all skills, agents, and scripts that compare project
outputs against external references must reference it.

### 2. Package-scribe benchmark isolation (existing, reaffirmed)

The existing `package-scribe` benchmark isolation rule remains unchanged and
authoritative. Paper formulas, benchmark values, and digitized data may be
used only as comparison targets or benchmarks after an independent derivation,
never as inputs to `result.wl`, `result-python.py`, or Package-X calculation
logic.

### 3. Hep-paper-formalize forbidden outputs

`hep-paper-formalize` must not write calculation outputs, numerics outputs, or
reproduction run outputs. In particular it must not write `result.wl`,
`result-python.py`, `result-meta.json`, `numerics/scan-configs/*`, or
`reproduction/runs/*`. Extracted paper formulas may be recorded for benchmark
and report comparison only; they must not become computational backends.

### 4. Compare script mechanical boundary

`scripts/compare_to_reference.py` is a mechanical script. It may validate
inputs, compute provenance states, compute metrics, classify verdicts by fixed
tolerance rules, and write reproduction outputs. It must not contain workflow
business logic such as deciding whether a project is ready to run, updating
manifest state, relaxing tolerances, or making physics judgments for the user.

### 5. Repro-orchestrator dispatch reminder

When `repro-orchestrator` dispatches `package-scribe` for a reproduction task,
the dispatch payload must explicitly restate benchmark isolation: loop tasks
should be Package-X derived with `benchmark_used_as_input=false`; tree tasks
may use independent manual algebra only with honest provenance; literature
fallbacks must remain visible in `result-meta.json`.

### 6. Reproduction-result provenance fields

Every `reproduction-result.json` must record provenance at both run summary and
per-target levels. The per-target `derivation_independence` field is
authoritative for that target's verdict ceiling, and `provenance_issues[]`
must list each task-level downgrade with a machine-readable reason.

### 7. Verdict ceiling for non-independent derivations

Targets whose derivation independence is `independent_manual`, `tainted`, or
`unknown` cannot receive a final `pass` verdict. Their `verdict_ceiling` is
`needs_human_review`. Negative outcomes such as `fail` or `blocked` are never
hidden or softened by this ceiling.

### 8. Immutable reproduction runs

Once `reproduction/runs/<repro-id>/` has been written, it is read-only audit
history. A re-run after any code, formula, scan, tolerance, or input change
must use a new `repro-id`. Scripts must refuse to overwrite an existing run
directory.

### 9. Neutral reporting language

`repro-orchestrator` reports and generated reproduction reports must use
neutral, metric-first language. They must not replace numerical disagreement
with phrases such as "approximately matches" or "basically consistent", and
must not claim reproduction success unless the relevant target is both
independently derived and has a `pass` verdict.

### 10. User final review

The agent presents evidence and suggested next steps, but the user makes the
final accept/reject decision for any target requiring human review. A
`needs_human_review` verdict is not a hidden pass, and agents must not decide
that outcome on the user's behalf.
