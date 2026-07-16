# Scan Config JSON Contract

This file defines the semantic contract for
`numerics/scan-configs/{analysis_id}.json`.

## Source of Truth

- Schema syntax: `schemas/scan-config.schema.json`.
- Runtime behavior: `scripts/validate_scan_config.py` and `scripts/run_scan.py`.
- Template and canonical example: `scripts/init_analysis.py` and `schemas/examples/scan-config.example.json`.
- This reference: cross-file semantics, failure modes, and authoring rules.

## What The Config Means

A scan-config is the complete request for one numeric analysis.
It chooses:

1. which model snapshot the scan depends on
2. which model parameters vary and which are fixed
3. which observable columns must be produced
4. which constraints are evaluated at every point
5. which figures should be rendered from the resulting table

The config is copied into `scan.meta.json.scan_config_snapshot`.
That snapshot must be sufficient to explain the run without relying on the
mutable current scan-config file.

## Analysis Namespace

`analysis_id` is the namespace for all numerics outputs.

For `analysis-001`, the derived paths are `numerics/scan-configs/analysis-001.json`,
`numerics/scan-results/analysis-001/`, `numerics/figures/analysis-001/`, and
`numerics/analysis-summary-analysis-001.md`.

The file name and `analysis_id` value must agree.
Do not reuse one `analysis_id` for a different physics question unless the user
explicitly asks for a rerun that overwrites the same analysis.

## Cross-File Invariants

These checks go beyond JSON shape.

1. `depends_on.model_version` must equal `manifest.json.active_model_version`.
2. `depends_on.model_checksum` and the manifest checksum must both equal the
   recomputed SHA-256 of the exact `model/model-spec.json` bytes. Matching
   self-claims are not evidence.
3. Every `depends_on.task_ids[]` entry must exist in `model/calc-tasks.json`.
4. Every task-backed observable must reference a task listed in `depends_on.task_ids[]`.
   Because one task result declares one return observable, the same task cannot
   be rebound to multiple public observable names, and any constraint using the
   task directly must name that same return observable.
5. Task-backed observables require `calculations/{task_id}/result-meta.json`.
6. A task result used by numerics must have `translation_status == "complete"`,
   a `verified` exact-byte `input_provenance` graph whose independently derived
  coverage and hashes pass, and acceptable provenance.
  `calculation_provenance == "blocked"` is always
   rejected. `calculation_provenance == "package_x_derived"` must not use the
   benchmark as an input and must list `package_x_methods`.
7. Every scan or fixed parameter must exist in `model/model-spec.json`.
8. Every scan parameter should have `role == "scan"` in `model-spec.json`.
9. A parameter may appear in `scan_parameters[]` or `fixed_parameters[]`, not both.
10. Names are unique within each of `scan_parameters[]`,
    `fixed_parameters[]`, `observables[]`, and `constraints_used[]`.
11. A log-scale scan range must have strictly positive lower and upper bounds.
12. Every `constraints_used[]` id must exist in `constraints/constraints-data.json`.
    The list is nonempty for a completed authoritative scan.
13. A constraint's observable must be covered by an observable binding, a model
    parameter, a parameter-combination rule, an external source, or a manual-only
    skip path.
14. Every figure axis must use a model canonical parameter name.
15. Every figure's `fixed` object must name exactly all scan parameters not
    used as visible axes and no visible or unknown parameter. The renderer uses
    exact numeric equality to select that slice.
16. Every `exclusion_2d.constraints[]` id must also appear in `constraints_used[]`.
17. Every `scan_1d.observables[]` entry must also appear in `observables[]`.

Failure of any invariant above is a pre-scan hard failure.
Formula fallback task backends (`literature_formula_imported` or
`manual_tree_algebra`) are usable only when the config explicitly sets
`allow_formula_fallback: true`; otherwise they are a pre-scan hard failure.

## Canonical Names

Machine-readable canonical names must match `^[A-Za-z_][A-Za-z0-9_]*$`
(ASCII letter or underscore first, followed by ASCII letters, digits, or
underscores), must not be a Python hard keyword, and must reuse the exact names from
`model/model-spec.json`; see `docs/contracts/canonical-name-convention.md`.
This applies to parameter fields, figure axes, custom-observable keyword
arguments, and model-parameter references in formulas. Resolve display input
through `model-spec.json.parameters[].latex`; if no unique existing name
matches, stop instead of inventing an alias.

## Field Semantics

### `model_name`

Human-readable label for the model family.
It does not select files and must not be used as a compatibility key.
Compatibility is enforced through `depends_on` and canonical artifact paths.

### `description`

