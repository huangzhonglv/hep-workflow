# Manifest History Actions — Cross-Layer Consistency Rule

Project-level rule: a new value of `manifest.history[*].action` is meaningful
only if every consumer of the manifest knows how to handle it. A schema-valid
manifest whose history action the skills/orchestrators do not understand is
still a workflow bug.

## When adding a new history action

The action name must be added in **all** of:

- `schemas/manifest.schema.json`, where `history[*].action` is constrained by
  exact literals or canonical dynamic-action patterns
- The **owning skill's** `SKILL.md` (allowed-actions section / step describing
  when the action is emitted)
- The **owning skill's** `references/manifest-json-contract.md` (if present)
- **Both** orchestrator definitions:
  - `.claude/agents/<orchestrator>.md`
  - `.codex/agents/<orchestrator>.toml`
- Relevant workspace fixtures under `workspace/projects/*` if the new action
  appears in their history

Then add a contract test asserting cross-layer consistency: the action
appears in all required surfaces, and nowhere else.

## Current reproduction actions

The reproduction workflow currently reserves these history actions across
the manifest contract, `hep-paper-formalize`, and `repro-orchestrator`:

- `literature_complete`
- `literature_updated`
- `reproduction_run_complete`
- `reproduction_run_failed`

`hep-paper-formalize` owns the two `literature_*` actions. It authors them only
in its private foundation candidate; `finalize_foundation_attempt.py` validates
and publishes the event with the owned literature files and manifest in one
transaction. `hep-idea` foundation events follow the same candidate/finalizer
boundary. The finalizer never emits a duplicate completion/update action of its
own. It mechanically derives the exact required action set from the changed
owner files and model-version transition; missing, extra, duplicated, or
misclassified candidate actions fail before authoritative publication.
`compare_to_reference.py` mechanically owns `reproduction_run_complete` and
publishes it in the same transaction as the immutable run and figures.
`reproduction_run_failed` is reserved for a future mechanical failure recorder;
failed comparison commands currently leave manifest history unchanged. All
orchestrators must recognize these schema-valid actions but must not emit them.
Ownership limits emission, not readability.

## Current calculation actions

Calculation history actions use one of these canonical forms:

- `calc_task_{task_id}_complete`, where `{task_id}` is the literal task id such
  as `task-001`; for example `calc_task_task-001_complete`
- `calc_task_{task_id}_revised`, for reruns, backend corrections, or other
  revisions of an existing task artifact
- `calculations_updated`, only for legacy or manual aggregate updates spanning
  multiple tasks; the history entry must include a `note` explaining the
  affected tasks

New task-scoped calculation events are owned by Package-Scribe's mechanical
finalizer. The finalizer assigns a unique `event_id` and publishes the complete
task directory plus the merged manifest in one transaction, with the manifest
last. The orchestrator validates and reports that event but must not append a
second event or directly move the task between pending/completed arrays.

Do not encode physics labels or prose in the `action` string. Put that context
in `history[*].note`.

## Event identity and analysis/run ownership

Every newly produced numerics history event has an explicit `analysis_id` and
a globally fresh 32-character lowercase-hex `event_id`. Consumers may recover
an absent `analysis_id` only from one exact `analysis_id=analysis-NNN` note token
in a legacy entry. A missing `event_id` is likewise accepted only as legacy
read compatibility; new writers must never omit it. Duplicate event IDs,
ambiguous note tokens, and links to analyses that are absent from
`artifacts.numerics.analyses` are invalid.

Every immutable directory under `reproduction/runs/run-NNN/` appears exactly
once in `artifacts.reproduction.runs`. Each listed run has exactly one
`reproduction_run_complete` history event with the same `repro_id` and a fresh
event ID. A run directory, manifest entry, or completion event visible without
the other two is a partial publication and fails workspace validation.

A scan-dependent reproduction may name only a registered numerics analysis
whose per-analysis status is `done`; `partial`, `stale`, failed, blocked, and
unregistered scans cannot support a new comparison. This is rechecked by the
comparator under the publication lock so a manifest-only ownership transition
cannot race the original input snapshot. Formula-only and mechanically blocked
targets consume no scan and keep the reproduction numerics analysis projection
empty.

## `done` is an evidence-bearing state

An artifact status of `done` is not a progress label that an agent may infer
from a successful-looking command. It may be published only after the owned
evidence exists and the applicable schema and semantic validators accept it.
At minimum:

- every `done` artifact records a nonempty owning evidence collection, a
  nonempty `produced_by`, and a timestamp;
- a `done` model records a version, exact model checksum, and files;
- `calculations.status = "done"` records at least one completed task, no
  pending tasks, and each completed result has a current verified dependency
  graph;
- `calculations.status = "stale"` records at least one completed historical
  task, retains its non-null model dependency and producer/timestamp, and does
  not append a completion/update event for the mechanically derived transition;
  its preserved graphs remain inspectable but cannot support current work;
- `numerics.status = "done"` records at least one analysis and its files; each
  analysis has a mutually consistent config, strict `scan.csv` / metadata
  pair, summary, and every configured figure output. The numerics writer uses
  `partial` until selected constraints and figure coverage are complete;
- `reproduction.status = "done"` records at least one immutable run whose
  result and exact-byte provenance validate.

Manifest paths must be project-relative, contained, symlink-safe, unique, and
resolve to existing regular files (or an existing immutable run where the
field owns run IDs). Evidence arrays and global machine-ID namespaces must not
contain duplicates. A schema-valid `done` object is insufficient when the
workspace validator finds missing, stale, malformed, or cross-file-inconsistent
evidence.

Emit a completion history action only after the corresponding artifact state
has met these rules. A failed attempt must not create a completion action or
fabricate evidence. Multi-file writers must additionally satisfy
[`transactional-state-publication.md`](transactional-state-publication.md);
callers must not treat `done` alone as proof that an interrupted writer
preserved the previous snapshot.

## Why this is load-bearing

Manifest history is the project's audit trail. If a skill writes an action
the orchestrator does not recognize, status reports become unreliable and
staleness detection breaks silently.
