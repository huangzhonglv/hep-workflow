# Contributing to hep-workflow

Thank you for considering a contribution. This project is research-grade
software for HEP phenomenology workflow automation; the code, schemas, and
contracts evolve as the workflow design matures.

The repository is built around a small set of machine-readable artifacts and
strict contributor invariants. Please treat schemas, skill references, example
projects, and tests as one contract surface.

## Quick links

- Project overview: [README.md](./README.md)
- Project change discipline: [AGENTS.md](./AGENTS.md)
- License: [LICENSE](./LICENSE) (MIT)
- Mirror invariants: [docs/contracts/mirror-invariants.md](./docs/contracts/mirror-invariants.md)
- Canonical name convention: [docs/contracts/canonical-name-convention.md](./docs/contracts/canonical-name-convention.md)
- Content-addressed dependencies: [docs/contracts/content-addressed-dependencies.md](./docs/contracts/content-addressed-dependencies.md)
- Manifest history ownership: [docs/contracts/manifest-history-actions.md](./docs/contracts/manifest-history-actions.md)
- Numerics manifest ownership: [docs/contracts/numerics-manifest-ownership.md](./docs/contracts/numerics-manifest-ownership.md)
- Skill / agent / script ownership: [docs/contracts/skill-agent-division.md](./docs/contracts/skill-agent-division.md)
- Transactional publication and recovery: [docs/contracts/transactional-state-publication.md](./docs/contracts/transactional-state-publication.md)
- Reproduction readiness: [docs/contracts/reproduction-readiness.md](./docs/contracts/reproduction-readiness.md)
- Strict JSON trust boundaries: [docs/contracts/strict-json.md](./docs/contracts/strict-json.md)
- Honest reproduction principle: [docs/contracts/honest-reproduction-principle.md](./docs/contracts/honest-reproduction-principle.md)
- Schema examples: [schemas/examples/](./schemas/examples/)

## Development environment

### Prerequisites

- Python 3.11+ (older versions have not been tested)
- For end-to-end smoke tests: `wolframscript` on PATH (Mathematica's
  command-line entry point). Without it, the enabled e2e suite hard-fails by
  design rather than silently skipping.
- Everything in `requirements-dev.txt` installed in your active environment.

### Setup

Use a local virtual environment for development tools:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements-dev.txt
```

If you already have an environment, make sure it contains the dev
requirements before running validators or tests.

### Running validators and tests

The project has three validation layers; all must be green before any commit
lands.

```bash
# Read-only mirror precondition for skill/shared-helper changes
python3 scripts/sync_skill_mirrors.py --check

# 1. Schema and example validation
python3 scripts/validate_examples.py

# 2. Workspace project structural validation
python3 scripts/validate_workspace_projects.py

