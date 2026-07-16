---
name: hep-idea
description: >
  Generate research proposals for particle physics phenomenology.
  This skill produces the complete project foundation in a single pass: research proposal, model formalization (model-spec.json, calc-tasks.json), experimental constraints (constraints-data.json), and benchmark formulas for calculation verification (benchmarks.json).
  Trigger this skill when the user mentions: "research idea", "new project", "propose a topic", "generate proposal", "start a new study", "research direction", or any request to brainstorm or formulate a phenomenology research topic.
  Also trigger when the user wants to derive a follow-up project from the conclusions of a completed study.
---

# HEP Idea Generator

Generate concrete, publishable research proposals for particle physics phenomenology. The goal is not to produce vague directions, but specific, well-scoped problems that can realistically lead to a paper.

## Step 1: Determine the idea source strategy

Before generating ideas, ask the user which approach they want for this session.
Present these three strategies and let them pick:

**Strategy A - Experiment-driven:** Start from a recent experimental anomaly or new data release (e.g., a tension in B-meson measurements, a new LHC search limit, updated neutrino oscillation data). The idea addresses the anomaly with a concrete model or reinterprets new bounds in an existing framework.

**Strategy B - Theory-driven:** Start from a theoretical model or mechanism and explore its phenomenological consequences in a region that hasn't been fully studied (e.g., extending a known Z' model to the lepton sector, combining a seesaw mechanism with a particular dark matter candidate).

**Strategy C - Gap-driven:** Start from an open question or gap identified in existing literature (e.g., "nobody has computed the one-loop correction to this process", "the interplay between constraints X and Y in this model class hasn't been mapped out").

If the user has already indicated a preference in their message (e.g., "there's a new anomaly in muon g-2, let's build a model for it"), skip the strategy question and proceed directly.

## Step 2: Gather context

Based on the chosen strategy, collect the necessary inputs:

- **Strategy A:** Ask what anomaly or dataset. If the user doesn't have one in mind, consult `references/research-directions.md` for current hot topics and suggest 2-5 recent experimental results worth investigating.

- **Strategy B:** Ask what model or mechanism to start from. If vague, consult `references/research-directions.md` and suggest 2-5 model frameworks with unexplored phenomenological territory.

- **Strategy C:** Ask what area to look for gaps. If the user has a previous project (check `workspace/projects/` for completed projects), offer to read its conclusions and derive follow-up questions from there.

When consulting `references/research-directions.md`:
- If the file is missing or unreadable, inform the user that the reference file was not found at the expected path and suggest they check the skill installation. Then fall back to web search (arXiv, INSPIRE) to gather current hot topics instead. Do not halt the workflow; proceed with web-sourced information.
- If the file is present, use it as the primary source but still supplement with web search when available, since the file may not reflect the very latest developments.
- **The reference file is a starting point, not a boundary.** It captures a snapshot of known directions and is inevitably incomplete. Interesting ideas often lie outside any pre-curated list, at the intersection of sub-fields, in very recent developments, or in directions nobody has catalogued yet. Actively consider directions not covered in the file, especially when the user's input or web search results point somewhere new. At least one of the three candidate ideas in Step 3 should come from outside the reference file whenever possible.

Also ask about scope preferences:
- Preferred number of observables to include in the project, for example `1-3`, `3-5`, or `more than 5`
- Any models, experiments, processes, observables, or methods the user particularly wants to avoid discussing

Hard workflow constraint:
- Because the current Package-X workflow supports only tree-level and one-loop calculations, the core observables in candidate ideas must be executable at tree level or one loop. Do not center an idea on two-loop or higher-order calculations. If a topic is otherwise interesting but its main novelty depends on 2-loop or higher-order work, reformulate it around a tree-level or one-loop observable, or discard it.

## Step 3: Generate candidate ideas

Produce **exactly 3** candidate ideas. For each idea, provide:

1. **Title** - a working paper title, specific enough to be informative
2. **One-paragraph pitch** - what the paper would do and why it matters
3. **Model sketch** - particle content and key interactions in 2-3 sentences
4. **Key observables** - the observables you would actually compute,
   with the count chosen to match the user's preferred scope.
   For each observable, specify:
   - Calculation type (tree / 1-loop only)
   - External particles
   - Loop particles (if loop diagram)
   - Target physical quantity (form factor, cross section, decay width, etc.)
   - Priority (high / medium / low)
   These details must be concrete enough to directly populate calc-tasks.json
   entries in Step 5b.
