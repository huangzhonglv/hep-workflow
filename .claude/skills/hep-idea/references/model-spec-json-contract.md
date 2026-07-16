# model-spec.json Contract

## Purpose

`model/model-spec.json` is the machine-readable source of truth for the model.
Downstream modules read this file directly; do not introduce alternate required
model-definition files.

## Shape

Top-level object with:
- `model_name`
- `version`
- `gauge_symmetry`
- `tags`
- `fields`
- `parameters`
- `interactions`
- `conventions`
- optional `symmetry_breaking`

Use `templates/model-spec.example.json` for example shape only. The contract in
this file is authoritative for required fields, conditional fields, and
cross-file invariants.

## Required Entry Shapes

### `fields[]`

Each entry must contain:
- `name`
- `latex`
- `spin`
- `quantum_numbers`
- `mass_parameter`
- `is_new`
- `propagator_note`

If the field is massless, `mass_parameter` must still be present and may be
`null`.

### `parameters[]`

Each entry must contain:
- `name`
- `latex`
- `type`
- `description`
- `unit`
- `role`

Conditional requirements:
- `role: "scan"` requires `suggested_range`
- `role: "fixed"` requires `value`
- `role: "derived"` requires `derivation`

### `interactions[]`

Each entry must contain:
- `id`
- `latex`
- `particles`
- `lorentz_structure`
- `chirality`
- `coupling`
- `feynman_rule_note`

### `conventions`

Must contain:
- `gauge`
- `momentum_flow`
- `gamma5_scheme`
- `metric_signature`

### `symmetry_breaking`

Optional object. Include only when it is physically relevant or needed to avoid
ambiguity.

## Hard Invariants

- For initial hep-idea output, set `version` to `"v1"`.
- Machine-readable canonical names must match `^[A-Za-z_][A-Za-z0-9_]*$`
  and must not be Python hard keywords; each name is project-global and
  immutable, and downstream artifacts must reuse it exactly. See
  `docs/contracts/canonical-name-convention.md`.
- Every model-specific or new particle referenced downstream must match a
  `fields[].name` entry in this file exactly.
- Standard SM particle labels may appear directly in downstream task or process
  descriptions without being redundantly expanded into `fields[]`, but they
  should use one consistent shared naming convention rather than ad hoc aliases.
- `conventions` is the default source for downstream convention handling.
  Downstream task files should not duplicate these conventions unless they need
  a rare explicit override.

## Authoring Checklist

- Keep `model_name` consistent across all project JSON artifacts.
- Encode the full model needed by downstream tools; do not rely on prose-only
  assumptions from `proposal.md`.
- Prefer explicit field and interaction entries over implicit assumptions.
- Register every model-specific or new particle in `fields[]` so downstream
  tasks can reference it unambiguously.
