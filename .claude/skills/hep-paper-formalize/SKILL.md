---
name: hep-paper-formalize
description: >
  Read a published paper (arXiv id / DOI / local PDF) and turn it into
  workspace artifacts: literature/paper-meta + paper-extract + repro-targets
  (Setup mode), and model/calc-tasks/constraints/benchmarks (Formalize mode).
  Trigger when the user says "reproduce", "reproduce paper", "replicate fig",
  "rebuild paper", "import paper", "build workspace from arxiv", or supplies a
  paper identifier (arXiv id, DOI, PDF path) and asks to formalize it.
  Does NOT do the comparison itself — that is `repro-orchestrator` + 
  `compare_to_reference.py`.
---

# HEP Paper Formalizer

Turn a published paper into the workspace artifacts needed for paper-anchored
reproduction. This skill has two independent modes: **Setup mode** authors the
`literature/` inputs, and **Formalize mode** reads `literature/paper-extract.json`
to author `model/`, `constraints/`, and `model/benchmarks.json`. Both modes write
only private foundation candidates and require mechanical finalization before
their outputs become authoritative.

This skill operates under `docs/contracts/honest-reproduction-principle.md`.
Paper formulas, curves, tables, and benchmark numbers are comparison targets
only. They must not become computational backends for Package-X, Python
translation, scans, or reproduction verdicts.

Mode selection is normally made by `repro-orchestrator` from
`manifest.artifacts.model.status`: a paper-first project runs Setup then
Formalize; a project that already has a model can run Setup only; a project
with existing `literature/paper-extract.json` can run Formalize only.

## 1. Mode Classification

Classify the mode before reading or writing artifacts.

| Mode | Inputs | Use when | First action | Hard fails |
| --- | --- | --- | --- | --- |
| `setup-only` | Paper identifier or PDF path; project name or existing project directory | Add `literature/` artifacts to a project whose model already exists, or prepare comparison targets before later formalization | Locate or initialize the skeleton, allocate a Setup candidate, then author the literature files | Missing paper identifier; request to write calculation outputs; no user decision on target subset when target selection is ambiguous |
| `formalize-only` | Existing `literature/paper-extract.json`; project directory | Convert a completed paper extract into `model/`, `constraints/`, and benchmark artifacts | Allocate a Formalize candidate, then read its seeded extract and author model/constraint files | Missing paper extract; extracted formulas requested as calculation backend; request to fetch latest external constraints instead of using the paper |
| `setup+formalize` | Paper identifier or PDF path; project name or empty project directory | Build a paper-first workspace from scratch | Run Setup mode through user target confirmation, then continue into Formalize mode | Any Setup hard fail; user declines target set; unresolved canonical naming conflicts |

Hard rules:

- Machine-readable canonical names must match `^[A-Za-z_][A-Za-z0-9_]*$`,
  must not be Python hard keywords, and must reuse exact
  `model/model-spec.json` names once formalization exists; see
  `docs/contracts/canonical-name-convention.md`.
- `literature/` is an input bucket for reproduction; `reproduction/` is not
  written by this skill except directory skeleton creation.
- Do not modify `calculations/`, `numerics/scan-configs/`, or
  `reproduction/runs/`.
- Before authoring a mode, allocate exactly one private candidate with
  `scripts/init_foundation_attempt.py --owner hep-paper-formalize`. Use mode
  `setup` for Setup and `formalize` for Formalize. Write only below its returned
  `candidate_dir`; never edit live foundation artifacts or `manifest.json`.
- Setup+Formalize uses two attempts: successfully finalize Setup, allocate a
  new Formalize attempt from that authoritative state, then finalize Formalize.
- If the user asks to compare against scan outputs, stop after writing the
  inputs and route the comparison to `repro-orchestrator` +
  `scripts/compare_to_reference.py`.

## 2. Setup Mode

Write `literature/` artifacts that describe what the paper says and what the
project should later reproduce.

Use these local references when authoring JSON:

- `references/paper-meta-contract.md`
- `references/paper-extract-contract.md`
- `references/repro-targets-contract.md`