# 3. Unit + contract + integration tests (e2e gated)
python3 -m pytest -q
```

With the development environment active, `make validate` runs the three
semantic validators in order. The mirror check is a separate precondition and
must be run when a skill or vendored shared helper changes. The `make test`,
`make contract`, and `make e2e` targets provide shortcuts for the corresponding
focused flows; the raw three-validator commands remain the normative semantic
validation definition.

`scripts/validate_examples.py` validates every schema/example pair registered in
its `SCHEMA_TO_EXAMPLE` map. Bidirectional completeness is enforced against
`schemas/*.schema.json` and `schemas/examples/*.example.json` by
`tests/contract/test_validate_examples_includes_scan_config.py`.

`scripts/validate_workspace_projects.py` checks projects under
`workspace/projects/`. The public repository commits only the minimal
`smoke-e2e` workspace fixture there; other workspace projects are user-local
generated state. You can validate selected local projects by passing project
names:

```bash
python3 scripts/validate_workspace_projects.py smoke-e2e
```

The workspace validator checks `manifest.json`, `model/model-spec.json`,
`model/calc-tasks.json`, `model/benchmarks.json`,
`constraints/constraints-data.json`, `literature/paper-meta.json`,
`literature/repro-targets.json`, `literature/paper-extract.json`,
`calculations/task-*/result-meta.json`, and `numerics/scan-configs/*.json`
when present. It also validates immutable
`reproduction/runs/*/reproduction-result.json` outputs when present. For each
calculation task, it checks required batch output files, unresolved template
placeholders, and cross-file consistency between result metadata, model spec,
and manifest, including the exact expected calculation dependency graph.
Literature targets additionally bind structured formula evidence or distinct
raw/canonical tables and normalization records; reproduction results are
checked for cross-field semantics, current exact-byte dependencies, and
declared PDF/PNG signatures and checksums.

It runs `hep-numerics` semantic validation for scan configs before a scan is
run. Additional static checks cover analysis summaries, custom observables via
Python AST, `result-python.py` signatures, completed and pending task lists,
strict scan table/metadata pairs, content-addressed scan provenance, manifest
`done` evidence, global identifier uniqueness, and stale calculation metadata
whose model version differs from `manifest.active_model_version`.

An optional artifact absent from a workflow stage that has not declared it is
reported as `SKIP`. Evidence required by a declared target, completed artifact,
or immutable run is an `ERROR`; required scientific evidence is never converted
to a skip merely to keep a partial project green. JSON trust boundaries also
reject duplicate keys, non-standard/non-finite values, numeric overflow, and
invalid UTF-8 before state changes.

### Identity and reproducibility boundaries

New canonical parameter, field, observable, function, and quantitative-column
identifiers must match `^[A-Za-z_][A-Za-z0-9_]*$` and must not be Python hard
keywords. The hard-keyword set is explicit and shared across supported Python
3.11-3.13 runtimes; do not sanitize an invalid input into a valid name. IDs
such as `task-001`, `analysis-001`, `run-001`, and `c-001` follow their separate
ASCII patterns. Persisted paths must be relative, contained, symlink-safe, and
bound to the payload ID/filename where the artifact contract requires it.

New calculation, scan, and reproduction outputs must carry a mechanically
built `input_provenance` graph. Producers and consumers use
`scripts/_workflow_dependencies.py` to derive the complete workflow-specific
role/path set independently, then hash exact file bytes through
`scripts/_dependency_graph.py`. Never construct a reduced graph from whichever
files happen to exist, accept `legacy-unverified` for a new execution, or edit a
checksum to make stale evidence current.

The graph does not yet bind external runtimes or prove runtime dataflow. Scan
metadata records Python, NumPy, SciPy, and Matplotlib version strings for
diagnosis, but these are not environment attestations; Wolfram / Package-X,
wheel/native-library bytes, OS, and CPU remain outside the graph. The required
scan `seed` drives the versioned `pcg64-v1` local-RNG/SeedSequence substream
contract. Stochastic custom observables must accept the injected `rng` and the
metadata records the consumers; ambient or partially seeded global Python /
NumPy randomness is not an acceptable compatibility shortcut.

### Manifest ownership and version 2

Every layer may read `manifest.json`, but write authority is narrow. A
foundation skill may author only its documented artifact fields in an
owner/mode-bound private candidate; its mechanical finalizer publishes that
candidate. Other scripts may publish only through contract-bound helpers. An
orchestrator dispatches the owner and validates the result. Do not perform a
second manifest merge after a successful owner publication, copy a candidate
into live paths, repair another owner's fields, or append a duplicate history
event.

New workspace state uses `manifest_version = 2`. Numerics evidence lives in
`artifacts.numerics.analyses[]`, where each analysis owns its files and exact
dependency snapshot. Aggregate files and status are deterministic projections,
not an independent source of truth. Updating one analysis must preserve every
unrelated analysis.

Version 1 migration is explicit and fail-closed. Diagnose first, review the
reported ownership reconstruction, and only then request the transactional
write:

```bash
python3 scripts/migrate_manifest_v2.py \
  --project-dir workspace/projects/{project-name}