5. **Feasibility note** - estimated difficulty and timeline

The ideas should be meaningfully different from each other, not three variations on the same theme. Aim for one safer/incremental idea, one moderately ambitious idea, and one higher-risk/higher-reward idea.

Quality criteria for a good idea:
- It should be specific enough that you can immediately start writing a Lagrangian (not "study new physics effects in neutrino oscillations" but "constrain a U(1)_{L_mu - L_tau} model using the latest NOvA and T2K disappearance data combined with IceCube DeepCore")
- The observables should be computable with standard tools (FeynCalc, MadGraph, Package-X, etc.) within a reasonable timeframe, with Package-X tasks restricted to tree-level or one-loop calculations in the current workflow
- There should be a clear "punchline": what new thing the reader learns
- If the proposal would only become publishable after a two-loop or higher-order analytic calculation, it is out of scope for this workflow

When generating ideas, draw on knowledge of:
- Current experimental status (LHC Run 3 results, neutrino oscillation global fits, dark matter direct detection limits, flavor anomalies, etc.)
- Standard BSM frameworks (Z' models, two-Higgs-doublet models,
  leptoquarks, scotogenic models, seesaw variants, ALPs, etc.)
- Methodological gaps (missing tree-level or one-loop calculations, unexplored parameter regions, combinations of constraints not yet studied together, etc.)

If web search is available, perform a lightweight but explicit novelty check before finalizing any candidate idea.

Minimum requirements:
- Search for related work from roughly the last three years using relevant keywords on arXiv and INSPIRE.
- Identify at least 3 closely related recent papers or results for each serious candidate idea.
- Briefly summarize how the proposed idea differs from the closest existing work.

If an idea appears too close to existing literature, downgrade or discard it and replace it with a more differentiated alternative.

<!-- Do not merely claim that novelty was checked - surface the outcome of the check in the candidate descriptions and in the final proposal. -->

## Step 4: User selects and refines

Present the 3 ideas and let the user choose one (or ask to modify/combine).
Once selected, ask if they want to adjust anything before generating the full proposal.

## Step 5: Generate full proposal, model formalization, constraints, and benchmarks

This step has multiple phases executed in sequence:
- 5a: determine the project name, initialize the project skeleton, allocate a
  private foundation attempt, and author the research proposal candidate
- 5b: formalize the model into machine-readable definitions
- 5c: collect and structure experimental constraints
- 5d: search for benchmark formulas for calculation verification
- 5e: author candidate `manifest.json`, then finalize the complete generation

Each phase owns only its own outputs. Use `scripts/init_project_skeleton.py`
to create the directory skeleton once. That script creates directories only:
it does **not** create `manifest.json` or any placeholder output files.
After the skeleton exists, Steps 5a-5e may assume their target directories
already exist. They must write only the files assigned to that phase below the
private `candidate_dir` returned by `scripts/init_foundation_attempt.py`; they
must never write the authoritative project artifact paths directly.

### Step 5a - Initialize the project skeleton and write the research proposal

Author the proposal in the private foundation candidate. Follow these steps:

1. Determine the project name from the idea (short, kebab-case, e.g.,
   `u1-lmu-ltau-nova-t2k` or `scotogenic-2loop-leptogenesis`)
2. Run the skeleton initializer located in this skill's own
   `scripts/` directory. Resolve the path relative to the current skill  directory rather than hardcoding a platform-specific repository path.  For example, if you are operating inside the skill directory itself:
   ```
   python scripts/init_project_skeleton.py {project-name}
   ```
   If the current working directory is the repository root, invoke the
   same script via the current skill's path, for example:
   ```
   python .agents/skills/hep-idea/scripts/init_project_skeleton.py {project-name}
   ```
   in Codex-style layouts, or the corresponding `.claude/...` path in Claude-style layouts. The important rule is to resolve `scripts/` relative to the current skill installation, not to assume one fixed top-level platform directory.
   - In the standard `.agents/...` or `.claude/...` skill layout, the script infers the repository root from its own installation path and writes under `workspace/projects/{project-name}/` by default. Users normally should not need to pass any extra root argument.
   - This script creates only the directory skeleton under
     `workspace/projects/{project-name}/`
   - It must not create `manifest.json` or any placeholder output files
   - The resulting skeleton is:
   ```
   workspace/projects/{project-name}/
   |-- idea/
   |-- model/
   |-- calculations/
   |-- constraints/
   `-- numerics/
   |   |-- scan-configs/
   |   |-- scan-results/
   |   `-- figures/
   ```
