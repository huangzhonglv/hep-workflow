# hep-workflow

> Read by all AI agents working in this repo. Claude Code loads `CLAUDE.md`;
> Codex and other tooling load `AGENTS.md`. The two files are kept
> **byte-identical** by `tests/contract/test_top_level_docs_byte_identical.py`.

## Project

A skill-based agent workflow for high-energy physics phenomenology — model
proposal → symbolic calculation (Package-X / Mathematica) → numerical scans
→ publication-grade exclusion plots.

The repo ships **skill definitions and validation tooling**, not a runtime
application. Most edits change schemas, skill prompts, helper scripts, or
fixtures, and the test/validator layer is the contract surface.

## Common commands

Setup (one-time):

```bash
python3 -m venv .venv && source .venv/bin/activate
python3 -m pip install -r requirements-dev.txt
```

The three validation layers — all must be green before any commit lands:

```bash
python3 scripts/validate_examples.py
python3 scripts/validate_workspace_projects.py
python3 -m pytest -q
```

When a skill tree or vendored shared helper changes, run the read-only mirror
precondition before those three layers:

```bash
python3 scripts/sync_skill_mirrors.py --check
```

Run a single test or one layer:

```bash
python3 -m pytest -q tests/contract
python3 -m pytest -q tests/integration/test_minimal_scan.py
```

End-to-end suite (requires `wolframscript`; gated):

```bash
python3 -m pytest -q tests/e2e --run-e2e
```

## Project-level contracts (load-bearing)

These rules apply project-wide. Full text in `docs/contracts/`. The summaries
here are pointers, not duplicates — never copy contract bodies into this file.

| Contract | One-line rule | Full text |
| --- | --- | --- |
| Mirror invariants | `.claude/skills/` ↔ `.agents/skills/` byte-identical; orchestrator `.md` ↔ `.toml` content-equivalent; CLAUDE.md ↔ AGENTS.md byte-identical | `docs/contracts/mirror-invariants.md` |
| Canonical name rule | New canonical identifiers match `^[A-Za-z_][A-Za-z0-9_]*$` and are not Python hard keywords; workflow IDs follow their own patterns | `docs/contracts/canonical-name-convention.md` |
| Content-addressed dependencies | New calculation, scan, and reproduction outputs bind exact declared input/code bytes; legacy evidence cannot support new scientific claims | `docs/contracts/content-addressed-dependencies.md` |
| Three-validator discipline | `validate_examples` + `validate_workspace_projects` + `pytest -q` must stay green on `main` | `docs/contracts/three-validator-discipline.md` |
| Manifest history actions | New action requires synchronized update across schema + skills + orchestrator + tests | `docs/contracts/manifest-history-actions.md` |
| Transactional publication | Multi-path writers publish one coherent generation through lock + journal + CAS; interrupted state fails closed | `docs/contracts/transactional-state-publication.md` |
| Numerics manifest ownership | Manifest v2 owns files and dependency snapshots per analysis; aggregates are deterministic projections | `docs/contracts/numerics-manifest-ownership.md` |
| Skill / agent / script division | Mechanical → script; generation → skill; coordination → agent | `docs/contracts/skill-agent-division.md` |
| Honest reproduction principle | Calculations independently derived; disagreement reported as-is; never adjust tolerance to mask | `docs/contracts/honest-reproduction-principle.md` |
| Reproduction readiness | Per-target prerequisites are derived read-only from typed current evidence; formula targets do not consume model/calculation/scan state | `docs/contracts/reproduction-readiness.md` |
| Strict JSON trust boundaries | Duplicate keys, non-standard/non-finite numbers, overflow, and invalid UTF-8 fail before side effects | `docs/contracts/strict-json.md` |

## Architecture (quick-glance)

Three responsibility layers:

- **Agents** (`.claude/agents/` / `.codex/agents/`) coordinate project state by
  reading `manifest.json`, dispatching the documented skill/script owner for
  writes, and validating owner-published outputs.
  `hep-orchestrator` owns model-first coordination; `repro-orchestrator` owns
  paper-reproduction coordination.
- **Skills** (`.claude/skills/` ↔ `.agents/skills/`) own disjoint slices of a
  workspace project. The shipped skills are `hep-idea`,
  `hep-paper-formalize`, `package-scribe`, and `hep-numerics`. Foundation
  skills author private candidates rather than live multi-path generations.
- **Scripts** (`scripts/` and `<skill>/scripts/`) do mechanical work.
  `finalize_foundation_attempt.py` publishes foundation candidates and derives
  calculation/numerics stale state; `refresh_numerics_staleness.py` owns the
  standalone legacy numerics repair.

Source-of-truth hierarchy (when docs disagree, fix top-down):
1. `schemas/*.json`
2. `docs/contracts/*.md`
3. `.claude/skills/*/references/`
4. `.claude/skills/*/SKILL.md`
5. `.claude/agents/*.md` + `.codex/agents/*.toml`
6. `README.md`, `CONTRIBUTING.md`, this file

## Workspace project layout (sketch)

```text
workspace/projects/<project-name>/
├── manifest.json
├── idea/                       hep-idea
├── model/                      hep-idea
├── constraints/                hep-idea
├── literature/                 hep-paper-formalize
├── calculations/task-NNN/      package-scribe
├── numerics/                    hep-numerics
│   ├── scan-configs/<analysis-id>.json
│   ├── scan-results/<analysis-id>/
│   └── figures/<analysis-id>/
└── reproduction/                repro-orchestrator + compare_to_reference.py
    ├── runs/<repro-id>/
    ├── figures/<repro-id>/
    └── reports/<repro-id>.md
```

Committed workspace fixture: `workspace/projects/smoke-e2e/` (minimal, used by
e2e). Other `workspace/projects/` entries are user-local generated state and
are not part of the public release. Richer synthetic contract fixtures live
under `tests/fixtures/workspace-projects/`.

## Next-step guidance

When responding to the user during an active agent or skill turn — progress
updates, completion summaries, validation reports, blocker reports, status
answers, or questions back to the user — end with a single concise line
naming the most useful next action:

> Next step: <concrete action> (<state basis>)

Base it on the current project state: `manifest.json`, artifact status,
validation output, last completed or blocked workflow step. Do not infer
from session memory alone. Localize the line to the conversation language
(e.g. "Next step: ..." when responding in English).

Skip this line when the response is a raw artifact, JSON / YAML / TOML / code
block, machine-readable output, brief tool-call narration ("file written",
"tests passing"), or a turn unrelated to an active workflow.

## Notes

- A scan `seed` drives the explicit local `numpy.random.PCG64` contract in
  `run_scan.py`. Stochastic backends must accept the injected `rng`; ambient
  randomness is rejected and cannot support a reproducibility claim.
- Python 3.11+ required.
- Authoritative test discipline: `python3 -m pytest -q` must stay at zero
  failures on `main`.