python3 scripts/migrate_manifest_v2.py \
  --project-dir workspace/projects/{project-name} --write
```

Never hand-convert an ambiguous v1 manifest or drop legacy files/statuses to
make migration pass.

### Transactional publication and recovery

Any command that publishes multiple authoritative paths must use
`scripts/_publication_transaction.py`: stage and validate a complete candidate,
publish with lock/journal/compare-and-swap, and write `manifest.json` last when
it indexes the other paths. A retry must be idempotent and must not create a
second history event.

For `hep-idea` and `hep-paper-formalize`, allocate a private attempt before
generation and finalize the exact returned tuple afterward:

```bash
python3 scripts/init_foundation_attempt.py \
  --project-dir workspace/projects/{project-name} \
  --owner hep-idea --mode revise --format json
python3 scripts/finalize_foundation_attempt.py \
  --project-dir workspace/projects/{project-name} \
  --attempt-dir {returned-attempt-dir} --attempt-id {returned-attempt-id} \
  --owner hep-idea --mode revise --format json
```

The corresponding paper modes are `setup` and `formalize`. Skills write only
below the returned `candidate_dir`. The finalizer enforces owner scope,
preserves unowned paths, marks existing calculations stale for changed
model/task/benchmark inputs, derives numerics staleness from staged inputs, and
publishes `manifest.json` last. A derived stale transition preserves historical
evidence and dependency metadata; it never edits or blesses old result files.

For a legacy/external upstream transition, diagnose the derived stale
projection first and write it only through the narrow repair command:

```bash
python3 scripts/refresh_numerics_staleness.py \
  --project-dir workspace/projects/{project-name}
python3 scripts/refresh_numerics_staleness.py \
  --project-dir workspace/projects/{project-name} --write
```

If a journal remains after interruption, inspect it without mutation:

```bash
python3 scripts/recover_publication_transactions.py \
  --project-dir workspace/projects/{project-name} --format json
```

After reviewing the authenticated journal, request conservative recovery
explicitly:

```bash
python3 scripts/recover_publication_transactions.py \
  --project-dir workspace/projects/{project-name} --recover --format json
