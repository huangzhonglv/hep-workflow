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
reproduction. This skill has two independent modes: **Setup mode** writes the
`literature/` inputs, and **Formalize mode** reads `literature/paper-extract.json`
to write `model/`, `constraints/`, and `model/benchmarks.json`.

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
| `setup-only` | Paper identifier or PDF path; project name or existing project directory | Add `literature/` artifacts to a project whose model already exists, or prepare comparison targets before later formalization | Locate or initialize the project skeleton, then write `literature/paper-meta.json`, `literature/paper-extract.json`, `literature/repro-targets.json`, and `literature/repro-summary.md` | Missing paper identifier; request to write calculation outputs; no user decision on target subset when target selection is ambiguous |
| `formalize-only` | Existing `literature/paper-extract.json`; project directory | Convert a completed paper extract into `model/`, `constraints/`, and benchmark artifacts | Read `paper-extract.json`, then write model and constraint artifacts using canonical names | Missing paper extract; extracted formulas requested as calculation backend; request to fetch latest external constraints instead of using the paper |
| `setup+formalize` | Paper identifier or PDF path; project name or empty project directory | Build a paper-first workspace from scratch | Run Setup mode through user target confirmation, then continue into Formalize mode | Any Setup hard fail; user declines target set; unresolved canonical naming conflicts |

Hard rules:

- Use canonical ASCII names for every machine-readable parameter name.
- `literature/` is an input bucket for reproduction; `reproduction/` is not
  written by this skill except directory skeleton creation.
- Do not modify `calculations/`, `numerics/scan-configs/`, or
  `reproduction/runs/`.
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
Step 0  Confirm / create the project skeleton
        If workspace/projects/<name>/ does not exist or is empty:
          - Call .claude/skills/hep-paper-formalize/scripts/init_paper_project_skeleton.py
            (newly written, following the pattern of hep-idea/scripts/init_project_skeleton.py,
             and additionally creating literature/, literature/digitized/, literature/style/,
             reproduction/, reproduction/runs/, reproduction/figures/,
             reproduction/reports/, and related directories)
          - manifest.json does not exist at this point; Setup mode writes the initial version in Step 9
        If the project already has manifest.json:
          - Read it and confirm fields such as active_model_version
          - Step 9 uses update rather than create

Step 1  Receive the paper identifier (arxiv id / DOI / PDF path)
Step 2  Fetch / read the PDF and write literature/paper-meta.json
Step 3  Digest the paper and extract structured middleware → literature/paper-extract.json
        Includes fields[] / parameters[] / interactions[] / constraints_in_paper[]
        / observables[] / formulas[] / scan_config_hints[] / unit_conversion_notes[]

        **Important: write boundary for formulas[]** (mirrors the exclusions in §13):
        - Allowed: the LLM may excerpt formula LaTeX strings explicitly written in the paper
          into formulas[].latex, with source_anchor (section / equation number) and
          human_review_required: true on every entry
        - Forbidden: any downstream skill / script must not treat formulas[] as a computational backend
          (these formulas may only be copied into model/benchmarks.json for after-the-fact validation;
           see §4.6 Forbidden outputs)
        - Forbidden: automatically extracting formulas from rendered PDF images via OCR
          (OCR is unreliable and can introduce incorrect benchmarks)

        **Naming constraint for constraints_in_paper[]** (fixes ID-ordering issues):
        - During Setup, c-001-style canonical constraint IDs do **not** exist yet
          (those IDs are generated when Formalize mode writes constraints-data.json)
        - Therefore this stage may only use paper-local labels, for example:
          {"label": "MEG bound 2016", "source_anchor": "Tab. 2, row 1"}
        - After Formalize mode generates constraints-data.json, it writes back a
          resolved_mapping into paper-extract.json:
          {"MEG bound 2016": "c-001", ...}
        - Constraint references in repro-targets.json targets also use paper-local
          labels; compare_to_reference.py resolves them through resolved_mapping

Step 4  Scan the paper and list reproducible candidate targets:
        all figures / tables / key numerical results / analytic formulas
Step 5  *** Intermediate user confirmation (solution for risk H) ***
        Present the candidate target list to the user and let the user choose:
        "all / specified subset / only fig-3a + tab-2 / defer target selection"
Step 6  Write literature/repro-targets.json (draft) according to the user's choice
        Each target's constraint references use paper-local labels (see the Step 3 notes)
