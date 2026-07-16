# paper-extract.json Contract

## Purpose

`literature/paper-extract.json` is the persisted structured digest of the
paper. It bridges paper reading and project formalization without becoming the
project source of truth. After Formalize mode, `model/model-spec.json` is the
canonical model definition.

## Shape

Top-level object with:

- `paper_id`
- `source`
- `fields[]`
- `parameters[]`
- `interactions[]`
- `constraints_in_paper[]`
- `observables[]`
- `formulas[]`
- `scan_config_hints[]`
- `unit_conversion_notes[]`
- optional `paper_local_to_canonical`
- optional `notes`

The repository schema `schemas/paper-extract.schema.json` is authoritative for
required fields, identifier patterns, formula review flags, and
`additionalProperties: false`. Use `templates/paper-extract.example.json` as
the canonical local example; it is kept byte-identical to
`schemas/examples/paper-extract.example.json`.

## Required Entry Shapes

### `fields[]`

Paper-level field descriptions. Include `paper_name`, `latex`,
`spin_or_type`, `role`, `source_anchor`, and any paper-specific quantum number
or mass information.

### `parameters[]`

Paper-level parameter descriptions. Include `paper_name`, proposed
`canonical_name`, `latex`, `unit`, `role`, `source_anchor`, and known values or
ranges. Canonical names must follow `docs/contracts/canonical-name-convention.md`.

### `interactions[]`

Paper interactions with `label`, `latex`, `particles`, `couplings`, and
`source_anchor`.

### `constraints_in_paper[]`

Paper-local constraint labels before Formalize mode. Each entry should include
`label`, `source_anchor`, `quantity`, `value`, and `notes`.

### `observables[]`

Observable definitions with `name`, `latex`, `description`, `source_anchor`,
and related formula or target labels.

### `formulas[]`

LLM-excerpted text formulas only. Each entry must include `label`, `latex`,
`source_anchor`, and `human_review_required: true`. These formulas may feed
`model/benchmarks.json.formula_latex`, but never `result-python.py`,
`result.wl`, or any calculation backend.

When a formula is selected as a reproduction target, write a separate
structured reference file conforming to `schemas/formula-reference.schema.json`.
That file binds the exact `paper_id`, target id, nonempty expression, source
locator, and acquisition time. A `formulas[]` excerpt or an empty placeholder
file alone is not sufficient comparison evidence.

### `scan_config_hints[]`

Per-target scan hints. Each entry should include:

- `target_id`
- `scan_parameters[]`
- `fixed_parameters`
- `constraints_used[]`
- `grid`
- `missing_fields[]`
- `source_anchor`

`missing_fields[]` is load-bearing for L1 honesty. If the paper omits scan
ranges, grids, fixed parameters, constraint set, observable definitions, or
units, record each omission here instead of pretending the scan is fully
specified.

### `unit_conversion_notes[]`

List paper-to-workspace unit conversions with `quantity`, `paper_unit`,
`workspace_unit`, `conversion`, and `source_anchor`.

These notes are extraction-time audit context, not executable normalization
evidence. For each selected numeric reproduction target, the digitization or
import step must additionally preserve a distinct raw table, write a
canonical-unit table, and write a JSON normalization record conforming to
`schemas/normalization-record.schema.json`. The record contains the exact
finite linear factor/offset for every column, source and canonical units,
acquisition provenance, and SHA-256 hashes of both tables. The comparator never
guesses units or performs an undeclared conversion.

### `paper_local_to_canonical`

Filled by Formalize mode after `constraints/constraints-data.json` assigns
canonical IDs:

```json
{
  "MEG bound 2016": "c-001",
  "Trident bound": "c-002"
}
```

## Hard Invariants

- `paper-extract.json` is not a computational backend.
- Formula excerpts require `human_review_required: true`.
- Do not OCR formulas from rendered PDF images into this file.
- Canonical names proposed here must match `^[A-Za-z_][A-Za-z0-9_]*$`
  and must not be Python hard keywords.
- `missing_fields[]` must be nonempty whenever the paper lacks information
  needed for an L1 data-layer reproduction.
- `unit_conversion_notes[]` does not replace a machine-verifiable normalization
  record for selected numeric targets.
- Raw digitized/imported evidence is immutable. Do not overwrite it with
  canonical values or inject fixed scan parameters that were not columns in the
  paper source.
- After Formalize mode, project-native files are authoritative; this file
  remains an audit trail and extraction intermediate.

## Authoring Checklist

- Include source anchors for every extracted physics statement.
- Separate paper-local labels from canonical IDs.
- Record incomplete scan information in `scan_config_hints[].missing_fields[]`.
- Keep formula excerpts as benchmarks or display references only.
- For every selected numeric target, ensure the raw file, canonical-unit file,
  and hash-bound normalization record are three distinct evidence objects.
- Record source anchors/acquisition locators that point to the paper or trusted
  external source, never to a generated scan or reproduction artifact.
