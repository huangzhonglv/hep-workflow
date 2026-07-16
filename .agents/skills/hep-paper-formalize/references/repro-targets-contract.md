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
- `match_columns[]` for keyed numeric targets
- `scan_parameters[]` for every non-formula target
- `observables[]`
- `fixed`
- `constraints_in_paper[]`
- `data_file`
- `tolerance`
- `normalization` for every non-formula target
- kind-specific comparison declarations described below
- optional `expected_qualitative` only for formula targets or exclusion-region
  context that explicitly requires a human qualitative note; quantitative
  curve/table/benchmark verdicts remain metric-first

Use `templates/repro-targets.example.json` for example shape only. The
repository schema `schemas/repro-targets.schema.json` is authoritative for
required fields, target id pattern, target kind enum, and
`additionalProperties: false`.

Every acquisition `acquired_at` value uses the one canonical UTC-second form
`YYYY-MM-DDTHH:MM:SSZ`. Offset spellings, fractional seconds, missing `Z`, and
calendar-invalid values are rejected rather than normalized implicitly.

## Target Kinds

### `formula`

Use for analytic expressions quoted by the paper. These targets usually need
qualitative or human-reviewed comparison. The paper formula remains a benchmark
or display reference, never a calculation backend.

`data_file` points to a JSON object conforming to
`schemas/formula-reference.schema.json`, not to an empty marker or free-form
text file. The record binds `paper_id`, `target_id`, the nonempty expression,
the paper location, and acquisition time. Formula reference evidence is still
comparison-side evidence and does not prove an independent derivation.

### `benchmark_point`

Use for exactly one numerical reference row at one fixed parameter point. The
comparator blocks zero rows and multiple rows. Declare the exact match columns;
every declared observable is compared.

### `keyed_benchmark_set`

Use for two or more numerical benchmark rows. Declare a nonempty unique
`match_columns[]` key. Each reference key must identify exactly one reference
row and exactly one row on the declared scan slice. Missing keys, duplicate
keys, partial observable values, or less than full reference coverage block the
target.

### `scan_table`

Use for tabulated paper scans or benchmark grids. Declare `match_columns[]`
explicitly; the comparison script never infers a smaller key from the columns
that happen to be shared by two CSV files. The list must include `x_param` and
`y_param`, contain no duplicates, and be disjoint from `observables[]`.

The default and currently supported completeness policy is strict: every
reference row must match exactly one scan row and every declared observable
must provide one finite numeric value on both sides. Extra scan rows are
allowed. Missing keys, duplicate keys, missing or non-finite observable values,
or partial reference coverage block the target instead of producing a metric.

`fixed` values select the scan slice before matching. Each fixed key must be
present in `scan.csv`; when the digitized table also carries that column, its
rows are filtered to the same declared value.

### `figure_curve`

Use for digitized one-dimensional curves from a figure. The currently
supported representation is only a finite, single-valued `y(x)` curve. Set
`curve_representation` to `single_valued_y_of_x` and declare the
complete comparison interval in `comparison_domain.{x_min,x_max}`.

Both the scan slice and canonical reference data must cover both domain
endpoints. Duplicate x values, rows outside the declared domain, an implicit
subset, interpolation outside either side's support, or non-finite values block
the target. Comparison uses the union of scan and reference knots within the
full declared interval; it does not silently drop internal excursions.

Parametric or multi-valued curves use the separate `parametric_curve` kind. Do
not encode them as `figure_curve`.

### `parametric_curve`

Use for a finite ordered two-coordinate curve that is not safely representable
as single-valued `y(x)`. Set `curve_representation` to
`ordered_parametric_xy`; declare `curve_parameter`, the complete
`parameter_domain.{parameter_min,parameter_max}`, `curve_closed`, and exactly
one positive canonical `coordinate_scales` entry for each of `x_param` and
`y_param`. The path parameter is the only varying scan axis; every other scan
axis is fixed exactly. Each plotted coordinate is either the path parameter or
a declared observable with calculation provenance.

Both canonical reference and scan evidence contain finite, unique path-
parameter nodes, include both declared endpoints, and remain inside the domain.
At least two nodes are required for an open curve and three for a closed curve.
Comparison treats each ordered input as a continuous piecewise-linear path and
uses the bidirectional Hausdorff distance after coordinate normalization. It is
invariant under path reparameterization and subdivision. Tolerance kind is
`normalized_distance`; the fixed numerical bound is recorded, and a bound that
straddles tolerance blocks rather than guessing pass/fail.

### `exclusion_region`

Use for digitized boundaries or regions in an exclusion/allowed-region figure.
The tolerance kind is `normalized_distance`; `coordinate_scales` supplies one
strictly positive normalization scale for each plotted coordinate.

Declare exactly one authoritative boundary construction in `boundary`:

- `observable_threshold`: names the observable, comparison operator, threshold
  value and canonical unit, component/order/closed columns, and a reference
  point known to be excluded.
- `constraint_verdict_transition`: names the constraint and four/eight-neighbor
  connectivity plus the same reference topology metadata. Phase 0 treats this
  mode as blocked until transition edges can be assembled into unambiguous
  ordered paths; do not fall back to raw scan coordinates.
- `precomputed_boundary`: identifies boundary membership, component, order,
  open/closed, region-side, excluded value, and excluded probe explicitly.
  Because the boundary is comparison-side/precomputed evidence, it remains a
  human-review comparison ceiling.

