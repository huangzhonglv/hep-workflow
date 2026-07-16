---
name: hep-numerics
description: >
  Numeric execution layer for HEP workspace projects: numerical scans,
  scan-config validation, constraint decisions, exclusion plots, and analysis
  summary generation. Use this skill when the user asks for numerical scan,
  exclusion plot, run numerics, validate scan-config,
  rerun an analysis, replot figures, scan.csv/scan.meta inspection, or
  custom observable wiring in a HEP phenomenology project.
---

# HEP Numerics

Use this skill as the numeric execution layer for a structured HEP workspace.
It routes validated model, calculation, and constraint artifacts into scans,
constraint decisions, figures, summaries, and manifest updates.

## 1. Skill Responsibilities And Boundaries

- Responsible: read existing workspace artifacts, validate scan-configs, run numeric scans, evaluate constraints, make figures, write summaries, and update numerics manifest entries.
- Responsible: preserve reproducibility through `scan.csv`, `scan.meta.json`, deterministic figure names, and manifest history.
- Responsible: distinguish hard failures from skipped evaluations and skipped
  figures without publishing an incomplete scan as a successful result.
- Not responsible: inventing physics, changing model assumptions, changing experimental data, or deciding whether a model is publishable.
- Not responsible: symbolic derivations, Feynman rules, loop reductions, or Package-X work.
- Not responsible: silently editing `model/`, `constraints/`, or `calculations/` to make a scan pass.
- Not responsible: replacing schema, script, or test checks with prose-only judgment.

## 2. Workspace Inputs And Outputs

Read these workspace inputs when present:

- `manifest.json`
- `model/model-spec.json`
- `model/calc-tasks.json`
- `constraints/constraints-data.json`
- `calculations/task-*/result-meta.json`
- `calculations/task-*/result-python.py`
- `numerics/scan-configs/{analysis_id}.json`
- `numerics/custom_observables.py`

Write only these workspace outputs:

- `numerics/scan-configs/{analysis_id}.json`
- `numerics/scan-results/{analysis_id}/scan.csv`
- `numerics/scan-results/{analysis_id}/scan.meta.json`
- `numerics/figures/{analysis_id}/*.{pdf,png}`
- `numerics/analysis-summary-{analysis_id}.md`
- `manifest.json` numerics artifact entries and allowed history actions

Treat `model/`, `constraints/`, and `calculations/` inputs as read-only.

## 3. Mode Classification

Classify the mode before reading or writing numerics outputs.

| Mode | Required signals | Use when | First action |
| --- | --- | --- | --- |
| `batch` | Workspace root, `manifest.json`, `numerics/`, explicit `analysis_id` | User or orchestrator names an existing or desired analysis | Load the named scan-config or initialize it if Branch I needs one |
| `interactive` | Workspace root, `manifest.json`, `numerics/`, missing or unclear `analysis_id` | User describes scan intent but has not fixed the config | Determine `analysis_id`, then create or edit the scan-config |
| `interactive-standalone` | No complete workspace skeleton | User asks for numerics outside a workspace | Ask for or create the minimum workspace layout before running scripts |

Hard rules:

- Prefer `batch` whenever the project root and `analysis_id` are explicit.
- Prefer `interactive` over guessing when more than one scan-config could match.
- Use `interactive-standalone` only as a fallback; scans still require structured equivalents of the workspace inputs.
- Ask at most one concise clarification when mode or `analysis_id` cannot be inferred safely.

## 4. Branch Classification

Choose the branch from user intent after mode classification.

| Branch | Trigger wording examples | Skip which Step | History action |
| --- | --- | --- | --- |
| Branch I full analysis | "run a new analysis", "scan this project", "make a scan-config and run" | None | `numerics_analysis_complete` |
| Branch II rerun | "rerun analysis-001", "same config, refresh scan", "rerun scan" | Skip Step 1 only when config already exists | `numerics_analysis_rerun` |
| Branch III replot-only | "replot", "make figures again" | Skip Step 1 and Step 3; Step 2 verifies the immutable scan pair/frozen execution snapshot and builds separate renderer provenance | `numerics_figures_regenerated` |