3. Allocate the branch-specific private attempt from the repository root and
   retain the JSON output for finalization:
   ```
   python scripts/init_foundation_attempt.py \
     --project-dir workspace/projects/{project-name} \
     --owner hep-idea --mode initialize --format json
   ```
   Use `--mode revise` for Branch II and `--mode direct` for Branch III.
   Do not continue if allocation fails. Never guess or reconstruct the returned
   `attempt_dir`, `attempt_id`, or `candidate_dir`.
4. Create `idea/proposal.md` below the returned `candidate_dir` from
   `templates/proposal.md.tmpl`.
   - Fill every placeholder with project-specific content
   - Write in English, academic style
   - Use LaTeX notation for equations (`$...$`, `$$...$$`) where appropriate

#### proposal.md template

Use `templates/proposal.md.tmpl`.
Do not reorder sections.

### Step 5b - Model formalization and calculation task decomposition

Formalize the model into machine-readable definitions. Read the `Model Framework` and `Key Observables and Calculations` sections of the proposal you just wrote, and generate the following two files.

For every JSON artifact below, treat the template as the shape example and the matching `references/*-contract.md` file as the authoritative artifact
contract. Do not change field names, nesting, or semantics relative to those
contracts.
If a contract and a template example ever appear inconsistent, follow the
contract.

#### File 1: `model/model-spec.json`

Machine-readable model definition. This is the **single source of truth** for all downstream modules.

Use `templates/model-spec.example.json` as the local canonical shape reference for this skill. For the authoritative artifact contract - required top-level keys, entry shapes, conditional parameter fields, and cross-file invariants - read `references/model-spec-json-contract.md`.

Write this file first. It is the machine-readable source of truth for downstream modules.

**Hard rules for model-spec.json:**
- For initial hep-idea output, set `version` to `"v1"`.
- Machine-readable canonical names must match `^[A-Za-z_][A-Za-z0-9_]*$`
  and must not be Python hard keywords; each name is project-global and
  immutable, and downstream artifacts must reuse it exactly. See
  `docs/contracts/canonical-name-convention.md`.

#### File 2: `model/calc-tasks.json`

Structured calculation task list. Decompose the Key Observables from the proposal into concrete tasks that package-scribe can execute directly.

Use `templates/calc-tasks.example.json` as the local canonical shape reference for this skill. For the authoritative artifact contract - top-level shape, required task fields, `external_particles` encoding, and tree/loop invariants - read `references/calc-tasks-json-contract.md`.

Decomposition logic:
- Since a single observable may correspond to multiple Feynman diagrams, it may be more appropriate to define one task per observable, depending on the capabilities of package-scribe
- Each task must contain all input information package-scribe needs: Lagrangian terms, structured external legs with momentum labels, loop particles (for loop diagrams), on-shell conditions, and target quantity
- Every task in calc-tasks.json must be executable at tree level or one loop only. Use `type: "tree"` with `loop_order: 0` for tree-level tasks, and `type: "loop"` with `loop_order: 1` for one-loop tasks. Do not emit tasks with `loop_order > 1`.
- Conventions are NOT repeated in tasks; they live in model-spec.json. Only add `"convention_overrides": {...}` if a specific task genuinely needs different conventions (rare)
- All parameter names in calc-tasks.json must match model-spec.json exactly. For particle names, every model-specific or new particle must match a `fields[].name` entry in model-spec.json exactly, while standard SM particle labels may be used directly as long as they follow one consistent shared naming convention. Put momentum labels in sibling fields such as `momentum`, not inside the particle name itself.

#### Post-write review

After generating both model files, write them below the allocated
`candidate_dir` immediately.

Then present a summary of `model-spec.json` and `calc-tasks.json` to the user. Highlight:
- The model definition (fields, interactions, conventions)
- The task list coverage (which observables are included)
- Parameter names and ranges

Pause here for user confirmation before proceeding to constraints and benchmarks, unless the user has explicitly asked for a fully end-to-end run without intermediate review.
If the user requests changes at this stage, revise only the private candidate
before continuing.

