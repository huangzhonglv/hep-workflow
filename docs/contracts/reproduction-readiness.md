# Reproduction Target Readiness

Reproduction readiness is a deterministic, read-only projection of current
workspace evidence. Its machine-readable shape is authoritative in
`schemas/reproduction-readiness.schema.json`; it is derived by
`scripts/check_reproduction_readiness.py` and is never persisted in
`manifest.json`.

## Required Stages

Every selected target requires schema-valid literature artifacts and valid
reference evidence. Requirements after that are target-kind specific:

| Target kind | Model | Calculations | Numerics |
| --- | --- | --- | --- |
| `formula` | not applicable | not applicable | not applicable |
| every numeric kind | required | required | required when scan hints are complete |

The numeric kinds are `benchmark_point`, `keyed_benchmark_set`, `scan_table`,
`figure_curve`, `parametric_curve`, and `exclusion_region`.

A formula target is a qualitative comparison of structured literature evidence.
It does not silently inherit an ambient model, calculation, or scan, cannot
receive a mechanical `pass`, and must retain a human-review ceiling.

## State Meanings

- `ready`: the required current artifacts are present, schema-valid, and their
  exact dependency evidence verifies.
- `missing`: a required artifact, task mapping, result, or manifest owner is
  absent.
- `invalid`: an artifact or cross-file identity is structurally or semantically
  inconsistent.
- `stale`: a required content-addressed dependency graph or owned analysis no
  longer matches current bytes.
- `blocked`: numerics cannot be planned because the selected numeric target has
  no scan hint or declares nonempty `missing_fields`.
- `not_applicable`: the target kind does not consume that stage.

Only the numerics stage may be `blocked`. A target is `not_ready` whenever any
required stage is `missing`, `invalid`, or `stale`. It is `blocked` only when
all other required stages are ready and numerics is blocked. Otherwise it is
`ready`.

## Routing And Trust

The orchestrator reads typed stage states and dispatches the owner of the first
unready prerequisite: Setup literature to `hep-paper-formalize`, model
formalization to `hep-paper-formalize`, calculation results to `package-scribe`,
and scans to `hep-numerics`. Status mode reads the same projection rather than
reconstructing readiness from manifest prose.

Manifest status and history remain routing hints, never scientific evidence.
Readiness recomputes exact model identity, calculation dependency graphs, scan
ownership, scan artifact validity, and frozen scan dependencies. A selected
numeric analysis is ready only when its unique `done` manifest entry owns all
four canonical scan artifacts, every declared owned file exists, the aggregate
file projection is exact, and its model/calculation/constraint dependencies
match the immutable scan snapshot and graph.

The CLI exits zero for both `routable` and typed `not_ready` reports so callers
can route from JSON. Missing or invalid report preconditions, malformed input,
or inability to derive a schema-valid report exit nonzero and fail closed. The
helper must not write files, update manifest state, allocate IDs, recover
transactions, or decide scientific verdicts.

`compare_to_reference.py` proceeds only when every selected target is `ready`
or honestly `blocked`. Its deprecated `--blocked-targets` option is a strict
compatibility assertion: duplicates and unknown IDs fail, and a supplied set
must exactly equal the derived blocked set. It can never create or clear a
blocker.

Generated reproduction metadata lists only files that exist and whose content
was validated. A blocked target may have a real diagnostic overlay, but no
planned or missing file may be represented as generated evidence.
