# calc-tasks.json Contract

## Purpose

`model/calc-tasks.json` is the core interface between model formalization and
symbolic calculation. It tells downstream tools what to calculate, while
`model-spec.json` tells them what the model is.

## Shape

Top-level object with:
- `model_name`
- optional `model_version`
- `tasks` (array of task objects)

Use `templates/calc-tasks.example.json` for example shape only. The contract in
this file is authoritative for required task fields and invariants.

`model_version`, when present, records the model version that this task list was
generated against. It must match `^v\d+$`.

## Required Task Fields

Each task must contain:
- `task_id`
- `title`
- `type`
- `loop_order`
- `process`
- `lagrangian_terms`
- `external_particles`
- `target_quantity`
- `on_shell`
- `priority`
- `notes`

Additional requirement:
- `loop_particles` is required whenever `type` is `"loop"`

## Tree/Loop Rules

- `type: "tree"` must use `loop_order: 0`
- `type: "loop"` must use `loop_order: 1`
- Do not emit tasks with `loop_order > 1`

## `external_particles` Encoding

`external_particles` must be a structured object that carries both particle
identity and leg kinematics.

Rules:
- Use exact `fields[].name` values for every model-specific or new particle
- Standard SM particle labels such as `mu`, `mu_bar`, `tau`, `gamma`, `Z`, or
  `Wplus` may be used directly, but keep the naming convention consistent across
  the project
- Put momentum labels such as `p1`, `p2`, or `q` in sibling `momentum` fields
- Do not embed momentum labels inside the particle name string
- Use semantic leg-role keys such as `incoming`, `outgoing`, `virtual_boson`,
  or `mediator` as needed by the process

Typical leaf forms:
- single leg object: `{"particle": "Zp", "momentum": "q"}`
- list of leg objects under one role: `[{"particle": "mu", "momentum": "p1"}]`

## Conventions and Naming

- Task files inherit default conventions from `model-spec.json`
- Do not repeat global conventions in each task
- Only add `convention_overrides` if a task genuinely needs a different local
  convention
- All parameter names must match `model-spec.json` exactly
- All model-specific or new particle names must match `model-spec.json`
  exactly; standard SM particle labels may be used directly if they follow the
  shared project naming convention

## Authoring Checklist

- Decompose observables into tasks that package-scribe can execute directly
- Include enough structured kinematic context for downstream execution
- Prefer one task per directly meaningful observable unless a different split is
  clearly better for the calculation pipeline