Short prose for humans and summaries.
It may mention benchmark choices, fixed parameters, and constraints, but it must
not contain machine-only information that is absent from structured fields.

### `depends_on`

`depends_on` freezes the upstream model and task snapshot that the scan expects.

- `model_version` guards against silently scanning the wrong active model.
- `model_checksum` is a declared snapshot that is accepted only after the
  validator recomputes the exact model file hash and proves equality.
- `task_ids` lists the calculation tasks whose outputs may be called by the scan.

If the upstream model or required task results changed, create a new config or
explicitly rerun after updating `depends_on`; do not bypass this gate.

### `allow_formula_fallback`

Default: `false`.

This boolean is the explicit opt-in for task-backed observables whose
`result-meta.json.calculation_provenance` is `literature_formula_imported` or
`manual_tree_algebra`. Setting it to `true` means the scan is intentionally
using a formula fallback backend rather than a Package-X-derived backend.

When fallback is allowed, the runner records the fallback task list in
`scan.meta.json.formula_fallbacks`, adds run-level warnings, and includes a
formula fallback provenance section in the analysis summary.
`init_analysis.py` keeps this field false unless the user supplies the explicit
`--allow-formula-fallback` flag; discovering a usable fallback is not consent.

### `scan_parameters`

Each entry defines one axis of the grid.

Semantic rules:

- The order is the column order in `scan.csv`.
- The order is also the nested grid traversal order used by the runner.
- The canonical name must exist in `model-spec.json`.
- The parameter should be declared scan-capable by its model role.
- Log-scale ranges must be strictly positive.
- The grid should be physically meaningful; the schema only checks shape.
- All generated parameter values must be finite. A runtime non-finite value is
  a hard failure even if the JSON configuration was structurally valid.

### `fixed_parameters`

Each entry pins a model parameter to one numeric value for all rows.

Semantic rules:

- Fixed parameters appear in `scan.csv` after scan parameters, in config order.
- A fixed parameter cannot also be scanned.
- A fixed parameter must be a known model canonical name.
- If a required task backend needs a parameter not scanned or fixed, the runner
  may use model defaults only when the project artifacts expose them safely.

### `observables`

Each binding creates one public `scan.csv` observable column.
The array may be empty when every selected constraint is computed directly from
model parameters or an interpolation table; initialization must not invent a
placeholder observable merely to populate it.

For `source.type == "task"`:

- `task_id` must exist in `calc-tasks.json`.
- `task_id` must be listed in `depends_on.task_ids`.
- The translated task backend must be importable and complete.
- The task backend provenance must pass the rules above, including explicit
  `allow_formula_fallback` opt-in for formula fallback backends.

For `source.type == "custom"`:

- `numerics/custom_observables.py` must exist.
- The named function must import successfully.
- The function must pass the custom observable smoke test.
- A function accepting `task_outputs` requires an exact, non-empty
  `source.task_ids` list; it receives only those verified finite task wrappers.
  Each wrapper accepts only that task's declared finite canonical parameters;
  unknown keywords, booleans, `NaN`, and infinity fail instead of being ignored.
- A stochastic function explicitly accepts the local `rng`; ambient
  randomness/entropy is forbidden.
- `source.canonical_unit` is required and declares the canonical unit of the emitted
  `scan.csv` column. A reproduction target must use that exact canonical unit.

Observable names are output column names.
They should be stable, ASCII-friendly identifiers, even though the schema allows
more general strings.

### `constraints_used`

This ordered list controls constraint evaluation and output columns.

For each id, `scan.csv` receives:

- `{id}_verdict`
- `{id}_margin`
- `{id}_chi2`
- `{id}_skip_reason`

The order of ids is the order of these column families.
Manual-only constraints are allowed, but they evaluate to skipped rows and must
not be treated as evidence for the allowed region. Under the Phase-0
fail-closed publication rule, selecting one in an automated run causes
`run_scan.py` to exit nonzero without publishing a partial scan. Keep it in the
project catalog for human work, but do not select it as automated evidence until
implemented.

### `figures`

Figure specs never change scan data.
They only request views over columns that the scan already produces.

`exclusion_2d` requires both axes to be scanned parameters and every listed
constraint to appear in `constraints_used[]`.

`scan_1d` requires `x` to be a scanned parameter and every requested observable
to appear in `observables[]`.

For either kind, `fixed` must contain exactly every hidden scan parameter and
no other key. A two-dimensional figure from a three- or four-dimensional scan
is therefore one explicit exact slice, not a projection. A one-dimensional
figure likewise fixes every other scan axis. The renderer rejects an empty
slice, non-finite slice columns, and duplicate coordinates; nearest-value
matching, `isclose`, median/first-row aggregation, and duplicate dropping are
forbidden.