```text
Step 0  Confirm / create the project skeleton and allocate a Setup attempt
        If workspace/projects/<name>/ does not exist or is empty:
          - Call scripts/init_paper_project_skeleton.py relative to the current
            skill installation. It creates literature/, literature/digitized/,
            literature/style/, reproduction/, reproduction/runs/,
            reproduction/figures/, reproduction/reports/, and related directories.
          - manifest.json does not exist at this point; Setup mode authors the
            initial candidate version in Step 9
        If the project already has manifest.json:
          - Read it and confirm fields such as active_model_version
          - Step 9 uses update rather than create

        From the repository root, allocate and retain the exact JSON result:
          python scripts/init_foundation_attempt.py \
            --project-dir workspace/projects/<name> \
            --owner hep-paper-formalize --mode setup --format json
        Every path in Steps 2-9 is relative to the returned candidate_dir.
        Do not continue if allocation fails, and never reconstruct its token.

Step 1  Receive the paper identifier (arxiv id / DOI / PDF path)
Step 2  Fetch / read the PDF and write literature/paper-meta.json
Step 3  Digest the paper and extract structured middleware → literature/paper-extract.json
        Includes fields[] / parameters[] / interactions[] / constraints_in_paper[]
        / observables[] / formulas[] / scan_config_hints[] / unit_conversion_notes[]

        **Important: write boundary for formulas[]** (matches the Forbidden Outputs section below):
        - Allowed: the LLM may excerpt formula LaTeX strings explicitly written in the paper
          into formulas[].latex, with source_anchor (section / equation number) and
          human_review_required: true on every entry
        - Forbidden: any downstream skill / script must not treat formulas[] as a computational backend
          (these formulas may only be copied into model/benchmarks.json for after-the-fact validation;
           see the Forbidden Outputs section)
        - Forbidden: automatically extracting formulas from rendered PDF images via OCR
          (OCR is unreliable and can introduce incorrect benchmarks)

        **Naming constraint for constraints_in_paper[]** (fixes ID-ordering issues):
        - During Setup, c-001-style canonical constraint IDs do **not** exist yet
          (those IDs are generated when Formalize mode writes constraints-data.json)
        - Therefore this stage may only use paper-local labels, for example:
          {"label": "MEG bound 2016", "source_anchor": "Tab. 2, row 1"}
        - After Formalize mode generates constraints-data.json, it writes
          paper_local_to_canonical back into paper-extract.json:
          {"paper_local_to_canonical": {"MEG bound 2016": "c-001", ...}}
        - Draft constraint references in repro-targets.json use paper-local
          labels. Formalize mode replaces them with canonical IDs; the
          paper_local_to_canonical mapping preserves the original labels for audit.

Step 4  Scan the paper and list reproducible candidate targets:
        all figures / tables / key numerical results / analytic formulas
Step 5  *** Intermediate user confirmation ***
        Present the candidate target list to the user and let the user choose:
        "all / specified subset / only fig-3a + tab-2 / defer target selection"
Step 6  Write literature/repro-targets.json (draft) according to the user's choice
        Each target's constraint references use paper-local labels (see the Step 3 notes)
        - benchmark_point means exactly one reference row; use
          keyed_benchmark_set for two or more keyed benchmark rows
        - Every keyed numeric target declares explicit match_columns containing
          the required axes and disjoint from observables; never infer a reduced
          key from columns that happen to be shared by two CSV files
        - figure_curve declares its complete comparison_domain and uses only
          curve_representation=single_valued_y_of_x; do not encode a parametric
          or multi-valued curve as this kind
        - parametric_curve declares one path parameter and complete parameter
          domain, ordered_parametric_xy representation, open/closed topology,
          and exact coordinate scales; it uses normalized symmetric continuous-
          polyline geometry and never projects another varying scan axis
        - exclusion_region declares coordinate scales and one authoritative
          boundary mode, including component/order/open-closed metadata and an
          excluded-side probe; disconnected/holed targets declare every closed
          reference face with unique ID, immediate parent, interior/exterior
          excluded side, and authoritative excluded probe; never infer a
          boundary or missing face semantics from raw scan coordinates
        - every non-formula target declares scan_parameters, an exact fixed
          slice for all hidden scan axes, and canonical normalization metadata
        - tolerance and coverage policy are predeclared and never adjusted after
          comparison output is seen
Step 7  Create auditable reference evidence for every selected target
        For each formula target:
          - Write a nonempty structured JSON record conforming to
            schemas/formula-reference.schema.json
          - Bind paper_id, target_id, expression, source_locator, and acquired_at
        For each numeric target:
          - Ask the user for the paper/author/supplemental digitized data, or use
            an interactive digitization workflow and record its source locator
          - Preserve the imported/digitized values in a distinct immutable raw CSV
          - Write a distinct canonical-unit CSV for comparison
          - Write a third JSON normalization record conforming to
            schemas/normalization-record.schema.json; bind both paths and hashes,
            source/canonical units, exact finite factor/offset per column,
            fixed-parameter conversion metadata, and acquisition provenance
          - Do not overwrite the raw file, inject fixed scan columns into it,
            guess units in the comparator, or source evidence from generated
            numerics/reproduction artifacts
Step 8  Write literature/repro-summary.md (describes input intent, not results)
Step 9  Write / update candidate manifest.json:
        - If the manifest does not exist (paper-first project): create the initial version
            * Set project_name, created, and last_updated to today
            * active_model_version = null (the model has not been written yet)
            * artifacts.idea = {status: "skipped", files: [], produced_by: null,
                                timestamp: null} ("skipped" is allowed by schemas/manifest.schema.json)
            * artifacts.model / calculations / numerics all use empty skeletons
              (same as the initial template in hep-idea manifest-json-contract.md)
            * artifacts.literature = {status: "done", files: [...],
                                      produced_by: "hep-paper-formalize",
                                      timestamp: now}
            * artifacts.reproduction = {status: "not_started", runs: [], ...}
            * history: one entry { action: "literature_complete",
                                    by: "hep-paper-formalize" }
        - If the manifest already exists (adding reproduction to an existing hep-idea project):
            * Only append / update artifacts.literature
            * Add "literature_complete" or "literature_updated" to history
            * Do not touch other artifacts
Step 10 Finalize the exact Setup attempt:
          python scripts/finalize_foundation_attempt.py \
            --project-dir workspace/projects/<name> \
            --attempt-dir <returned-attempt-dir> \
            --attempt-id <returned-attempt-id> \
            --owner hep-paper-formalize --mode setup --format json
        Never copy the candidate into the project yourself. Only `published`
        or verified `already_published` is authoritative success.
```