A legacy target with exactly one simple ordered boundary component uses its
single `reference_excluded_probe`. A disconnected or holed target instead uses
`reference_faces[]`; each closed reference component appears exactly once with
a unique `id`, immediate `parent_id` or null root, `interior`/`exterior`
`excluded_side`, and one authoritative `excluded_probe`. Parent/child sides
alternate. Reference IDs/nesting/probes and the predicted one-to-one component
assignment/nesting/excluded status must all verify. Missing, duplicate, or
incomplete declarations, intersecting/touching reference faces, and
ambiguous assignment block; an observed predicted face-count, nesting, or
side/status disagreement fails. Raw scan coordinates are never inferred to be
a boundary. Constraint-verdict transition geometry remains blocked until
transition edges can be assembled into ordered paths.

## Canonical Reference Data And Normalization

For every non-formula target, `data_file` points to the canonical-unit CSV
under `literature/digitized/`. It must not point to a generated scan or
reproduction artifact. The target's `normalization` object also declares:

- `source_data_file`: the distinct immutable raw import/digitization file
- `record_file`: a distinct JSON record conforming to
  `schemas/normalization-record.schema.json`
- exact source and canonical units for every compared column
- a finite linear `factor` and `offset` for every column
- any fixed-parameter conversion record
- acquisition provenance (`source_type`, `paper_id`, `source_locator`,
  `method`, `acquired_at`)

The record binds the raw and canonical paths and SHA-256 hashes. `identity`
means the units and values are unchanged; `converted` must use a supported real
unit conversion. Raw, canonical, and record files must be distinct resolved
objects. Never overwrite the raw data, guess a unit in the comparator, or use a
generated artifact as the source locator. The comparator accepts only data
already normalized into canonical units and verifies the declared
transformation.

Numeric raw CSV lexemes are checked by exact decimal arithmetic. Every
canonical numeric comparison cell must round-trip through finite IEEE-754
binary64 without changing its decimal value, because the metric runtime is
binary64. Block canonical overflow, underflow, or sub-ULP distinctions instead
of silently rounding them; do not impose a fixed significant-digit ceiling on
the raw-to-canonical transformation check.

Fixed values belong in `fixed` and in
`normalization.fixed_parameters`; do not inject constant columns into the raw
paper table merely to satisfy a join.

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

Then refresh `repro-targets.json` to use canonical IDs. The
`paper_local_to_canonical` mapping preserves the paper-local labels for audit;
do not add undeclared per-target fields.

## Readiness Handoff

After Setup has produced schema-valid `paper-extract.json` and
`repro-targets.json`, orchestration must call
`scripts/check_reproduction_readiness.py` for the selected analysis/targets.
It must not reconstruct blockers from prompt text or manifest status.

Formula targets consume only their structured literature reference and remain
human-reviewed; they do not require Formalize, Package-Scribe, or numerics.
Every numeric target requires a verified model and the calculation tasks mapped
to its observables. A complete scan hint additionally requires one verified
owned analysis. An absent hint or nonempty `missing_fields[]` produces the
typed numerics `blocked` state; it does not authorize invented scan inputs.

The complete state semantics are defined by
`docs/contracts/reproduction-readiness.md` and
`schemas/reproduction-readiness.schema.json`.


## Hard Invariants

- `paper_id` matches `literature/paper-meta.json`.
- `id` matches `^[a-zA-Z][a-zA-Z0-9_-]*$`.
- `x_param`, `y_param`, and `observables[]` use canonical names once a model
  exists.
- Every non-formula target declares the scan axes needed to identify its
  comparison input in `scan_parameters[]` and a complete normalization record.
- A `benchmark_point`, `keyed_benchmark_set`, or `scan_table` target declares
  nonempty, unique `match_columns[]` containing `x_param` and `y_param`; match
  columns do not overlap `observables[]`.
- `benchmark_point` has exactly one reference row;
  `keyed_benchmark_set` has at least two selected reference rows.
- A `figure_curve` declares its complete domain and single-valued
  representation.
- A `parametric_curve` declares its complete path-parameter domain, ordered
  representation, open/closed topology, and exact coordinate scales.
- An `exclusion_region` declares coordinate scales, boundary provenance,
  component/order/open-closed metadata, and either one legacy probe or complete
  per-face parent/side/probe semantics.
- `fixed` keys use canonical parameter names.
- Formula tolerance is `qualitative`; single-valued curve/table/benchmark
  tolerance is `relative` or `absolute`; parametric/exclusion tolerance is
  `normalized_distance`.
- Do not set a tolerance after seeing comparison results.

## Authoring Checklist

- Include at least one target for each paper result the user selected.
- Use paper-local constraint labels during Setup mode only.
- Record missing digitized CSVs honestly; do not invent data points.
- For formula targets, write and validate structured formula reference evidence.
- For numeric targets, preserve the raw source, write a canonical-unit copy,
  and write the hash-bound normalization record before comparison.
- Record acquisition source and locator precisely enough to audit against the
  paper; generated workspace paths are not paper provenance.
- Do not lower table coverage after seeing comparison results. Partial table
  coverage is blocked under the current contract.
- Do not encode a parametric curve as `figure_curve`, omit parameter endpoints,
  compare only selected knots, or project extra varying scan axes.
- For disconnected/holed exclusions, declare every face and its parent, side,
  and excluded probe; never infer missing topology from component ordering.
- Do not silently choose a curve subdomain, extrapolate, aggregate hidden scan
  axes, infer a boundary, or change tolerance after seeing results.
- Validate against `schemas/repro-targets.schema.json`.
- Validate normalization records against
  `schemas/normalization-record.schema.json` and formula records against
  `schemas/formula-reference.schema.json`.