Branch rules:

- Branch I creates or completes the scan-config, validates it, runs the scan, makes figures, writes summary, and updates manifest.
- Branch II reuses the existing scan-config but reruns validation, scan, figures, summary, and manifest update.
- Branch III must reuse an existing strictly verified `scan.csv` and
  `scan.meta.json`. Its live execution projection must match the stored
  snapshot. Title/prose and `parallelism`-hint drift may be rendered under a
  new `figures.meta.json`; scientific figure/config drift requires a new scan.
  It may update figures, figure provenance, summary, and manifest only.
- If the user asks to change model, constraints, or calculations, stop and route that work outside this skill.

## 5. Hard Gates

- Validate before scan: `validate_scan_config.py` must pass before `run_scan.py`.
- Abort before scan-results: hard preflight failure must not create or refresh `numerics/scan-results/{analysis_id}/`.
- Replot does not scan: Branch III must not call `run_scan.py`.
- Do not mutate upstream: never edit `model/`, `constraints/`, or `calculations/` to satisfy numerics validation.
- Do not invent dependencies: stale or missing `depends_on` entries are hard blockers unless the user asks for a new config.
- Recompute and verify dependencies: matching version/checksum strings are not
  proof. The model hash, every task calculation graph, the scan CSV hash, and
  the complete scan graph must verify from exact current bytes before use.
- Do not invent canonical names: unknown machine names are validation errors.
- Do not hide skipped work: skipped constraints, observables, or points need
  explicit failure diagnostics; skipped figures need explicit recorded reasons.
- Fail closed on incomplete point evidence: if any attempted point has an
  unavailable/non-finite observable, invalid/non-finite constraint result, or
  `skipped` constraint/verdict, `run_scan.py` exits nonzero before writing or
  refreshing `scan.csv`, `scan.meta.json`, the analysis summary, or manifest
  history. In-memory skip reasons are diagnostics, not a publishable partial
  scan.
- Numeric evidence must be a real finite scalar. Booleans, arrays, `NaN`, and
  positive/negative infinity are invalid even when Python can coerce them.

## 6. Canonical Name Rule

Machine-readable canonical names must match `^[A-Za-z_][A-Za-z0-9_]*$`
(ASCII letter or underscore first), must not be a Python hard keyword, and
must reuse the exact
`model-spec.json.parameters[].name` values; see
`docs/contracts/canonical-name-convention.md`. Resolve display input only
through `parameters[].latex`; if no unique existing name matches, stop instead
of inventing an alias. Human labels may use `latex`, but machine fields,
filenames, CSV columns, figure axes, and custom-observable keyword arguments
use canonical names.

## 7. Manifest Updates And History Actions

Use scripts for manifest writes whenever possible:

- `run_scan.py` records scan outputs and scan metadata.
- `make_figures.py` records figure outputs and replot-only history.
- `scripts/_manifest.py` contains shared manifest helpers.

Allowed history actions for this skill:

- `numerics_analysis_complete`
- `numerics_analysis_rerun`
- `numerics_figures_regenerated`

New history entries MUST include an explicit `analysis_id` and a fresh
32-character lowercase-hex `event_id`. The fields are defined by
`schemas/manifest.schema.json` and documented in
[`references/scan-results-contract.md`](references/scan-results-contract.md)
under "Manifest History Entry Fields (Cross-Reference)". Consumers may parse
one exact `analysis_id=<id>` note token or accept a missing event ID only when
reading legacy history; current producers never use those compatibility paths.

Manifest rules:

- Require `manifest_version = 2`. `artifacts.numerics.analyses` contains
  analysis-owned objects, never string IDs. Each object owns its exact files,
  dependency snapshot, producer, timestamp, and status; the aggregate is only
  the deterministic projection defined by
  `docs/contracts/numerics-manifest-ownership.md`.
- Merge one analysis without rewriting unrelated analysis entries. The only
  permitted unrelated-field transition is `done`/`partial` to `stale` after an
  exact recorded dependency drifts; stale evidence must still pass intrinsic
  scan-pair and graph structure/coverage validation.
