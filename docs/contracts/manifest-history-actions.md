# Manifest History Actions — Cross-Layer Consistency Rule

Project-level rule: a new value of `manifest.history[*].action` is meaningful
only if every consumer of the manifest knows how to handle it. A schema-valid
manifest whose history action the skills/orchestrators do not understand is
still a workflow bug.

## When adding a new history action

The action name must be added in **all** of:

- `schemas/manifest.schema.json`, where `history[*].action` is constrained by
  exact literals or canonical dynamic-action patterns
- The **owning skill's** `SKILL.md` (allowed-actions section / step describing
  when the action is emitted)
- The **owning skill's** `references/manifest-json-contract.md` (if present)
- **Both** orchestrator definitions:
  - `.claude/agents/<orchestrator>.md`
  - `.codex/agents/<orchestrator>.toml`
- Relevant workspace fixtures under `workspace/projects/*` if the new action
  appears in their history

Then add a contract test asserting cross-layer consistency: the action
appears in all required surfaces, and nowhere else.

## Current reproduction actions

The PR-2 reproduction workflow currently reserves these history actions across
the manifest contract, `hep-paper-formalize`, and `repro-orchestrator`:

- `literature_complete`
- `literature_updated`
- `reproduction_run_complete`
- `reproduction_run_failed`

## Current calculation actions

Calculation history actions use one of these canonical forms:

- `calc_task_{task_id}_complete`, where `{task_id}` is the literal task id such
  as `task-001`; for example `calc_task_task-001_complete`
- `calc_task_{task_id}_revised`, for reruns, backend corrections, or other
  revisions of an existing task artifact
- `calculations_updated`, only for legacy or manual aggregate updates spanning
  multiple tasks; the history entry must include a `note` explaining the
  affected tasks

Do not encode physics labels or prose in the `action` string. Put that context
in `history[*].note`.

## Why this is load-bearing

Manifest history is the project's audit trail. If a skill writes an action
the orchestrator does not recognize, status reports become unreliable and
staleness detection breaks silently.