Setup mode candidate output paths:

- `literature/paper-meta.json`
- `literature/paper-extract.json`
- `literature/repro-targets.json`
- `literature/digitized/` scaffolding and, for each selected numeric target,
  distinct raw CSV, canonical-unit CSV, and normalization-record JSON evidence
- structured formula-reference JSON evidence for each selected formula target
- `literature/style/paper-style.mplstyle` only when the user asks for a style template
- `literature/repro-summary.md`
- candidate `manifest.json` literature entries only as specified by Step 9;
  the finalizer publishes it last

## 3. Formalize Mode

Author the project-native model, calculation task, constraint, and benchmark
candidate from the paper extract, then publish it through the finalizer.

```text
Step 0  Allocate a fresh Formalize attempt from the authoritative Setup state:
          python scripts/init_foundation_attempt.py \
            --project-dir workspace/projects/<name> \
            --owner hep-paper-formalize --mode formalize --format json
        Every path below is relative to the returned candidate_dir.
Step 1  Read candidate literature/paper-extract.json
Step 2  Write model/model-spec.json according to docs/contracts/canonical-name-convention.md
Step 3  Write model/calc-tasks.json according to the contracts in docs/contracts/
Step 4  Write constraints/constraints-data.json (use the constraint values given by the paper; do not search the web for newer values)
        - Generate c-001, c-002, ... canonical constraint IDs
        - Write paper_local_to_canonical back into literature/paper-extract.json:
          {"paper_local_to_canonical": {"MEG bound 2016": "c-001", ...}}
        - Refresh literature/repro-targets.json in sync: replace paper-local
          labels in targets with canonical IDs; preserve the original labels in
          paper-extract.json's paper_local_to_canonical mapping
Step 5  Write model/benchmarks.json (paper formulas as benchmarks, has_benchmark=true,
        source_type: "literature")
Step 6  Write candidate manifest.json: active_model_version = "v1"
        artifacts.model/.constraints.produced_by = "hep-paper-formalize"
        artifacts.idea.status = "skipped" (allowed by schemas/manifest.schema.json)
        history: model_complete_v1, constraints_complete
Step 7  Finalize the exact Formalize attempt with
        scripts/finalize_foundation_attempt.py, passing the returned project,
        attempt_dir, attempt_id, owner hep-paper-formalize, mode formalize, and
        --format json. Do not report success unless it returns `published` or
        verified `already_published`.
```