- Manifest artifact paths must match files that exist on disk.
- A full or rerun analysis should point to the scan-config, scan-results, figures, and summary.
- A replot-only update must not claim a new scan was run.
- `status` and history actions are declarations, not evidence. Publish `done`
  only after all configured scan/summary/figure evidence and verified graphs
  pass their mechanical validators.
- Do not add new history action names without updating schemas, scripts, tests, and references.
- Build the candidate with `build_manifest_for_numerics`; do not directly write
  through a legacy updater. `run_scan.py` / `make_figures.py` publish scan or
  figure candidates and the manifest in one shared transaction, manifest last.
  The shared serializer rejects paths outside private transaction staging.
- If a command reports an incomplete publication journal, inspect it with
  `python3 scripts/recover_publication_transactions.py --project-dir <project>
  --format json`; use `--recover` only after reviewing the typed outcome.

## 8. Common Script Commands

Run commands from the repository root unless the user gives another root.

- `python3 <skill_dir>/scripts/init_analysis.py --project-dir <project>` (allocate a new owned ID)
- `python3 <skill_dir>/scripts/init_analysis.py --project-dir <project> --allow-formula-fallback` (explicitly opt a new draft into detected formula-fallback task backends)
- `python3 <skill_dir>/scripts/init_analysis.py --project-dir <project> --reuse-draft` (explicitly open the newest unexecuted initializer draft)
- `python3 <skill_dir>/scripts/init_analysis.py --project-dir <project> --analysis-id <analysis_id> --resume-attempt <attempt_id>` (authenticated interrupted-attempt resume)
- `python3 <skill_dir>/scripts/validate_scan_config.py --project-dir <project> --analysis-id <analysis_id>`
- `python3 <skill_dir>/scripts/run_scan.py --project-dir <project> --analysis-id <analysis_id>`
- `python3 <skill_dir>/scripts/make_figures.py --project-dir <project> --analysis-id <analysis_id>`

Use the installed skill directory for `<skill_dir>`, such as `.claude/skills/hep-numerics` or `.agents/skills/hep-numerics`.

## 9. Step 0–7 Execution Route

### Step 0 — Classify Mode And Branch

Inputs:
- User request
- Current working directory
- `manifest.json`
- `numerics/`
Do:
- Identify workspace root.
- Determine `analysis_id`.
- Select `batch`, `interactive`, or `interactive-standalone`.
- Select Branch I, II, or III.
Script:
- Use filesystem checks; no required script.
Outputs:
- Mode, branch, project directory, and `analysis_id`.
Hard fail:
- No safe workspace or standalone equivalent exists.
- Branch III requested without existing scan results.

### Step 1 — Create Or Load Scan Config

Inputs:
- `manifest.json`
- `model/model-spec.json`
- `constraints/constraints-data.json`
- Existing `numerics/scan-configs/{analysis_id}.json`
Do:
- Load existing config for Branch II or III.
- Create a draft config for Branch I when missing.
- Resolve all parameter display labels to canonical names.
- Add only declared observables, constraints, and figure specs.
Script:
- `scripts/init_analysis.py`
- `references/scan-config-json-contract.md`
Outputs:
- `numerics/scan-configs/{analysis_id}.json`
Hard fail:
- Required upstream artifacts are missing.
- Any machine field cannot be resolved to a canonical name.

### Step 2 — Validate Config And Dependencies

Inputs:
- `numerics/scan-configs/{analysis_id}.json`
- `model/`
- `constraints/`
- `calculations/`
Do:
- Run schema and semantic validation.
- Recompute the model checksum and independently verify exact-byte calculation
  dependency graphs in addition to checking versions and task ids.
- Check scan/fixed parameter conflicts.
- Check observable and constraint implementation readiness.
- For custom derived observables, require exact `source.task_ids` parity and
  smoke-test the real immutable task-output wrappers.
- Validate interpolation headers, finite ordered nodes, units, support, and
  explicit extrapolation policy.
- Reject ambient RNG/entropy; stochastic callables must accept the local `rng`.
- Reject formula fallback task backends unless the scan-config explicitly sets
  `allow_formula_fallback: true`.
