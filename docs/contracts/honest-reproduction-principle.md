# Honest Reproduction Principle

When this project compares its own outputs against external references
(literature formulas, paper figures, benchmark points), three rules apply
across all skills, scripts, and agents:

## 1. Independent derivation

All comparisons must be made against outputs produced by this project's
independent derivation pipeline (Package-X for symbolic; our own scan
pipeline for numerics). External formulas, digitized curves, and benchmark
data are **comparison targets only**, never computational backends.

An `independent` label requires runtime-verifiable evidence for every observable
used by the target. Metadata claims and method names in comments are not
evidence. Schema-valid result metadata, complete observable-to-task coverage,
declared nonempty derivation artifacts, hashes, and executable method calls
outside comments/strings are necessary static evidence, but do not by
themselves prove that the executed result flowed from Package-X. Phase 0
therefore records an otherwise valid static Package-X chain as `unknown` with
reason `derivation_evidence_not_runtime_verified` and caps it at
`needs_human_review`. A future runtime attestation may establish `independent`;
prompts or self-claims may not.

Reference data must resolve under the project's `literature/digitized/` input
area. It must not be an absolute/outside path, a symlink escape, or the same
resolved file, filesystem object, or content hash as a generated numerics or
reproduction artifact. Raw imported data, canonical comparison data, and the
normalization record are distinct immutable evidence files.

Every target records three independent evidence axes:

- `derivation_independence`: whether this project's calculation was
  independently derived;
- `reference_evidence`: whether the external target is an
  `independent_snapshot`, `synthetic`, or `unverified`;
- `comparison_evidence`: whether the metric is `machine_verifiable` or
  `requires_human_review`.

Every new calculation, scan, and reproduction result also records a verified
exact-byte `input_provenance` graph under the
[`content-addressed dependency contract`](./content-addressed-dependencies.md).
Consumers independently derive the complete expected dependency set and
recompute every file/root hash. Missing, stale, incomplete, or
`legacy-unverified` provenance cannot support a new scientific execution, an
`independent` derivation label, or a `pass` verdict. The graph is necessary but
not sufficient: it identifies declared inputs and code, but does not by itself
prove the runtime dataflow or the scientific correctness of the derivation.

A `pass` ceiling exists only when all three are respectively `independent`,
`independent_snapshot`, and `machine_verifiable`. Synthetic/unverified
references, formula review, and precomputed-boundary provenance cannot be
promoted to `pass` by a small metric.

The two target-derived axes are not editable result claims. Formula targets
derive `unverified` / `requires_human_review`; a quantitative target whose
acquisition is `synthetic_fixture` derives `synthetic`, while every other
schema-approved acquisition type derives `independent_snapshot`. Supported
quantitative metrics derive `machine_verifiable` except exclusion targets with
`precomputed_boundary` or `constraint_verdict_transition`, which derive
`requires_human_review`. Result generation and semantic validation must use
the same derivation rule, and validation must recompute both axes from the
current target before accepting a persisted result.

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

Absence of comparable evidence is not agreement. A numerical target may
receive `pass` or `fail` only after its declared comparison inputs are complete
and contain at least one finite comparison point. Missing columns, zero valid
points, or coverage below the target's predeclared policy produce `blocked`.

The following target semantics are mandatory:

- `figure_curve` is a finite, single-valued `y(x)` comparison over its complete
  predeclared domain. Duplicate x values, dropped rows, undeclared subranges,
  and interpolation outside scan support are blocked.
- `parametric_curve` is a separate ordered two-coordinate target. It declares
  one path parameter and its complete domain, open/closed topology, canonical
  coordinate scales, and the fixed `ordered_parametric_xy` representation.
  Both reference and scan evidence must contain finite unique ordered path
  nodes that exactly cover the declared parameter endpoints. Comparison is
  invariant under reparameterization: it uses the bidirectional continuous-
  polyline Hausdorff distance after coordinate normalization. Its fixed
  numerical error bound is reported; a bound that straddles tolerance blocks.
- `benchmark_point` contains exactly one reference row. A multi-point benchmark
  uses the separate keyed-set type, declares unique match keys, compares every
  required row/value, and is blocked below full coverage.
- `exclusion_region` declares its authoritative boundary source: an observable
  threshold, a constraint-verdict transition, or explicitly identified
  precomputed-boundary rows. It uses a bidirectional distance normalized by
  predeclared coordinate scales and must validate boundary coverage/components;
  raw scan coordinates are never inferred to be a boundary.
  A legacy single simple ordered component uses one explicit excluded-side
  probe. A disconnected or holed region instead declares every closed reference
  face exactly once, including its unique face ID, immediate parent (or null
  root), `interior`/`exterior` excluded side, and one authoritative excluded
  probe on that side. The reference component IDs, nesting graph, probes,
  predicted one-to-one component assignment, nesting, and excluded status must
  all verify. Incomplete/duplicate declarations, intersecting or touching
  reference faces, and ambiguous assignment block; an observed predicted face
  count, topology, or side/status disagreement fails. Sides alternate across
  parent/child faces. Constraint-verdict transitions remain blocked until
  transition edges can be assembled into ordered paths.
  Continuous-polyline distance uses a fixed normalized error bound; if the
  bound straddles the declared tolerance, the verdict is `blocked`.