### Step 5c - Experimental constraints collection

Immediately after Step 5b, collect and structure the experimental
constraints relevant to this model.

Write the constraint outputs in this order:
1. First write `constraints/constraints-data.json` as the structured source of truth for downstream automation.
2. Then write `constraints/constraints-summary.md` as the human-readable view derived from `constraints-data.json`.

The summary must not overstate automation readiness. It should reflect the actual `implementation_status` and `computed_by` values written to `constraints-data.json`.

**Information sources:**

1. **Proposal's Experimental Constraints section**: The proposal already lists relevant bounds in narrative form. Use this as the starting point.

2. **Web search for latest data**: For each constraint identified in the proposal, search arXiv, INSPIRE, and PDG for the latest experimental values. Specifically look for:
   - PDG latest world averages for relevant quantities
   - LHC search results from the last 1-2 years (may be stronger than those cited in the proposal)
   - Latest global fit results (e.g., neutrino oscillation global fits)

3. **Gap check**: Based on model-spec.json's fields, interactions, and tags, consider whether the proposal missed any important constraints. Common categories to check: electroweak precision (S, T, U parameters), collider direct searches, flavor-changing processes, dark matter bounds (if applicable), cosmological constraints.

**Output files:**

#### `constraints/constraints-data.json`

Structured constraint data. Use
`templates/constraints-data.example.json` as the local canonical shape reference for this skill. For the authoritative artifact contract - top-level shape, required per-constraint fields, `computed_by`, `implementation_status`, and interpolation rules - read `references/constraints-data-json-contract.md`.

Write this file before `constraints/constraints-summary.md`. It is the machine-readable source of truth for downstream modules and for the scan-usability labels shown in the summary.

Treat `computed_by` and `implementation_status` as two independent labels:
- first decide where the theory quantity comes from
- then decide whether hep-numerics can use the constraint automatically right now
- if interpolation is needed, use `interpolated` only when the required local
  asset is present below the candidate `constraints/` tree (seeded from the
  project for a revision) and the interpolation metadata is complete; otherwise
  use `manual_only`

**Hard rules:**
- All parameter names in constraints-data.json must use the same
  canonical names as model-spec.json
- `implementation_status` must accurately reflect whether hep-numerics can automatically process this constraint
- never use `interpolated` unless the asset is already local and immediately usable

#### `constraints/constraints-summary.md`

Human-readable constraint summary generated after
`constraints/constraints-data.json`.

Start with a compact overview that groups or counts constraints by scan usability:
- usable now in hep-numerics (`implementation_status = "direct"`)
- usable now in hep-numerics via bundled interpolation assets
  (`implementation_status = "interpolated"`)
- recorded only / not directly scan-ready
  (`implementation_status = "manual_only"`)

For each constraint, describe:
- What it constrains and why it's relevant to this model
- Current experimental status (measurement value or limit)
- Source reference
- How it maps to the model's observables
- Scan usability, stated explicitly in language consistent with
  `constraints-data.json`

When a constraint is not directly usable in downstream scans, say so plainly in the summary instead of implying full automation support.

### Step 5d - Benchmark formula search

For each task in calc-tasks.json, search for known analytic results in the literature that can serve as verification benchmarks for package-scribe's calculations.

**Search strategy** (per task):
1. Search arXiv/INSPIRE for the specific process + model name
   (e.g., "one-loop Z' contribution muon g-2 analytic")
2. Check the papers already cited in `proposal.md`, especially the original model paper(s), the closest phenomenology papers, and references mentioned in Physical Motivation, Key Observables, or Experimental Constraints, for analytic expressions or useful limits of this observable
3. Search for review papers or textbooks containing general formulas for the process type (e.g., general one-loop contributions to anomalous magnetic moments)
4. Look for known limiting cases (e.g., heavy-mass limit, small-coupling limit) even if a full closed-form result is not available

**If web search is unavailable**: Fall back to built-in knowledge of standard results (the LLM has reliable memory of classic analytic formulas for common processes). Mark such entries with
`"source_type": "training_knowledge"` and note that they have not been verified against the latest literature.

**Output file: `model/benchmarks.json`**

Structured benchmark data. Use `templates/benchmarks.example.json` as the local canonical shape reference for this skill. For the authoritative artifact contract - top-level shape, required benchmark fields, nullability guidance, and benchmark quality rules - read `references/benchmarks-json-contract.md`.