Script:
- `scripts/validate_scan_config.py`
- `references/scan-config-json-contract.md`
Outputs:
- Validation pass/fail and warnings.
Hard fail:
- Validation reports errors.
- Dependency, schema, canonical-name, or implementation checks fail.

### Step 3 — Run Scan

Inputs:
- Validated scan-config
- Calculation backends
- Custom observables if declared
Do:
- Execute the parameter grid.
- Derive independent local PCG64 smoke/scan, point, and consumer substreams
  from the required seed; never seed global state.
- Build the complete exact-byte scan dependency graph before execution and
  reverify the same expected coverage immediately before publication.
- Compute observable columns.
- Evaluate each configured constraint.
- Retain diagnostic skip/failure reasons in memory while evaluating all points.
- Before any output or manifest mutation, require every attempted point to have
  finite observable evidence and a complete `allowed` or `excluded` status.
Script:
- `scripts/run_scan.py`
- `references/constraint-evaluation.md`
- `references/custom-observables-guide.md`
Outputs:
- `numerics/scan-results/{analysis_id}/scan.csv`
- `numerics/scan-results/{analysis_id}/scan.meta.json`
Hard fail:
- Branch III selected.
- Config was not validated first.
- Required observable code raises a hard blocker.
- Any attempted point or configured constraint is skipped, unavailable,
  malformed, boolean-valued, or non-finite. The failed run publishes no scan
  pair, summary, or manifest history.
- Any input graph drift between preflight and publication.

### Step 4 — Make Figures

Inputs:
- `scan.csv`
- `scan.meta.json`
- scan-config figure specs
- `model/model-spec.json`
Do:
- Render every supported figure spec.
- Verify the strict scan pair, its CSV checksum, exact source/snapshot, frozen
  execution projection, and scan graph before loading rows or writing a figure.
- Build `figures.meta.json` over the live rendering request, immutable scan
  hashes, exact outputs, renderer/helpers, schemas, model, and constraints.
- Use canonical names for file names and data lookup.
- Use labels and units for human-facing axes.
- For every figure, select exactly one slice by requiring `figures[].fixed` to
  name every hidden scan axis and no other axis. Match fixed values by exact
  numeric equality; do not use nearest/isclose matching.
- Reject duplicate coordinates on the selected slice. Do not aggregate,
  median-reduce, choose a first row, or drop duplicates.
- Continue past per-figure skips only when the reason is recorded.
Script:
- `scripts/make_figures.py`
- `references/figure-styles.md`
Outputs:
- `numerics/figures/{analysis_id}/*.pdf`
- `numerics/figures/{analysis_id}/*.png`
- `numerics/figures/{analysis_id}/figures.meta.json`
Hard fail:
- Required scan result files are missing.
- A figure requests unknown axes, observables, or constraint columns.

### Step 5 — Write Analysis Summary

Inputs:
- scan-config
- `scan.csv`
- `scan.meta.json`
- figure listing
Do:
- Summarize grid size, columns, constraints, warnings, and skips.
- List any explicitly allowed formula fallback task backends.
- List generated figures and any skipped figures.
- Keep physics interpretation separate from mechanical scan status.
Script:
- Prefer existing summary behavior in `scripts/run_scan.py` and `scripts/make_figures.py`.
- Use `references/scan-results-contract.md`.
Outputs:
- `numerics/analysis-summary-{analysis_id}.md`
Hard fail:
- Summary would describe files that do not exist.
- Summary hides failed or skipped required outputs.

### Step 6 — Update Manifest

Inputs:
- New or refreshed numerics outputs
- Existing `manifest.json`
- Selected branch
Do:
- Upsert only this analysis's ownership object; preserve every unrelated
  analysis object and register this scan-config, scan-results, figures, and
  summary paths.
- Use the branch's allowed history action.
- Keep manifest paths relative to the project root.
Script:
- `scripts/_manifest.py`
- Manifest writes inside `scripts/run_scan.py` and `scripts/make_figures.py`
Outputs:
- Updated `manifest.json`
Hard fail:
- Any manifest path points to a missing file.
- Any history action is outside the allowed list.
- The existing manifest is version 1 or contains string analysis IDs; diagnose
  with `scripts/migrate_manifest_v2.py` and never migrate implicitly.
