# constraints-data.json Contract

## Purpose

`constraints/constraints-data.json` is the machine-readable source of truth for
experimental constraints and scan usability. Write it before
`constraints-summary.md`.

## Shape

Top-level object with:
- `model_name`
- optional `model_version`
- `parameters`
- `constraints` (array of constraint objects)

Use `templates/constraints-data.example.json` for example shape only. The
contract in this file is authoritative for required fields and automation
readiness rules.

## Required Constraint Fields

Each constraint entry must contain:
- `id`
- `name`
- `type`
- `observable`
- `source`
- `implementation_status`
- `notes`

Also include:
- the value fields appropriate to its type, such as `central_value` and
  `uncertainty`, `limit_value`, or an `interpolation` payload
- `computed_by` whenever the observable maps to a task, a derived quantity, or
  a parameter combination in the current workflow

Top-level `parameters` should list the canonical parameter names relevant to the
constraint set.

`model_version`, when present, records the model version that the constraint set
was assembled against. It must match `^v\d+$`.

## Two Independent Labels

### `computed_by`

Describes where the theory quantity comes from:
- `task`
- `derived`
- `parameter_combination`
- `external`

Template patterns:
- `{"type": "task", "task_id": "..."}`
- `{"type": "derived", "depends_on_tasks": [...], "derivation_note": "..."}`
- `{"type": "parameter_combination", "formula": "..."}`
- `{"type": "external", "note": "..."}`

### `implementation_status`

Describes whether hep-numerics can use the constraint automatically:
- `direct`
- `interpolated`
- `manual_only`

Decision order:
1. decide `computed_by`
2. decide `implementation_status`

Combination rules for `computed_by.type = "external"`:
- use `direct` only when the current workflow already has a dedicated,
  automatically callable implementation of that external theory quantity
- use `interpolated` only when the current workflow already has such a
  dedicated implementation and the experimental side is provided by a local
  interpolation asset
- if the observable is only available in the literature, or would require a new
  dedicated module / future implementation, use `manual_only`

## Interpolation Rules

Use `implementation_status: "interpolated"` only when both conditions hold:
- the local asset already exists under
  `workspace/projects/{project-name}/constraints/`
- the interpolation metadata is complete

If either condition is missing, use `manual_only` instead.

When `implementation_status` is `interpolated`, include an `interpolation`
payload with:
- local asset path
- x-axis parameter name and unit
- y-axis quantity name and unit
- interpolation method
- valid range
- extrapolation policy

## Hard Invariants

- Parameter names must match `model-spec.json` canonical names exactly
- `implementation_status` must reflect actual automation readiness, not hoped-for
  future readiness
- Never mark a constraint `interpolated` unless the asset is already local and
  immediately usable
- Do not pair `computed_by.type = "external"` with `direct` or `interpolated`
  unless the required dedicated theory implementation already exists in the
  current workflow

## Authoring Checklist

- Prefer the strongest up-to-date experimental numbers you can support
- Record machine-usable constraints in structured form first
- Keep summary labels consistent with this file rather than overstating
  automation support
- Any `computed_by.task_id` or `computed_by.depends_on_tasks` reference must
  resolve to task IDs that actually exist in the current `calc-tasks.json`;
  otherwise switch to a non-task mode such as `external` instead of leaving a
  dangling reference