Formalize mode candidate output paths:

- `model/model-spec.json`
- `model/calc-tasks.json`
- `model/benchmarks.json`
- `constraints/constraints-data.json`
- `constraints/constraints-summary.md` when useful for human review
- `literature/paper-extract.json` only to add `paper_local_to_canonical`
- `literature/repro-targets.json` only to replace paper-local labels with
  canonical constraint IDs
- candidate `manifest.json` model and constraint entries only as specified by
  Step 6; the finalizer publishes it last

Do not search the web for newer constraints during Formalize mode. The point is
to formalize the paper being reproduced, not to update the paper.

## 4. Forbidden Outputs

Source of authority: `docs/contracts/honest-reproduction-principle.md`.

> **Forbidden**: `hep-paper-formalize` MUST NOT write any of the following:
> - `result.wl`, `result-python.py`, `result-meta.json` —— these belong to `package-scribe`
> - `numerics/scan-configs/*` —— these belong to `hep-numerics` or `repro-orchestrator`
> - `reproduction/runs/*` —— these belong to `repro-orchestrator` + `compare_to_reference.py`
>
> **Specifically**: when extracting paper formulas in Formalize mode, write them
> ONLY into `model/benchmarks.json.formula_latex` (existing benchmark slot,
> `source_type: "literature"`). DO NOT translate paper formulas into
> `result-python.py` or supply them as Python "starter code" for package-scribe.
> Package-X derivation is package-scribe's responsibility; supplying paper
> formulas to it short-circuits the independent derivation required by HRP.

- MUST NOT write `result-python.py` / `numerics/scan-configs/*` / `reproduction/runs/*`.
- MUST NOT write `result.wl` or `result-meta.json`.
- MUST NOT OCR formulas from rendered PDF images and promote them to a backend.
- MUST NOT use paper formulas as starter code for Package-X or Python.
- MUST NOT write comparison verdicts, metrics, diagnostic reports, or
  `reproduction-result.json`.

Allowed formula handling:

- LLM may excerpt formulas that are written in the paper text into
  `literature/paper-extract.json.formulas[].latex`.
- Each excerpted formula must include `source_anchor` and
  `human_review_required: true`.
- These formulas may only become benchmarks or report-side comparison text.

## 5. Relationship To hep-idea

- `hep-paper-formalize` and `hep-idea` do not depend on each other and do not
  call each other.
- Both reference `docs/contracts/canonical-name-convention.md` and the shared
  schema contracts.
- Trigger keywords must remain disjoint:
  - hep-idea: "research idea / new project / propose a topic / brainstorm"
  - hep-paper-formalize: "reproduce / reproduce paper / replicate / import paper / arxiv id"
- `hep-idea` Branch II model revision can apply to projects created by
  `hep-paper-formalize`; `produced_by` does not affect revision semantics.

If the user asks for a new research direction, use `hep-idea`. If the user asks
to import, formalize, or reproduce a specific paper, use `hep-paper-formalize`.