- A comparison over a higher-dimensional scan fixes every hidden scan
  parameter to an exact declared slice. Implicit `all`, `any`, median, first-row,
  or duplicate-dropping aggregation is forbidden.

Unit conversion occurs before comparison. The import/digitization stage keeps
the raw source table, writes a canonical-unit table, and writes a strict
machine-verifiable record binding paths, hashes, source/canonical units, and
the exact finite factor/offset for each converted column. The comparator
verifies that record and transformation but never guesses or chooses units.
The scan-side canonical-unit authorities are singular and exact: model scan
and fixed parameters use `model-spec.json parameters[].unit`; task-backed
observables use the bound `result-meta.json return_value.unit`; custom
observables use `scan-config.json source.canonical_unit`. Every quantitatively
compared target column must have a canonical unit equal to the corresponding
scan-side authority as a string.
The comparator never converts scan output and blocks missing, ambiguous, or
mismatched unit authority, even when the bare numeric values agree.
Raw CSV conversion lexemes are checked with exact decimal arithmetic, without
a fixed significant-digit ceiling. Canonical numeric CSV cells must round-trip
through finite IEEE-754 binary64 without changing their decimal value because
the metric runtime is binary64; overflow, underflow, and sub-ULP distinctions
that the runtime would erase are blocked rather than rounded silently.

## 3. Immutable run history

When our derivation has a bug and the user fixes it, the re-run is recorded
as a **new run with a new repro-id**; old runs remain on disk and unmodified
for audit. Verdict, metrics, and provenance fields of any persisted run
result must not be edited after the run completes.

## Scope

This principle is load-bearing across:

- `.claude/skills/package-scribe/` (existing rules; reaffirmed)
- `.claude/skills/hep-paper-formalize/`
- `.claude/agents/repro-orchestrator.md` and
  `.codex/agents/repro-orchestrator.toml`
- `scripts/compare_to_reference.py`
- Any future skill / script / agent that performs comparison against
  external references

Implementation specifics (forbidden output paths, verdict ceiling rules,
tainted detection, and report-language requirements) are defined by the
mandatory implementation points below and the referenced shipped surfaces.

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

`scripts/compare_to_reference.py` is a mechanical publisher. It may validate
inputs, mechanically derive and enforce typed readiness, compute provenance
states and metrics, classify verdicts by fixed tolerance rules, write
reproduction outputs, and transactionally publish the manifest projection
strictly derived from a successful immutable run. That narrow write authority
is limited to `artifacts.reproduction`, one fresh
`reproduction_run_complete` history event, and schema-required bookkeeping. It
must preserve unrelated owner state and publish `manifest.json` last in the
same transaction as the immutable run and figures, as required by the
[skill / agent / script division](./skill-agent-division.md),
[manifest history](./manifest-history-actions.md), and
[transactional publication](./transactional-state-publication.md) contracts.

Mechanical readiness enforcement and exact-evidence rechecks under the
publication lock are fail-closed validation, not orchestration authority. The
script must not choose or dispatch prerequisite owners, infer readiness from
mutable manifest status or history, synthesize or repair another owner's fields
or events, relax tolerances, or make physics judgments for the user.
`repro-orchestrator` owns routing and validates the completed publication; it
must not perform a second manifest merge.

### 5. Repro-orchestrator dispatch reminder

When `repro-orchestrator` dispatches `package-scribe` for a reproduction task,
the dispatch payload must explicitly restate benchmark isolation: loop tasks
should be Package-X derived with `benchmark_used_as_input=false`; tree tasks
may use independent manual algebra only with honest provenance; literature
fallbacks must remain visible in `result-meta.json`.
An explicitly authorized literature-formula fallback is permitted only for a
clearly labeled exploratory calculation outside an honest reproduction claim.
Any reproduction target that depends on that fallback is tainted/blocked and
the fallback output is not comparison evidence.

### 6. Reproduction-result provenance fields

Every `reproduction-result.json` must record provenance at both run summary and
per-target levels. The per-target `derivation_independence`,
`reference_evidence`, and `comparison_evidence` fields jointly determine the
target's verdict ceiling, the top-level `input_provenance` graph must bind the
exact comparison inputs and tool sources, and `provenance_issues[]` must list
each task-level downgrade with a machine-readable reason.

### 7. Verdict ceiling for non-independent derivations

Targets whose derivation independence is `independent_manual`, `tainted`, or
`unknown`, whose reference is synthetic/unverified, or whose comparison
requires human review cannot receive a final `pass` verdict. Their
`verdict_ceiling` is `needs_human_review`. Negative outcomes such as `fail` or
`blocked` are never hidden or softened by this ceiling.

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
