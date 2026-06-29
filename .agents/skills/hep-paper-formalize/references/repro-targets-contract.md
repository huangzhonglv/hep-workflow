# repro-targets.json Contract

## Purpose

`literature/repro-targets.json` lists the paper results this project intends to
reproduce or compare against. It is the target catalog consumed by
`scripts/compare_to_reference.py`.

## Shape

Top-level object with:

- `paper_id`
- `targets[]`

Each target contains:

- `id`
- `kind`
- `x_param`
- `y_param`
- `observables[]`
- `fixed`
- `constraints_in_paper[]`
- `data_file`
- `tolerance`
- optional `expected_qualitative`

Use `templates/repro-targets.example.json` for example shape only. The
repository schema `schemas/repro-targets.schema.json` is authoritative for
required fields, target id pattern, target kind enum, and
`additionalProperties: false`.

## Target Kinds

### `formula`

Use for analytic expressions quoted by the paper. These targets usually need
qualitative or human-reviewed comparison. The paper formula remains a benchmark
or display reference, never a calculation backend.

### `benchmark_point`

Use for one or a small number of numerical benchmark values at fixed parameter
points.

### `scan_table`

Use for tabulated paper scans or benchmark grids.

### `figure_curve`

Use for digitized one-dimensional or parametric curves from a figure. The
`data_file` normally points into `literature/digitized/`.

### `exclusion_region`

Use for digitized boundaries or regions in an exclusion/allowed-region figure.
The comparison may be qualitative when the paper does not expose enough raw
data for a robust metric.

## Setup vs Formalize Constraint IDs

During Setup mode there is no canonical `c-001` style constraint ID yet. Use
paper-local labels in `constraints_in_paper[]`, such as `"MEG bound 2016"` or
`"Tab. 2 trident bound"`.

During Formalize mode, after `constraints/constraints-data.json` creates
canonical IDs, update `literature/paper-extract.json` with:

```json
{
  "paper_local_to_canonical": {
    "MEG bound 2016": "c-001"
  }
}
```

Then refresh `repro-targets.json` to use canonical IDs while preserving the old
label for audit with `_label_was` where the target shape allows it or in a
nearby human-readable note.

## Hard Invariants

- `paper_id` matches `literature/paper-meta.json`.
- `id` matches `^[a-zA-Z][a-zA-Z0-9_-]*$`.
- `x_param`, `y_param`, and `observables[]` use canonical names once a model
  exists.
- `fixed` keys use canonical parameter names.
- `tolerance.kind` is `relative`, `absolute`, or `qualitative`.
- Do not set a tolerance after seeing comparison results.

## Authoring Checklist

- Include at least one target for each paper result the user selected.
- Use paper-local constraint labels during Setup mode only.
- Record missing digitized CSVs honestly; do not invent data points.
- Validate against `schemas/repro-targets.schema.json`.
