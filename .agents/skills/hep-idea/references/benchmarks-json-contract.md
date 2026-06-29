# benchmarks.json Contract

## Purpose

`model/benchmarks.json` stores literature or trusted-reference benchmarks used
to validate downstream symbolic calculations.

## Shape

Top-level object with:
- `model_name`
- `benchmarks` (array of benchmark entries)

Use `templates/benchmarks.example.json` for example shape only. The contract in
this file is authoritative for required fields and benchmark quality rules.

## Required Benchmark Fields

Each benchmark entry must contain:
- `task_id`
- `observable`
- `has_benchmark`
- `notes`

Conditional expectations:
- when `has_benchmark` is `true`, include `formula_latex` and
  `formula_description`
- when `has_benchmark` is `true`, include `known_limits`, `sources`, and
  `source_type`
- when `has_benchmark` is `false`, omit `formula_latex`, `known_limits`, and
  `numerical_test_point`
- include `numerical_test_point` whenever a reliable spot-check can be derived
  from the cited formula or limit

## `known_limits[]`

When `has_benchmark` is `true`, each limiting-case entry should contain:
- `limit`
- `approximate_result_latex`
- `approximate_result_code`
- `source`

## `numerical_test_point`

When present, include:
- `inputs`
- `expected_value`
- `tolerance`
- `source`

## No-Benchmark Guidance

When `has_benchmark` is `false`, omit benchmark-specific fields rather than
recording null placeholders. In particular, do not include `formula_latex`,
`known_limits`, or `numerical_test_point`. `sources` and `source_type` may also
be omitted when no concrete benchmark source exists. In that case, use `notes`
to record the best fallback check or cross-reference suggestion.

## Quality Rules

- For well-studied processes, finding a benchmark is expected rather than
  optional
- For novel or niche calculations, `has_benchmark: false` is acceptable only if
  `notes` still gives a useful fallback check
- When citing formulas, convert them to the conventions in `model-spec.json`
  whenever this can be done reliably
- If a reliable convention conversion is not possible, record the remaining
  convention difference explicitly and treat the benchmark as limited-scope

## `source_type`

When a concrete benchmark source exists, allowed values are:
- `literature`
- `training_knowledge`

If `has_benchmark` is `false` and the entry records only fallback guidance
rather than an actual benchmark source, omit `source_type`.

## Authoring Checklist

- Prefer formulas already cited or well-supported in the proposal's reference
  trail before branching into broader search
- Favor limiting cases that downstream tools can test cleanly
