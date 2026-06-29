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
- Canonical name convention: [docs/contracts/canonical-name-convention.md](./docs/contracts/canonical-name-convention.md)
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
# 1. Schema and example validation
python3 scripts/validate_examples.py

# 2. Workspace project structural validation
python3 scripts/validate_workspace_projects.py

# 3. Unit + contract + integration tests (e2e gated)
python3 -m pytest -q
```

`scripts/validate_examples.py` checks canonical files in `schemas/examples/`
against `manifest.schema.json`, `model-spec.schema.json`,
`calc-tasks.schema.json`, `benchmarks.schema.json`,
`result-meta.schema.json`, `constraints-data.schema.json`, and
`scan-config.schema.json`.

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
and manifest.

It runs `hep-numerics` semantic validation for scan configs before a scan is
run. Additional static checks cover analysis summaries, custom observables via
Python AST, `result-python.py` signatures, completed and pending task lists,
and stale calculation metadata whose model version differs from
`manifest.active_model_version`.

Missing files are reported as `SKIP`, which keeps the validator useful for
partially completed projects as well as fully populated fixtures.

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
III, and a `wolframscript`-backed benchmark verification.

For convenience, `scripts/smoke_hep_numerics_e2e.sh` runs the
wolframscript-free baseline first and then the gated e2e suite. Use it only on
hosts with `wolframscript` installed.

## Project structure

- `.claude/skills/<skill-name>/` and `.agents/skills/<skill-name>/`:
  parallel skill installation trees. Apart from a small set of hardcoded
  installation-path strings, matching skill files must remain byte-identical
  between the two trees. This invariant is enforced by contract tests.
- `.claude/agents/hep-orchestrator.md` and
  `.codex/agents/hep-orchestrator.toml`: orchestrator definitions. Their
  prompt bodies must remain content-equivalent.
- `schemas/`: JSON Schemas for machine-readable artifacts.
- `schemas/examples/`: canonical examples exercised by validators.
- `scripts/`: repository-level scripts, validators, and smoke runners.
- `workspace/projects/`: user-local generated workspace projects. The public
  repo commits only `smoke-e2e/` as a minimal e2e fixture.
- `tests/fixtures/workspace-projects/`: synthetic workspace fixtures used by
  contract and integration tests when a richer project shape is required.
- `tests/{unit,contract,integration,e2e,smoke}/`: test layers from pure
  functions to gated full-workflow checks.
- Local `codex-prompts-*.md` files: frozen prompt batches from execution
  history. Use them as context only; they are not the current source of truth.

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

Current development context: the repository has primarily established schema
contracts connecting `hep-idea`, `package-scribe`, and `hep-numerics`; the
unified `hep-idea` responsibility for model and constraint generation or
revision; the batch-compatible `package-scribe` workflow; and validation
tooling for examples and workspace projects. Treat local `codex-prompts-*.md`
files as execution history, not active source of truth.

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
|       `-- scan1d-{x}-{observable}.png
|-- custom_observables.py
`-- analysis-summary-{analysis_id}.md
```

This layout is specified by the `hep-numerics` operational guide and
`references/scan-results-contract.md`.

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
   skill paths and configurations must be ASCII canonical names, with no
   LaTeX, Unicode, or punctuation. See
   `docs/contracts/canonical-name-convention.md`.

When you edit a skill, the rule of thumb is:

- Keep `SKILL.md` short and execution-routing focused: mode detection, branch
  dispatch, hard rules, self-check checklist, and references index.
- Put field-level and contract-level detail in `references/*.md`.
- Update both `.claude/skills/<name>/` and `.agents/skills/<name>/` in the
  same change.
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

## Adding a new history action

Manifest history actions are referenced in multiple places. When adding a new
action, update all relevant surfaces together:

1. `schemas/manifest.schema.json`: extend it if the action set is constrained
   there.
2. The owning skill's `SKILL.md` and `references/manifest-json-contract.md`
   when applicable: list the new action and its meaning.
3. `.claude/agents/hep-orchestrator.md` and
   `.codex/agents/hep-orchestrator.toml`: extend the canonical action set both
   files document.
4. Workspace fixtures that demonstrate or rely on the new action.

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
- [ ] If you touched the orchestrator, both
      `.claude/agents/hep-orchestrator.md` and
      `.codex/agents/hep-orchestrator.toml` were updated to remain
      content-equivalent.
- [ ] If you touched schemas or skill reference contracts, examples and
      workspace projects still validate.
- [ ] If you added or changed Python behavior, focused tests were added under
      the appropriate test layer.
- [ ] If you added a new file or invariant, you ran the relevant contract
      tests with `python3 -m pytest -q tests/contract/`.
- [ ] If your change touches the e2e workflow, the `smoke-e2e` fixture,
      `tests/e2e/`, hep-numerics scripts, or manifest schema behavior used by
      the e2e workflow, you ran
      `python3 -m pytest -q tests/e2e --run-e2e` on a host with
      `wolframscript`.
- [ ] New artifact paths, schema names, script names, and skill-owned machine
      identifiers follow the ASCII canonical name rule.
- [ ] Your commit messages explain why the change is needed, not just what
      files changed.

## Code of Conduct

This project follows the [Contributor Covenant Code of Conduct](https://www.contributor-covenant.org/version/2/1/code_of_conduct/).
By participating, you agree to abide by its terms.

## License

By contributing, you agree that your contributions will be licensed under the
project's [MIT License](./LICENSE).
