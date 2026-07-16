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
Figures, summaries, comparison, replotting, and workspace validation MUST reject
a partial or malformed pair.

## `scan.csv` Hard Rules

`scan.csv` is the row-level data product.

Hard rules:

1. Encoding is UTF-8.
2. The first row is a header.
3. There is one data row per attempted grid point.
4. Every parameter and observable cell is a finite real scalar.
5. Every persisted point has a complete `allowed` or `excluded` point status;
   a completed scan contains no skipped constraint verdict.
6. Empty cells are permitted only for fields that are semantically optional for
   a successfully evaluated constraint, such as `chi2` on an upper limit and
   `skip_reason` on a non-skipped verdict.
7. The header and order equal the config-derived columns exactly; extra,
   missing, duplicated, or reordered columns are invalid.
8. Scan coordinates form one unique complete Cartesian grid with no duplicated
   or omitted point.

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

## Row Semantics And Fail-Closed Publication

Each persisted row is one successfully evaluated attempted grid point.

- Parameter columns record the exact finite numeric values used for that point.
- Observable columns record finite scalar predictions.
- Constraint columns record the successful evaluation result for every used
  constraint.
- Rows are never dropped to hide a failed point, but failed/skipped rows are
  also never published as a partial successful scan.

`run_scan.py` evaluates the requested grid in memory first. If any observable
is unavailable/non-finite, any constraint result is malformed/non-finite, or
any constraint/point is `skipped`, the command exits nonzero before writing or
refreshing `scan.csv`, `scan.meta.json`, the summary, or manifest history.
Diagnostic skip reasons may be printed, but they do not turn incomplete
evidence into a valid scan pair. If an older result pair already exists, a
failed rerun does not mean that pair was refreshed; consumers must use command
success and metadata rather than file existence alone.

The row count MUST equal the product of all scan parameter grid sizes.
If the runner intentionally changes traversal or sampling, that change must be
reflected in both `scan.meta.json` and tests.

## Constraint Columns

Every constraint listed in `constraints_used[]` has these columns. The schema
of the row evaluator includes `skipped`, but a completed persisted scan does
not:

| Column | Presence | Storage rule |
| --- | --- | --- |
| `{id}_verdict` | always | `allowed` or `excluded` in completed output |
| `{id}_margin` | always | signed numeric margin or empty string |
| `{id}_chi2` | always | numeric chi2 for measurement constraints or empty string |
| `{id}_skip_reason` | always | empty string in completed output |

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
| `scan_config_source` | Exact UTF-8 config source decoded without newline normalization. |
| `scan_config_sha256` | SHA-256 of those exact source bytes. |
| `model_version` | Model version copied from `depends_on`. |
| `model_checksum` | Model checksum copied from `depends_on`. |
| `seed` | Seed from the config or default. |
| `rng` | PCG64 algorithm/version, SeedSequence substream scheme, seed, phases, and consumers. |
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
| `scan_csv_sha256` | SHA-256 of the exact persisted `scan.csv` bytes. |
| `input_provenance` | Verified exact-byte graph over every scan data/code dependency. |

`scan_config_snapshot`, exact source, and source SHA-256 are mandatory and must
agree. A path to the config is not enough because the config may be edited
after the run. Consumers compare the live execution projection with the frozen
snapshot; renderer-only drift is attested separately rather than relabeling the
scan.

For every newly completed scan, `n_points` equals the CSV row count,
`n_allowed + n_excluded == n_points`, and `n_skipped == 0`. Any internal
skipped count blocks publication instead of being serialized in metadata.
The four counts are recomputed from the strict CSV, the summary contains exact
matching count markers, `scan_csv_sha256` matches current bytes, and the graph
is independently re-derived and verified before any consumer proceeds.

## Figure Generation Sidecar

Every published figure generation owns
`numerics/figures/{analysis_id}/figures.meta.json`. The sidecar records the
immutable scan CSV/meta hashes, hash of the frozen execution projection, full
live render snapshot, renderer contract, exact PDF/PNG output hashes, and a
verified dependency graph. The figure directory contains exactly the sidecar
and those outputs. Missing, extra, empty, stale, or hash-mismatched files fail
workspace validation. Replot replaces that directory transactionally while
leaving the scan pair byte-identical.

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

New numerics history events MUST include a fresh 32-character lowercase-hex
`event_id`. Timestamp precision is one second, so event identity—not equality
of action/timestamp/note—distinguishes two genuine same-second operations.
Validators accept an absent ID only for legacy read compatibility and reject
duplicate IDs. New events also use the explicit `analysis_id`; the exact
note-token fallback is legacy-only.

## Manifest-v2 Ownership And Publication

`manifest_version = 2` records one object per analysis under
`artifacts.numerics.analyses`. The object owns only that analysis's canonical
config, strict scan pair, summary, optional custom module, figures, and exact
model/calculation/constraints dependency snapshot. Updating or replotting one
analysis preserves every unrelated entry byte-for-byte except the derived
aggregate projection and a conservative `done`/`partial` to `stale` transition
when that entry's exact inputs drift. The aggregate files, status, producer,
and timestamp are computed according to
`docs/contracts/numerics-manifest-ownership.md`.

`run_scan.py` and `make_figures.py` stage and validate all candidates, then use
the shared project publication lock, durable journal, destination CAS, and
manifest-last ordering described in
`docs/contracts/transactional-state-publication.md`. A stale analysis still
requires finite, complete, internally consistent historical evidence; only
matching its recorded dependency hashes to changed current bytes is skipped.

Manifest v1 is never upgraded implicitly. Use
`scripts/migrate_manifest_v2.py` without `--write` to diagnose, review the
candidate, and then opt into a transactional `--write` migration. Ambiguous or
damaged legacy evidence is a hard failure, not a reason to drop an entry.