## 6. Reference And Template Map

- `references/paper-meta-contract.md`: contract for `literature/paper-meta.json`
- `references/paper-extract-contract.md`: contract for `literature/paper-extract.json`
- `references/repro-targets-contract.md`: contract for `literature/repro-targets.json`
- `templates/paper-meta.example.json`: canonical paper metadata example
- `templates/paper-extract.example.json`: canonical paper extraction example
- `templates/repro-targets.example.json`: canonical reproduction target example
- `schemas/formula-reference.schema.json`: formula comparison-evidence schema
- `schemas/normalization-record.schema.json`: raw-to-canonical normalization
  evidence schema
- `templates/paper-style.mplstyle`: optional matplotlib style template copied
  to `literature/style/paper-style.mplstyle`
- `scripts/init_paper_project_skeleton.py`: creates directories only; it does
  not create `manifest.json`
- repository `scripts/init_foundation_attempt.py`: allocates and seeds the
  private owner/mode-bound candidate
- repository `scripts/finalize_foundation_attempt.py`: validates and
  transactionally publishes the candidate with `manifest.json` last

## 7. Final Delivery Self-Check Checklist

- [ ] Mode is classified as `setup-only`, `formalize-only`, or `setup+formalize`.
- [ ] Each executed mode has its own exact foundation allocation tuple; every
      generated path is below its returned `candidate_dir`.
- [ ] Canonical names satisfy the hard rule above across repro-targets, model,
      constraints, and benchmarks.
- [ ] Every `scan_table` target has explicit, unique `match_columns` containing
      `x_param` and `y_param` and not overlapping `observables`.
- [ ] Every `benchmark_point` target has exactly one reference row; multi-row
      benchmark evidence uses `keyed_benchmark_set` with unique keys.
- [ ] Every `figure_curve` target declares and fully covers its predeclared
      domain and is finite, duplicate-free, and single-valued.
- [ ] Every `parametric_curve` target declares and fully covers its path-
      parameter domain, uses ordered finite nodes, fixes every other scan axis,
      and declares open/closed topology plus exact coordinate scales.
- [ ] Every `exclusion_region` target declares coordinate normalization,
      authoritative boundary construction, topology metadata, and an
      excluded-side probe; disconnected/holed targets completely declare every
      face's parent, side, and probe; raw scan points are not used as a boundary fallback.
- [ ] Every higher-dimensional target comparison fixes all hidden scan
      parameters to one exact declared slice; no aggregation or nearest-match
      slice is used.
- [ ] Every formula target has structured, nonempty formula-reference evidence
      bound to the paper and target.
- [ ] Every numeric target has separate raw, canonical-unit, and normalization
      record files; the record binds hashes, units, conversions, and acquisition
      provenance.
- [ ] Tolerances, comparison domains, coverage rules, and unit conversions were
      chosen before comparison results were inspected.
- [ ] HRP-compliant formula handling is preserved: excerpting is allowed, OCR backends are forbidden.
- [ ] Excerpted formulas include `source_anchor` and
      `human_review_required: true`.
- [ ] Setup candidate paths are only under `literature/`, plus candidate
      manifest and the permitted live directory skeleton.
- [ ] Formalize candidate paths are only under `model/` and `constraints/`,
      plus the permitted candidate literature mappings and manifest.
- [ ] The exact finalizer returned `published` or verified
      `already_published`; no candidate was copied to live paths manually.
- [ ] No `result.wl`, `result-python.py`, `result-meta.json`,
      `numerics/scan-configs/*`, or `reproduction/runs/*` file was written.
- [ ] `constraints_in_paper[]` uses paper-local labels during Setup mode.
- [ ] Formalize mode resolves paper-local constraint labels to `c-NNN` IDs and
      preserves the old labels for audit.
- [ ] `scan_config_hints[].missing_fields[]` records missing L1 inputs rather
      than pretending the paper gives a complete scan definition.
- [ ] `.claude/skills/hep-paper-formalize/` and
      `.agents/skills/hep-paper-formalize/` remain byte-identical after skill edits.