- A retained analysis entry, dependency snapshot, or owned path would be lost.

### Step 7 — Self-Check And Deliver

Inputs:
- Validation status
- Generated files
- `manifest.json`
- Warnings and skip reasons
Do:
- Run the checklist in Section 11.
- Verify branch-specific hard gates.
- Report produced paths, warnings, skips, and blockers.
Script:
- Use shell checks plus the scripts already run.
- Read references only for failed contract questions.
Outputs:
- Final concise status.
Hard fail:
- Any checklist item required for the selected branch fails.
- `.agents` and `.claude` skill copies diverge after edits to this skill.

## 10. Reference Reading Index

| Open this reference | When to open it |
| --- | --- |
| `references/scan-config-json-contract.md` | Creating, editing, validating, or reviewing `numerics/scan-configs/{analysis_id}.json` |
| `references/scan-results-contract.md` | Inspecting `scan.csv`, `scan.meta.json`, row counts, column order, metadata, warnings, or rerun reproducibility |
| `references/constraint-evaluation.md` | Debugging verdicts, margins, chi2, skip reasons, direct limits, interpolated limits, or manual-only constraints |
| `references/figure-styles.md` | Making or reviewing `exclusion_2d` or `scan_1d` figures, labels, filenames, color policy, or replot behavior |
| `references/custom-observables-guide.md` | Wiring custom observables, parameter-combination fallbacks, custom signatures, smoke tests, or `NotImplementedError` blockers |

## 11. Final Delivery Self-Check Checklist

- [ ] Mode is classified as `batch`, `interactive`, or `interactive-standalone`.
- [ ] Branch is classified as Branch I, Branch II, or Branch III.
- [ ] Branch III did not call `run_scan.py`.
- [ ] `analysis_id` is known and matches output paths.
- [ ] `scan-config.json` exists for the selected analysis.
- [ ] All machine-readable parameter names are canonical ASCII names.
- [ ] Canonical identifiers start with a letter/underscore and are not Python
      hard keywords.
- [ ] No upstream `model/`, `constraints/`, or `calculations/` file was modified.
- [ ] `validate_scan_config.py` passed before any scan run.
- [ ] Hard preflight failures stopped before scan-results were written.
- [ ] Any point-level failure, skipped verdict, unavailable value, boolean, or
      non-finite value caused a nonzero run with no refreshed scan outputs,
      summary, or manifest history.
- [ ] `scan.csv` exists when the selected branch requires scan results.
- [ ] `scan.csv` row and column contracts match the config.
- [ ] `scan.csv` has one unique complete Cartesian grid, exact finite cells,
      and a matching `scan_csv_sha256`.
- [ ] Every configured observable has a complete finite scalar column; a
      recorded skip/failure reason was not treated as successful scan output.
- [ ] Every configured constraint has `verdict`, `margin`, `chi2`, and `skip_reason` columns.
- [ ] Every persisted constraint verdict is `allowed` or `excluded`; completed
      scan output contains no skipped verdict.
- [ ] `scan.meta.json` contains an exact scan-config snapshot, reproducibility
      source/hash, explicit RNG metadata, and a verified graph whose
      independently derived coverage and hashes pass.
- [ ] Figures exist for each required figure spec or a skip reason is recorded.
- [ ] `figures.meta.json` owns exactly the current figure generation and all
      scan/renderer/output hashes verify.
- [ ] Each figure explicitly fixes exactly every hidden scan axis, uses exact
      slice equality, and contains no duplicate selected coordinates.
- [ ] `analysis-summary-{analysis_id}.md` exists and is nonempty when summary generation is in scope.
- [ ] `manifest.json` paths match files on disk.
- [ ] Manifest history action is one of the three allowed numerics actions.
- [ ] Custom observable functions used by the config are implemented and smoke-tested.
- [ ] `.claude/skills/hep-numerics/` and `.agents/skills/hep-numerics/` remain byte-identical after skill edits.