```

Do not delete `.hep-workflow-transactions` manually and do not rerun a
scientific command as a substitute for recovery. A `blocked` recovery result
means ownership is ambiguous and must remain untouched. The durable protocol is
currently qualified only for POSIX hosts, regular local filesystem objects,
same-filesystem atomic rename, and working file/directory `fsync`; Windows,
NFS, SMB, object-backed mounts, and cross-device publication are unsupported.

### End-to-end tests

End-to-end tests live under `tests/e2e/` and are gated behind `--run-e2e`, so
a default `pytest -q` run never requires `wolframscript`:

```bash
python3 -m pytest -q tests/e2e --run-e2e
# or set HEP_E2E=1
HEP_E2E=1 python3 -m pytest -q tests/e2e
```

The e2e suite runs the full `hep-numerics` workflow against the minimal
`workspace/projects/smoke-e2e/` fixture, including Branch I, Branch II, Branch
III, a `wolframscript`-backed benchmark verification, and an immutable
reproduction comparison smoke path.

For convenience, `scripts/smoke_hep_numerics.sh` delegates to `make validate`,
while `scripts/smoke_hep_numerics_e2e.sh` delegates to
`make validate e2e`. Both wrappers use the active environment without creating
or reinstalling `.venv`; use the e2e wrapper only on hosts with
`wolframscript` installed.

## Project structure

- `.claude/skills/<skill-name>/` and `.agents/skills/<skill-name>/`:
  parallel skill installation trees. Matching files must remain strictly
  byte-identical between the two trees. Shared text that mentions installed
  paths names both supported layouts. This invariant is enforced by contract
  tests.
- `.claude/agents/<name>.md` and `.codex/agents/<name>.toml`: matching
  orchestrator definitions. Every pair's prompt bodies must remain
  content-equivalent.
- `schemas/`: JSON Schemas for machine-readable artifacts.
- `schemas/examples/`: canonical examples exercised by validators.
- `scripts/`: repository-level scripts, validators, and smoke runners.
- `workspace/projects/`: user-local generated workspace projects. The public
  repo commits only `smoke-e2e/` as a minimal e2e fixture.
- `tests/fixtures/workspace-projects/`: synthetic workspace fixtures used by
  contract and integration tests when a richer project shape is required.
- `tests/{unit,contract,integration,e2e,smoke}/`: test layers from pure
  functions to gated full-workflow checks.

The source-of-truth hierarchy is:

1. JSON schemas in `schemas/`
2. Project-level contracts in `docs/contracts/`
3. Skill reference contracts in `.claude/skills/*/references/` and
   `.agents/skills/*/references/`
4. Operational skill guides in `.claude/skills/*/SKILL.md` and
   `.agents/skills/*/SKILL.md`
5. Agent definitions in `.claude/agents/` and `.codex/agents/`
6. Documentation such as `README.md` and this contributor guide

When documentation and contracts disagree, update them together rather than
letting one drift.

Current development context: schemas, strict JSON readers, exact-byte
dependency graphs, manifest-v2 ownership, crash-transactional publication, and
the explicit `pcg64-v1` stochastic interface connect `hep-idea`, batch
`package-scribe`, `hep-numerics`, and reproduction comparison. The workspace
validator independently checks their current calculation/scan/reproduction
evidence. External runtime attestation remains outside that completed layer.

### Canonical output paths

Batch `package-scribe` calculation results belong under:

```text
workspace/projects/{project-name}/calculations/{task_id}/
```

Expected batch artifacts are:

- `request.md`
- `result.wl`
- `result-summary.md`
- `result-python.py`
- `result-meta.json`
- `run-instructions.md`

Standalone `package-scribe` results belong under:

```text
workspace/package-scribe/package-resultNNN/
```

`hep-numerics` outputs belong under:

```text
workspace/projects/{project-name}/numerics/
|-- scan-configs/
|   `-- {analysis_id}.json
|-- scan-results/
|   `-- {analysis_id}/
|       |-- scan.csv
|       `-- scan.meta.json
|-- figures/
|   `-- {analysis_id}/
|       |-- exclusion-{x}-{y}.pdf
|       |-- exclusion-{x}-{y}.png
|       |-- scan1d-{x}-{observable}.pdf
|       |-- scan1d-{x}-{observable}.png
|       `-- figures.meta.json
|-- custom_observables.py
`-- analysis-summary-{analysis_id}.md
```

This layout is specified by the `hep-numerics` operational guide and
`references/scan-results-contract.md`.
The only canonical one-dimensional figure prefix is `scan1d-`; writers and
manifest consumers must not emit or accept a second `scan-` alias.

## Skill writing discipline

This project deliberately keeps skill documentation lean (the "slim" pattern).
Three contracts are enforced automatically:

1. No dependence on retired design documents: skill files (`SKILL.md` and
   `references/*.md`) under `.claude/skills/` and `.agents/skills/` must not
   rely on historical design documents that are no longer part of the contract
   surface.
2. Byte-identical mirroring: matching files between
   `.claude/skills/<name>/` and `.agents/skills/<name>/` must be
   byte-identical. Contract tests enforce this for existing mirrored skills;
   follow the same convention for any new skill.
3. Explicit canonical name rule: parameter and observable identifiers used in
   skill paths and configurations must match `^[A-Za-z_][A-Za-z0-9_]*$`, must
   not be Python hard keywords, and must contain no LaTeX or Unicode. See
   `docs/contracts/canonical-name-convention.md`.

When you edit a skill, the rule of thumb is:

- Keep `SKILL.md` short and execution-routing focused: mode detection, branch
  dispatch, hard rules, self-check checklist, and references index.
- Put field-level and contract-level detail in `references/*.md`.
- Update both `.claude/skills/<name>/` and `.agents/skills/<name>/` in the
  same change. The recommended flow is to edit `.claude/skills/`, then run
  `python3 scripts/sync_skill_mirrors.py --from-claude`; use `--from-agents`
  only when the `.agents` copy is intentionally the source.
- Run `python3 scripts/sync_skill_mirrors.py --check` before committing. Check
  mode is the default and never writes files.
- Run focused contract tests before the general test suite.
- When in doubt, look at how `.claude/skills/hep-numerics/` is organized; that
  skill is the reference shape for new slim skills.

### `package-scribe` mode discipline

`package-scribe` supports two execution modes:

- Standalone mode: triggered by a direct natural-language or LaTeX calculation
  request. It preserves the interactive questioning flow and writes to
  `workspace/package-scribe/package-resultNNN/`.
- Batch mode: triggered when workspace artifacts provide
  `model/model-spec.json`, `model/calc-tasks.json`, and a specific `task_id`.
  It recovers task context from artifacts, writes to
  `calculations/{task_id}/`, and emits batch-only outputs such as
  `result-python.py`, `result-meta.json`, and benchmark verification status.

If batch calculations are explicitly `stale`, the first successfully finalized
task starts a new current generation. Only that task is completed, all other
currently declared tasks are pending, and untouched task directories remain
historical until individually regenerated.

Keep mode behavior synchronized with
`.agents/skills/package-scribe/SKILL.md` and the mirrored Claude skill file.

### `hep-numerics` mode discipline

`hep-numerics` covers scan-config validation, parameter scans, constraint
evaluation, figure generation, and analysis summaries.

It supports two execution modes:

- Batch mode: run an existing `numerics/scan-configs/{analysis_id}.json`
  inside a workspace project.
- Interactive mode: infer or draft a scan config from workspace artifacts,
  then run it as a named analysis.

It also exposes three high-level branches:

- Branch I, full analysis: generate or refine a config, run the scan, make
  figures, and write the summary.
- Branch II, re-run existing analysis: rerun an existing `analysis_id` against
  the current workspace state.
- Branch III, only replot: reuse existing `scan.csv` and regenerate figures
  without rerunning the scan.

Changes to `hep-numerics` scripts, manifest behavior, or the `smoke-e2e`
fixture require the gated e2e suite before the change is declared complete.

### Extending reproduction comparisons

Supported quantitative kinds are `benchmark_point`, `keyed_benchmark_set`,
`scan_table`, `figure_curve`, `parametric_curve`, and `exclusion_region`.
Do not overload one kind to approximate another: a benchmark point has exactly
one row, multi-point benchmarks use unique keyed rows, figure curves are
single-valued, and parametric curves carry an ordered path parameter.

Before comparison, derive typed readiness without mutating the project:

```bash
python3 scripts/check_reproduction_readiness.py \
  --project-dir workspace/projects/{project-name} \
  --analysis-id analysis-001
```

Use `--target-id <target-id>` to restrict the report. Exit code 0 means the
tool produced a valid routing report; it does **not** mean every target is
ready. Inspect `workflow_state`, every target `disposition`, and the typed stage
statuses. Preconditions that prevent a valid report still return nonzero.

When adding or changing comparison behavior, update the complete contract
surface in one change:

1. `schemas/repro-targets.schema.json`,
   `schemas/reproduction-result.schema.json`, readiness/result schemas when
   affected, and their registered examples.
2. The hep-paper-formalize reproduction-target reference and the honest
   reproduction contract.
3. `scripts/_compare_metrics.py`, `scripts/compare_to_reference.py`, and
   `scripts/_reproduction_result_validation.py` as applicable.
4. Positive, blocked, false-pass, false-fail, duplicate-key, non-finite,
   incomplete-coverage, and immutable-publication tests.

Tolerance and coverage policy must be fixed before results are inspected.
Keyed/table comparisons require full declared row/value coverage. A
higher-dimensional scan must declare one exact slice by fixing every hidden
scan parameter; aggregation or implicit projection is not a compatibility
fallback. Exclusion targets must declare an authoritative boundary source;
disconnected or holed regions require complete `reference_faces` and probes.

Unit conversion belongs to import/digitization. Preserve an immutable raw
table, generate a canonical-unit table, and bind both with a strict
normalization record containing exact factors/offsets and hashes. The comparator
verifies this record and accepts canonical-unit inputs; it must not choose units
or convert scan output.

## Adding tests

- Unit tests: pure-function checks with small inputs and no workspace fixture
  mutation.
- Contract tests: schema and structural invariants, preferably one assertion
  per invariant with a descriptive failure message.
- Integration tests: exercise scripts against `workspace/projects/` fixtures.
  Use the existing `project_copy_factory` fixture in `tests/conftest.py` to
  avoid mutating source fixtures.
- E2E tests: full workflow checks with `wolframscript`; mark them with
  `@pytest.mark.e2e`.

A new fixture-level numerical regression test should follow
`tests/contract/test_fixture_benchmarks_self_consistent.py`: parametrize over
each project's `benchmarks.json` numerical test points and assert that
`result-python.py` evaluates to the expected value within tolerance. This
catches schema-valid but mathematically wrong fixtures before they reach the
e2e layer.

When adding Python behavior:

- Prefer focused tests under `tests/unit/` or `tests/integration/` for the
  changed script.
- Add contract coverage when the change creates or preserves a repository
  invariant.
- Keep `python3 -m pytest -q` at zero failures.
- If the behavior affects e2e workflow routing, also run
  `python3 -m pytest -q tests/e2e --run-e2e` on a host with `wolframscript`.

## Adding a new skill

Follow the repository's source-of-truth hierarchy when introducing a skill.
The labels below distinguish surfaces enforced by current tests from surfaces
that still require deliberate review.

1. **Define artifact contracts first `[automated where applicable]`.** Add or
   extend `schemas/*.schema.json` and `schemas/examples/*.example.json`; update
   the manifest artifact shape and history-action enum when the skill owns new
   state. The example validator and its schema/example completeness guard cover
   registered pairs.
2. **Create the mirrored skill `[automated]`.** Add the canonical
   `.claude/skills/<name>/` tree, including only the required `SKILL.md`,
   references, scripts, and templates. Run
   `python3 scripts/sync_skill_mirrors.py --from-claude` to create the
   `.agents/skills/<name>/` copy. The generic mirror contract checks every
   file byte-for-byte.
3. **Register coordination and routing `[mixed]`.** Update each relevant
   `.claude/agents/<name>.md` and `.codex/agents/<name>.toml` pair: skill
   inventory, trigger precedence, prerequisite table, pre-dispatch checks,
   output validation, and recognized manifest actions. Pair equivalence is
   automated; routing completeness and prose semantics require review plus
   explicit cases in `tests/contract/test_agent_trigger_precedence.py`.
4. **Update ownership boundaries `[mixed]`.** Adjust sibling `SKILL.md`
   boundary sections and `docs/contracts/skill-agent-division.md` when
   responsibilities move. If the skill emits a new history action, follow the
   checklist below and extend the history-action contract tests.
5. **Wire workspace state `[mixed]`.** If the skill introduces directories,
   update `PROJECT_SUBDIRECTORIES` in
   `.claude/skills/hep-idea/scripts/init_project_skeleton.py` and
   `.claude/skills/hep-paper-formalize/scripts/init_paper_project_skeleton.py`,
   then sync their mirrors. If it owns new artifacts, extend
   `scripts/validate_workspace_projects.py` and representative fixture state.
   Skeleton alignment and validator behavior are test-covered once the new
   case is registered.
6. **Refresh public discovery surfaces `[mixed]`.** Update `CLAUDE.md` and
   `AGENTS.md` byte-identically, then update the skill/component tables in
   `README.md` and this guide. Top-level byte identity is automated; descriptive
   completeness still needs manual review.
7. **Add behavior contracts `[automated once added]`.** Cover the new trigger,
   schema/template behavior, validator behavior, and any script logic in the
   narrowest appropriate test layer. Add a per-skill contract test only for an
   invariant not already covered by the generic mirror walker.
8. **Run the complete gate.** Run
   `python3 scripts/sync_skill_mirrors.py --check`, then `make validate`. Run
   `make e2e` as well when the new skill changes routed e2e behavior or shared
   workspace artifacts used by the e2e fixture.

## Adding a new history action

Manifest history actions are referenced in multiple places. When adding a new
action, update all relevant surfaces together:

1. `schemas/manifest.schema.json`: extend it if the action set is constrained
   there.
2. The owning skill's `SKILL.md` and `references/manifest-json-contract.md`
   when applicable: list the new action and its meaning.
3. Every orchestrator pair that reads the shared manifest:
   `.claude/agents/<name>.md` and `.codex/agents/<name>.toml`. Update both sides
   content-equivalently; recognizing an action does not grant permission to
   emit it.
4. The contract-bound skill or script writer that owns the event and its
   idempotency/publication tests.
5. Workspace fixtures that demonstrate or rely on the new action.

Then add a contract test that asserts the action name matches in all expected
places.

Do not add an action name in only one layer. A schema-valid manifest that the
skills or orchestrator do not understand is still a workflow bug.

## Pull request checklist

Before opening a PR:

- [ ] All three validators pass on your machine:
      `python3 scripts/validate_examples.py`,
      `python3 scripts/validate_workspace_projects.py`, and
      `python3 -m pytest -q`.
- [ ] If you touched `.claude/skills/<name>/`, the matching
      `.agents/skills/<name>/` files were updated to remain byte-identical.
- [ ] If you touched `.agents/skills/<name>/`, the matching
      `.claude/skills/<name>/` files were updated to remain byte-identical.
- [ ] If you touched a root vendored shared helper, both standalone
      `hep-numerics` copies were refreshed from root and
      `scripts/sync_skill_mirrors.py --check` passes.
- [ ] If you touched an orchestrator, its matching
      `.claude/agents/<name>.md` and `.codex/agents/<name>.toml` definitions
      were updated to remain content-equivalent.
- [ ] If you touched schemas or skill reference contracts, examples and
      workspace projects still validate.
- [ ] If you added or changed Python behavior, focused tests were added under
      the appropriate test layer.
- [ ] If you added a JSON trust-boundary reader, it uses the shared strict JSON
      behavior and has duplicate-key, non-finite/overflow, and invalid-UTF-8
      negative tests before any side effect.
- [ ] If you added a multi-path writer, it uses the shared transactional
      protocol, has interruption/concurrency/idempotency/recovery coverage, and
      publishes the manifest last when applicable.
- [ ] If you changed reproduction routing, tests distinguish a valid
      `workflow_state = not_ready` report from actual target readiness; a zero
      helper exit code is not treated as scientific readiness.
- [ ] If you added a new file or invariant, you ran the relevant contract
      tests with `python3 -m pytest -q tests/contract/`.
- [ ] If your change touches the e2e workflow, the `smoke-e2e` fixture,
      `tests/e2e/`, hep-numerics scripts, or manifest schema behavior used by
      the e2e workflow, you ran
      `python3 -m pytest -q tests/e2e --run-e2e` on a host with
      `wolframscript`.
- [ ] New artifact paths, schema names, script names, and skill-owned machine
      identifiers follow the Python-compatible ASCII canonical name rule and
      are not Python hard keywords.
- [ ] Your commit messages explain why the change is needed, not just what
      files changed.

## Code of Conduct

This project follows the [Contributor Covenant Code of Conduct](https://www.contributor-covenant.org/version/2/1/code_of_conduct/).
By participating, you agree to abide by its terms.

## License

By contributing, you agree that your contributions will be licensed under the
project's [MIT License](./LICENSE).
