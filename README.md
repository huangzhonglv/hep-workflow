# hep-workflow

> A skill-based agent workflow for high-energy physics phenomenology:
> from model proposal through symbolic calculation to numerical scans
> and publication-oriented exclusion plots.

**Status**: research preview. Skill contracts and workspace artifact formats
are under active iteration. Use at your own risk; pin to a specific commit if
you build on top.

This repository ships agent and skill definitions, JSON Schemas, validators,
fixtures, and tests. It is not a packaged runtime application.

## What this is for

When you have a beyond-the-Standard-Model Lagrangian and want to:

1. Generate canonical model and constraint artifacts.
2. Compute observables symbolically with reproducible LaTeX and Python output.
3. Run numerical parameter scans with constraint overlays.
4. Produce publication-style 2D exclusion overlays and 1D parameter scans,
   plus a written analysis summary.
5. Optionally formalize and compare against a published paper while keeping
   reproduction targets separate from computational backends.

`hep-workflow` provides two coordinated workflow surfaces:

- A model-first HEP workflow: `hep-idea` -> `package-scribe` -> `hep-numerics`.
- A paper-reproduction workflow: `hep-paper-formalize` plus mechanical
  comparison through `scripts/compare_to_reference.py`.

## Quick start

### 1. Install

Use Python 3.11 or newer. A virtual environment is recommended; mixed system
scientific Python environments can fail for reasons unrelated to this project.

```bash
git clone <repository-url>
cd hep-workflow
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements-dev.txt
```

### 2. Run the validators

```bash
python3 scripts/validate_examples.py
python3 scripts/validate_workspace_projects.py
python3 -m pytest -q
```

If all three return exit code 0, your environment is ready.

### 3. Explore an example project

```bash
ls workspace/projects/smoke-e2e/
ls workspace/projects/smoke-e2e/model/
```

The repository commits only this minimal workspace fixture under
`workspace/projects/`. Other workspace projects are user-local generated state
and are not part of the public release.

## Skills and Agents

| Skill | Role | Key entry point |
| --- | --- | --- |
| [`hep-idea`](./.claude/skills/hep-idea/SKILL.md) | Model and constraint artifact generation, including revisions | Triggered by "define a new model", "add constraint", "update model" |
| [`hep-paper-formalize`](./.claude/skills/hep-paper-formalize/SKILL.md) | Paper metadata, extraction, reproduction targets, and paper-first model formalization | Triggered by "reproduce paper", "replicate Fig.", "import paper" |
| [`package-scribe`](./.claude/skills/package-scribe/SKILL.md) | Symbolic calculation: Mathematica and Python with benchmark verification | Triggered by "compute the analytical expression for ..." |
| [`hep-numerics`](./.claude/skills/hep-numerics/SKILL.md) | Parameter scans, constraint evaluation, figures, analysis summaries | Triggered by "run a scan", "make an exclusion plot", "rerun analysis" |

| Agent | Role |
| --- | --- |
| [`hep-orchestrator`](./.claude/agents/hep-orchestrator.md) | Coordinates model-first projects, project status, skill dispatch, `manifest.json`, and prerequisite checks |
| [`repro-orchestrator`](./.claude/agents/repro-orchestrator.md) | Coordinates paper reproduction requests, `literature/` artifacts, immutable reproduction runs, and `compare_to_reference.py` |

Codex-format agent definitions live under [`.codex/agents/`](./.codex/agents/).
Skill definitions are mirrored under [`.claude/skills/`](./.claude/skills/) and
[`.agents/skills/`](./.agents/skills/); mirror invariants are contract-tested.

## Workspace project layout

```text
workspace/projects/<project-name>/
|-- manifest.json              # project state and history
|-- idea/                      # research proposal artifacts
|-- model/                     # model spec, calc tasks, benchmarks
|-- constraints/               # experimental constraint data
|-- calculations/task-XXX/     # symbolic and Python results per task
|-- literature/                # optional paper reproduction inputs
|-- reproduction/              # optional immutable comparison outputs
`-- numerics/
    |-- scan-configs/<analysis-id>.json
    |-- scan-results/<analysis-id>/
    |-- figures/<analysis-id>/
    `-- analysis-summary-<analysis-id>.md
```

A minimal hand-checkable example lives at `workspace/projects/smoke-e2e/` and
is used by the end-to-end smoke suite. Richer synthetic contract fixtures for
tests live under `tests/fixtures/workspace-projects/`; they are not user
workspace state. Detailed output contracts, e2e gating, and `wolframscript`
requirements live in [CONTRIBUTING.md](./CONTRIBUTING.md).

## Contracts

The load-bearing contracts live under [docs/contracts/](./docs/contracts/).
When documentation disagrees, fix top-down from schemas and contracts rather
than treating README prose as the source of truth.

## Documentation

- **For users**: each skill's `SKILL.md` documents how it is invoked and what
  it produces.
- **For contributors**: see [CONTRIBUTING.md](./CONTRIBUTING.md) for
  development setup, test discipline, and how to add new skills, tests, or
  history actions.
- **For agents reading this repository**: see [AGENTS.md](./AGENTS.md) for
  change discipline.

## License

[MIT](./LICENSE)