Replot-only workflows may rerun `make_figures.py` when the live config's scan
execution projection is exactly equal to the immutable
`scan_config_snapshot`. A figure `title`, prose `description`, or the
non-scientific `parallelism` hint may change without relabeling the scan.
Axes, slices, requested observables/constraints, overlays, ranges, bindings,
seed, dependencies, and other execution semantics remain hard blockers and
require a new scan. Each successful render publishes `figures.meta.json` with
the full live figure request, immutable scan hashes, output hashes, and a
verified renderer input graph. Replot must not call `run_scan.py` or rewrite
`scan.csv`/`scan.meta.json`.

## Fail-Closed Runtime Completion

A schema-valid configuration does not authorize partial scientific coverage.
During execution, every attempted grid point must produce a finite scalar for
each configured observable and a complete finite result for each configured
constraint. A skipped verdict, missing prediction, exception, boolean,
non-scalar, `NaN`, or infinity aborts the complete run before `scan.csv`,
`scan.meta.json`, the summary, or manifest history is written or refreshed.
Do not weaken `constraints_used[]`, drop points, or substitute defaults merely
to obtain a successful command.

### `seed` And `parallelism`

`seed` is required and drives explicit local `numpy.random.PCG64` generators.
The runner derives separate smoke (`0`) and scan (`1`) substreams, then derives
each point/consumer stream with `numpy.random.SeedSequence`. A stochastic custom
callable must explicitly accept `rng`; ambient RNG/entropy access fails
preflight. `scan.meta.json.rng` records `pcg64-v1`,
`numpy-seedsequence-v1`, the seed, fixed phase IDs, and canonical consumers.
The same seed/config/code/environment reproduces the same generated stream;
global seeding is never part of the contract.

`parallelism` is an execution hint.
Changing it should not change scientific results; if it does, treat that as a
bug in the backend or custom observable.

## Common Counterexamples

Bad canonical name, followed by the correct canonical version:

```json
{"canonical_name": "M_{H^{++}}", "range": [100, 2000], "grid": 20, "scale": "log"}
{"canonical_name": "M_Hpp", "range": [100, 2000], "grid": 20, "scale": "log"}
```

Bad figure reference:

```json
{"kind": "scan_1d", "x": "M_Hpp", "observables": ["Br_tau_to_mugamma"]}
```

Fails if `Br_tau_to_mugamma` is not declared in `observables[]`.

Bad hidden-axis projection for a scan over `M_Hpp`, `v_Delta`, and
`m_lightest`:

```json
{"kind": "exclusion_2d", "x": "M_Hpp", "y": "v_Delta", "constraints": ["c-001"]}
```

It must declare the exact slice, for example:

```json
{"kind": "exclusion_2d", "x": "M_Hpp", "y": "v_Delta", "constraints": ["c-001"], "fixed": {"m_lightest": 0.0}}
```

Bad task binding:

```json
{"observable": "Br_mu_to_egamma", "source": {"type": "task", "task_id": "task-009"}}
```

Fails if `task-009` is absent from `depends_on.task_ids[]` or `model/calc-tasks.json`.

## Canonical Example

Use `schemas/examples/scan-config.example.json` as the canonical minimal shape.
It demonstrates:

- two log-scale scan axes
- fixed benchmark parameters
- task-backed and custom observables
- three constraints used by both scan results and figures
- one `exclusion_2d` figure and one `scan_1d` figure

Copy the structure, not the physics choices.
Every real project must resolve names and ids against its own workspace files.

## Authoring Checklist

- [ ] File name matches `analysis_id`.
- [ ] `depends_on` matches the current manifest model version and checksum.
- [ ] The model checksum was recomputed from exact bytes and every selected
      task has a complete verified dependency graph.
- [ ] All task-backed observables are listed in `depends_on.task_ids`.
- [ ] Every parameter field uses a model canonical name.
- [ ] No parameter is both scanned and fixed.
- [ ] Scan, fixed, observable, and constraint names contain no duplicates.
- [ ] Log ranges are strictly positive.
- [ ] Every constraint id exists in `constraints-data.json`.
- [ ] `constraints_used` is nonempty for a completed authoritative scan.
- [ ] Every figure references only columns that the scan will produce.
- [ ] Every figure fixes exactly every hidden scan parameter to one exact value;
      no implicit projection or aggregation is requested.
- [ ] Custom functions exist and pass the smoke test.
- [ ] No selected manual-only/unavailable path would force a skipped point in a
      run intended to publish results.
- [ ] The config can be copied into `scan.meta.json` as the full run snapshot.