**Quality expectations:**
- For well-studied processes (muon g-2, LFV decays, EW precision), there are almost always known formulas; finding them is expected, not optional
- For novel or niche calculations, it is acceptable to have `has_benchmark: false`, but always provide at least a limiting-case check or a numerical cross-reference suggestion in `notes`
- When citing formulas, convert them to the conventions in model-spec.json whenever this can be done reliably. If a reliable conversion is not possible, note the remaining convention difference explicitly and treat the benchmark as limited-scope rather than fully comparable.

### Step 5e - Author and publish manifest.json

Write `manifest.json` only below the returned `candidate_dir`, and only after
Steps 5a-5d have successfully authored their candidate outputs. Do not create
it earlier as a placeholder. The initial candidate should mark `idea`, `model`,
and `constraints` artifacts as done:

Use `templates/manifest.example.json` as the local canonical shape
reference for this skill. For the authoritative artifact contract - initial
hep-idea file lists, producer fields, checksum rules, and history entries - read
`references/manifest-json-contract.md`.

The template contains schema-valid example values. Replace its project name,
dates, timestamps, and checksum sentinel with values from the current project;
never copy those example literals into a generated manifest.

Finalize the exact allocated attempt from the repository root:

```
python scripts/finalize_foundation_attempt.py \
  --project-dir workspace/projects/{project-name} \
  --attempt-dir {returned-attempt-dir} \
  --attempt-id {returned-attempt-id} \
  --owner hep-idea --mode {initialize|revise|direct} --format json
```

Do not copy candidate files to the project yourself. Report authoritative
completion only when the finalizer returns `published` or a verified
`already_published`. Any other result preserves the prior generation and must
be reported as a failed or blocked publication.

### After successful finalization, tell the user:
- Summarize the proposal in 2-3 sentences
- Summarize the model definition (particle content, key interactions)
- Summarize the calculation task list (how many tasks, what they compute)
- Summarize the constraints collected (how many, which are most important)
- Report benchmark coverage (how many tasks have literature benchmarks, how many do not)
- Remind them to review all outputs before moving to the next step
  (package-scribe)
- Mention that later model or constraint changes are still handled by hep-idea via Branch II / Branch III below.

## Entry branch selection

Before entering the workflow, decide which entry branch matches the current
workspace state and the user's intent. The three branches below share the
same Step 5 artifact contracts and file-writing discipline. The difference is
only how the skill enters the workflow and which earlier steps are skipped.

### Branch I - Full generation

This is the default branch for a fresh idea-generation session.

Use it when the workspace does not yet contain `manifest.json`, or when the
user is clearly asking to start something new: for example, "generate a new
idea", "propose a project", "start a new study", or "let's explore a new
direction".

In this branch, run the normal end-to-end path:
- Step 1: determine the idea source strategy
- Step 2: gather context
- Step 3: generate candidate ideas
- Step 4: let the user select/refine
- Step 5: write the proposal, formalized model, constraints, benchmarks, and
  manifest

If the user already has a fairly concrete idea in mind but still wants a full
proposal and workspace initialized from scratch, this is still Branch I. In
that case, compress Steps 1-4 as needed, but keep the full-generation
behavior and produce the standard Step 5 outputs.

### Branch II - Revise existing artifacts

Use this branch when a project workspace already exists and the user's message
is about modifying what is already there rather than generating a new idea
from scratch. Typical signals include language like "modify", "update",
"adjust", "add", "replace with the latest", "change the model", or "refresh
the constraints".

Revision flow:

1. Read `manifest.json` first. Capture the current
   `active_model_version` and the relevant `artifacts.*.checksum` values so
   you know what version of the project you are editing.
2. Identify which artifacts the user wants changed. The editable set is:
   `model/model-spec.json`, `model/calc-tasks.json`,
   `constraints/constraints-data.json`, and `model/benchmarks.json`.
3. Allocate one `hep-idea:revise` foundation attempt. Read and edit only the
   implicated files in its seeded `candidate_dir`; do not write live artifacts.
4. If the request changes parameter names or parameter references in
   `model-spec.json` or `calc-tasks.json`, enforce canonical-name compliance
   before writing. Do not introduce aliases, display-name variants, or
   case-only rewrites.
5. If `model/model-spec.json` changes, bump the model version
   (`v1 -> v2`, `v2 -> v3`, and so on) and recompute its SHA-256 checksum.

