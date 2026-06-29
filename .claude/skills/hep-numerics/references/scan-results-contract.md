# Scan Results Contract

This file defines the output contract for
`numerics/scan-results/{analysis_id}/`.

## Source of Truth

- Schema syntax: `schemas/scan-meta.schema.json` defines the JSON sidecar;
  CSV shape and cross-file consistency are checked by workspace validators.
- Runtime behavior: `scripts/run_scan.py`.
- Template: the active scan-config snapshot in `scan.meta.json`.
- This reference: column order, missing-value policy, metadata semantics, and
  compatibility expectations.

## Result Pair

Every completed scan writes these two files together:

- `scan.csv`
- `scan.meta.json`

They live under:

```text
numerics/scan-results/{analysis_id}/
```

If one file is missing, the analysis is incomplete.
Figures and summaries should not treat a partial pair as a valid scan result.

## `scan.csv` Hard Rules

`scan.csv` is the row-level data product.

Hard rules:

1. Encoding is UTF-8.
2. The first row is a header.
3. There is one data row per attempted grid point.
4. Missing scalar values are serialized as the empty string.

Do not store nested JSON, lists, dictionaries, or multi-line values in cells.
If a value needs structure, put the explanation in `scan.meta.json` or the
analysis summary.

## Column Order Hard Rule

Columns are ordered by the active scan-config:

1. scan parameter columns, in `scan_parameters[]` order
2. fixed parameter columns, in `fixed_parameters[]` order
3. observable columns, in `observables[]` order
4. constraint column families, in `constraints_used[]` order

Each constraint family has exactly this order:

```text
{constraint_id}_verdict,{constraint_id}_margin,{constraint_id}_chi2,{constraint_id}_skip_reason
```

Small example:

```text
M_Hpp,v_Delta,m_lightest,Br_mu_to_egamma,c-001_verdict,c-001_margin,c-001_chi2,c-001_skip_reason
150.0,0.001,0.0,2.0e-13,allowed,0.35,,
```

The empty trailing cells mean `chi2` and `skip_reason` are absent for that
allowed upper-limit constraint.

## Row Semantics

Each row is one attempted grid point, not one successful point.

- Parameter columns record the exact numeric values used for that point.
- Observable columns record scalar predictions or empty strings when unavailable.
- Constraint columns record the evaluation result for every used constraint.
- Rows with skipped constraints stay in the file; they are not dropped.

The row count should equal the product of all scan parameter grid sizes.
If the runner intentionally changes traversal or sampling, that change must be
reflected in both `scan.meta.json` and tests.

## Constraint Columns

Every constraint listed in `constraints_used[]` has these columns:

| Column | Presence | Storage rule |
| --- | --- | --- |
| `{id}_verdict` | always | `allowed`, `excluded`, or `skipped` |
| `{id}_margin` | always | signed numeric margin or empty string |
| `{id}_chi2` | always | numeric chi2 for measurement constraints or empty string |
| `{id}_skip_reason` | always | empty string unless verdict is `skipped` |

The meaning of `verdict`, `margin`, `chi2`, and `skip_reason` belongs to
`constraint-evaluation.md`.
This file only fixes their existence and storage format.

## `scan.meta.json` Field Table

`scan.meta.json` is the run-level sidecar.

| Field | Meaning |
| --- | --- |
| `analysis_id` | Analysis namespace used for paths. |
| `history_action` | Manifest history action chosen for the scan. |
| `scan_config_snapshot` | Full scan-config object used for the run. |
| `model_version` | Model version copied from `depends_on`. |
| `model_checksum` | Model checksum copied from `depends_on`. |
| `seed` | Seed from the config or default. |
| `started_at` | UTC start timestamp. |
| `finished_at` | UTC finish timestamp. |
| `timing_seconds` | Wall-clock scan duration. |
| `timing` | Structured timing block for compatibility. |
| `n_points` | Number of attempted grid points. |
| `n_allowed` | Count of rows classified allowed by runtime summary. |
| `n_excluded` | Count of rows classified excluded by runtime summary. |
| `n_skipped` | Count of rows classified skipped by runtime summary. |
| `environment` | Python and package versions relevant to the run. |
| `formula_fallbacks` | Explicitly allowed fallback task backends used by the run. Empty when none were used. |
| `warnings` | Run-level warning strings. |

`scan_config_snapshot` is mandatory in practice.
A path to the config is not enough because the config may be edited after the
run.

## Manifest History Entry Fields (Cross-Reference)

> **NOT to be confused with `scan.meta.json.analysis_id`** documented above.
> That field lives inside the per-analysis scan metadata sidecar.
> The field documented in this section is a **separate, optional** field
> on each entry of the project-level `manifest.json`'s `history[]` array.

### Schema

`manifest.history[]` is defined by `schemas/manifest.schema.json`. Each
history entry MAY include `analysis_id` to associate the entry with a
specific numerics analysis:

| Field | Type | Required | Pattern | Meaning |
| --- | --- | --- | --- | --- |
| `action` | string | yes | (one of allowed actions; see SKILL §7) | Action name |
| `timestamp` | string | yes | ISO 8601 UTC, `^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$` | When the action occurred |
| `analysis_id` | string | **optional** | `^analysis-\d{3}$` | Which numerics analysis this entry pertains to |
| `note` | string | optional | (free text) | Additional context |

### Consumer Behavior

`hep-numerics/scripts/make_figures.py:determine_manifest_history_action`
(and any future consumer that needs to disambiguate per-analysis history
entries) MUST follow this lookup order when checking whether a given
`(action, analysis_id)` pair is already recorded:

1. Read `entry["analysis_id"]` directly. If it equals the target
   `analysis_id`, the entry is a match.
2. Otherwise, parse `entry["note"]` (if present) for the substring
   `"analysis_id=<target>"`. Legacy entries written before this field was
   added use this pattern, and consumers must continue to recognize it.

This dual-path disambiguation is intentional: the explicit field is the
preferred forward-compatible form, while note-pattern parsing keeps the
contract working with existing fixtures that predate the schema field.

### Writing the Field

Producers (currently `run_scan.py`, `make_figures.py`, the
`scripts/_manifest.py` shared helper, and any direct fixture editor) MAY
include `analysis_id` in newly written history entries. Doing so is
recommended for new entries because it removes ambiguity and avoids the
`note`-parsing fallback. Existing fixtures are not required to be
backfilled.

### Allowed Values

When present, `analysis_id` MUST equal an `analysis_id` that exists under
`numerics/scan-configs/{analysis_id}.json`. The value is the same as the
analysis identifier used throughout `numerics/`. The pattern check is
also enforced by the schema.