Step 7  For each figure_curve / exclusion_region target, prompt the user:
        "Please provide the digitized CSV, or use WebPlotDigitizer interactively and then tell me the path"
Step 8  Write literature/repro-summary.md (describes input intent, not results)
Step 9  Write / update manifest.json:
        - If the manifest does not exist (paper-first project): create the initial version
            * Set project_name, created, and last_updated to today
            * active_model_version = null (the model has not been written yet)
            * artifacts.idea = {status: "skipped", files: [], produced_by: null,
                                timestamp: null} ("skipped" requires the schema enum extension in §3.2)
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
```

Setup mode output paths:

- `literature/paper-meta.json`
- `literature/paper-extract.json`
- `literature/repro-targets.json`
- `literature/digitized/` scaffolding or user-provided digitized CSV pointers
- `literature/style/paper-style.mplstyle` only when the user asks for a style template
- `literature/repro-summary.md`
- `manifest.json` literature entries only as specified by Step 9

## 3. Formalize Mode

Write the project-native model, calculation task, constraint, and benchmark
artifacts from the paper extract.

```text
Step 1  Read literature/paper-extract.json
Step 2  Write model/model-spec.json according to docs/contracts/canonical-name-convention.md
Step 3  Write model/calc-tasks.json according to the contracts in docs/contracts/
Step 4  Write constraints/constraints-data.json (use the constraint values given by the paper; do not search the web for newer values)
        - Generate c-001, c-002, ... canonical constraint IDs
        - Write resolved_mapping back into literature/paper-extract.json:
          {"paper_local_to_canonical": {"MEG bound 2016": "c-001", ...}}
        - Refresh literature/repro-targets.json in sync: replace paper-local
          labels in targets with canonical IDs (preserve the paper-local label
          as _label_was for audit)
Step 5  Write model/benchmarks.json (paper formulas as benchmarks, has_benchmark=true,
        source_type: "literature")
Step 6  Write manifest.json: active_model_version = "v1"
        artifacts.model/.constraints.produced_by = "hep-paper-formalize"
        artifacts.idea.status = "skipped" (the schema already adds this enum value in §3.2)
        history: model_complete_v1, constraints_complete
```

Formalize mode output paths:

- `model/model-spec.json`
- `model/calc-tasks.json`
- `model/benchmarks.json`
- `constraints/constraints-data.json`
- `constraints/constraints-summary.md` when useful for human review
- `literature/paper-extract.json` only to add `paper_local_to_canonical`
- `literature/repro-targets.json` only to replace paper-local labels with
  canonical constraint IDs while preserving `_label_was`
- `manifest.json` model and constraint entries only as specified by Step 6

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
- `templates/paper-style.mplstyle`: optional matplotlib style template copied
  to `literature/style/paper-style.mplstyle`
- `scripts/init_paper_project_skeleton.py`: creates directories only; it does
  not create `manifest.json`

## 7. Final Delivery Self-Check Checklist

- [ ] Mode is classified as `setup-only`, `formalize-only`, or `setup+formalize`.
- [ ] All parameter names satisfy canonical-name compliance: ASCII letters,
      digits, and underscores only.
- [ ] Every machine-readable parameter name used in repro-targets, model,
      constraints, and benchmarks matches `model/model-spec.json` once model
      formalization exists.
- [ ] HRP-compliant formula handling is preserved: excerpting is allowed, OCR backends are forbidden.
- [ ] Excerpted formulas include `source_anchor` and
      `human_review_required: true`.
- [ ] Setup mode output paths are only under `literature/`, plus the permitted
      manifest update and directory skeleton.
- [ ] Formalize mode output paths are only under `model/` and `constraints/`,
      plus the permitted literature mapping updates and manifest update.
- [ ] No `result.wl`, `result-python.py`, `result-meta.json`,
      `numerics/scan-configs/*`, or `reproduction/runs/*` file was written.
- [ ] `constraints_in_paper[]` uses paper-local labels during Setup mode.
- [ ] Formalize mode resolves paper-local constraint labels to `c-NNN` IDs and
      preserves the old labels for audit.
- [ ] `scan_config_hints[].missing_fields[]` records missing L1 inputs rather
      than pretending the paper gives a complete scan definition.
- [ ] `.claude/skills/hep-paper-formalize/` and
      `.agents/skills/hep-paper-formalize/` remain byte-identical after skill edits.
