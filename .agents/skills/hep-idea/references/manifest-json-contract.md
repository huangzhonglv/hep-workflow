# manifest.json Contract

## Purpose

`manifest.json` records project state, artifact files, and version dependencies.
For hep-idea, write it only after the idea, model, and constraint artifacts have
already been generated.

## Shape

Top-level object with:
- `project_name`
- `created`
- `last_updated`
- `active_model_version`
- `artifacts`
- `history`

Use `templates/manifest.example.json` for example shape only. The contract in
this file is authoritative for the initial hep-idea manifest layout.

## Initial hep-idea Expectations

For the initial hep-idea write:
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
- `artifacts.numerics.depends_on.model.version = null`
- `artifacts.numerics.depends_on.model.checksum = null`
- `artifacts.numerics.depends_on.calculations.tasks = []`
- `artifacts.numerics.depends_on.calculations.model_version = null`
- `artifacts.numerics.depends_on.constraints.checksum = null`

The numerics artifact additionally includes `analyses: string[]`, maintained by
hep-numerics. hep-idea writes the initial empty list only.

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

- Do not create `manifest.json` as an early placeholder
- Write it only after the corresponding artifacts exist and are non-empty
- Keep artifact file lists aligned with the actual outputs produced by the skill
- Keep `depends_on` paths aligned with the actual nesting under `artifacts.*`
- Include the empty `calculations` and `numerics` skeletons expected by
  orchestrator from the initial hep-idea write
