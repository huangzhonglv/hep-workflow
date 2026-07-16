# manifest.json Contract

## Purpose

`manifest.json` records project state, artifact files, and version dependencies.
For hep-idea, author the candidate only after the idea, model, and constraint
candidate artifacts have already been generated.

## Publication boundary

Before generating any foundation artifact, call
`scripts/init_foundation_attempt.py --owner hep-idea` with the branch-specific
mode. Write every path described by this contract below the returned
`candidate_dir`; never write the authoritative project paths directly. After
the complete candidate is ready, call `scripts/finalize_foundation_attempt.py`
with the exact returned project, attempt directory, attempt ID, owner, and mode.

The finalizer validates owner scope, schema and cross-file identities, preserves
unrelated manifest state/history, marks prior calculation evidence stale when
load-bearing model/task/benchmark bytes change, derives affected numerics
staleness, and transactionally publishes owned files with `manifest.json` last.
Only `published` or verified `already_published` output is success.

## Shape

Top-level object with:
- `manifest_version` (write `2`)
- `project_name`
- `created`
- `last_updated`
- `active_model_version`
- `artifacts`
- `history`

Use `templates/manifest.example.json` for example shape only. The contract in
this file is authoritative for the initial hep-idea manifest layout.

## Initial hep-idea Expectations

For the initial hep-idea candidate:
- `active_model_version = "v1"`
- `artifacts.idea.status = "done"`
- `artifacts.model.status = "done"`
- `artifacts.constraints.status = "done"`
- `artifacts.calculations.status = "not_started"`
- `artifacts.numerics.status = "not_started"`

Expected initial file lists:
- `artifacts.idea.files = ["idea/proposal.md"]`
- `artifacts.model.files = ["model/model-spec.json", "model/calc-tasks.json", "model/benchmarks.json"]`
- `artifacts.constraints.files = ["constraints/constraints-summary.md", "constraints/constraints-data.json"]`

Expected producers:
- `artifacts.idea.produced_by = "hep-idea"`
- `artifacts.model.produced_by = "hep-idea"`
- `artifacts.constraints.produced_by = "hep-idea"`

Expected initial skeleton entries:
- `artifacts.calculations.completed_tasks = []`
- `artifacts.calculations.pending_tasks = []`
- `artifacts.calculations.depends_on.model.version = null`
- `artifacts.calculations.depends_on.model.checksum = null`
- `artifacts.numerics.files = []`
- `artifacts.numerics.analyses = []`
- `artifacts.numerics.produced_by = null`
- `artifacts.numerics.timestamp = null`

The numerics artifact uses the manifest-v2 per-analysis ownership contract in
`docs/contracts/numerics-manifest-ownership.md`. `analyses` is an array of
analysis-owned objects once numerics exist; hep-idea writes only the exact
empty `not_started` skeleton. There is no aggregate numerics `depends_on`.

On revision, hep-idea must preserve the candidate's unowned calculations and
numerics objects exactly. The finalizer, not the skill, derives any
`calculations.status = "stale"` or per-analysis numerics stale transition while
preserving the historical files, dependency declarations, producer, and
timestamp.

## Checksum and Dependency Rules

- `artifacts.model.checksum` must be the SHA-256 checksum of
  `model/model-spec.json`
- `artifacts.constraints.depends_on.model.version` and
  `artifacts.constraints.depends_on.model.checksum` must point to the active
  model used to collect the constraint set

## History

The initial hep-idea manifest should append:
- `idea_complete`
- `model_complete_v1`
- `constraints_complete`

Optional `note` (string) may accompany `action` / `timestamp` / `by` to record
human-readable revision context. hep-idea Branch II / III should fill this when
the revision is user-initiated.

### `history.action`

hep-idea may emit only these actions:

- `idea_complete`
- `model_complete_v{N}`
- `model_updated`
- `constraints_complete`
- `constraints_updated`
- `benchmarks_updated`

Do not emit any other hep-idea history action unless the schema, orchestrator
checks, tests, and this contract are updated together. `model_complete_v{N}`
uses the active model version number, for example `model_complete_v2`.

## Authoring Checklist

- Do not create candidate `manifest.json` as an early placeholder
- Author it only after the corresponding candidate artifacts exist and are non-empty
- Never write or copy it directly to the project root
- Keep artifact file lists aligned with the actual outputs produced by the skill
- Keep `depends_on` paths aligned with the actual nesting under `artifacts.*`
- Include the empty `calculations` and `numerics` skeletons expected by
  orchestrator from the initial hep-idea write
- Require successful `finalize_foundation_attempt.py` output before reporting
  authoritative completion