Allowed hep-idea history actions for revisions are `model_complete_v{N}`,
`model_updated`, `constraints_updated`, and `benchmarks_updated`. Use
`model_complete_v{N}` when the model version is bumped; otherwise use the
narrow `_updated` action matching the changed artifact. When one revision
changes multiple scopes, append exactly one action for each changed scope; do
not use one action as a summary label for unrelated changed files. The
foundation finalizer derives the required action set from the staged/live file
diff and rejects missing, extra, duplicated, or misclassified actions.

6. Write updated artifact files only in the candidate, then append one new
   candidate `manifest.history` entry per changed scope with `by: "hep-idea"`,
   the exact action required above, and a short note describing that scope.
7. If the model version changed, also update
   `manifest.artifacts.model.checksum` and `active_model_version` so the rest
   of the workflow can detect staleness correctly.
8. Run the foundation finalizer with the exact allocation tuple. A change to
   `model-spec.json`, `calc-tasks.json`, or `benchmarks.json` mechanically marks
   evidence-bearing calculations stale while preserving their historical task
   registry and dependency. The finalizer also marks affected numerics analyses
   stale in the same transaction; do not edit `artifacts.calculations` or
   `artifacts.numerics` yourself.
9. Report back to the user with three explicit items:
   - what changed
   - whether the model version was bumped
   - whether downstream `calculations/` results are now explicitly stale and
     need to be regenerated

Branch II is the only branch that may publish revisions to existing artifacts,
and it does so only through the private attempt plus finalizer.
Do not silently fall back to "new project" behavior when `manifest.json`
already exists and the user is clearly asking for a targeted update.

### Branch III - Direct formalization

Use this branch when the user is not asking for idea generation at all, but
instead directly provides a model description, interaction structure, or
constraint description that should be formalized into workspace artifacts.

This branch is especially appropriate when `idea/proposal.md` does not exist
and the user is effectively saying "formalize this model" or "organize the
constraints for this setup" rather than "brainstorm a project for me".

Behavior in this branch:

- Skip Steps 1-4 entirely.
- Initialize the directory skeleton when necessary, then allocate one
  `hep-idea:direct` private foundation attempt before authoring any artifact.
- Go directly to Step 5b when the user has supplied enough model content to
  write `model-spec.json` and `calc-tasks.json`.
- Go directly to Step 5c when the user mainly provided a constraint-side
  description and wants `constraints-data.json` / `constraints-summary.md`.
- Do not write `idea/proposal.md`.
- In candidate `manifest.json`, mark `artifacts.idea.status` as `"skipped"`
  and keep its `files` list empty.

If the user provides enough detail for both model formalization and
constraint collection, you may continue through the relevant Step 5 phases in
sequence, but the branch still remains "direct formalization" rather than
"full generation" because no proposal-generation path was used.

### Branch identification

Use workspace state and message intent together to identify the correct
branch:

- If `manifest.json` is missing and the user is asking for a new idea or new
  project, choose Branch I.
- If `manifest.json` exists and the message is about editing, updating,
  adding, replacing, or refreshing existing model / task / constraint /
  benchmark artifacts, choose Branch II.
- If `idea/proposal.md` is missing and the user directly supplies model or
  constraint content to formalize, choose Branch III.
- If the user has a very specific research idea but still wants a normal
  proposal and project scaffold, prefer Branch I rather than Branch III.

If these signals point in different directions or remain ambiguous, stop and
ask the user explicitly which mode they want: full generation, revise
existing artifacts, or direct formalization.

## Handling edge cases

- **User gives a very vague direction** (e.g., "I want to do BSM"): Don't refuse. Consult `references/research-directions.md`, pick 3 diverse sub-topics, and present them as the candidate ideas.

- **User wants to continue from a previous project**: Read the conclusions from `workspace/projects/{old-project}/numerics/analysis-summary-{analysis_id}.md`; when a reproduction overlay exists, also inspect `workspace/projects/{old-project}/literature/` and `workspace/projects/{old-project}/reproduction/reports/`, then derive follow-up questions.

- **User already has a very specific idea**: Skip Steps 1-4 entirely. Go straight to writing the full proposal. The skill should adapt to the user's level of specificity, not force them through every step.

- **Workspace directory doesn't exist yet**: Create `workspace/projects/` before writing.
