# paper-meta.json Contract

## Purpose

`literature/paper-meta.json` records the published paper identity and retrieval
metadata used by a reproduction project. It anchors `paper_id` values in
`repro-targets.json` and `reproduction-result.json`.

## Shape

Top-level object with:

- `arxiv`
- `doi`
- `title`
- `authors`
- `year`
- optional `journal`
- optional `version_used`
- optional `retrieved`
- optional `hash`

Use `templates/paper-meta.example.json` for example shape only. The repository
schema `schemas/paper-meta.schema.json` is authoritative for required fields,
date patterns, and `additionalProperties: false`.

## Required Entry Shapes

### Identity

At least one of `arxiv` or `doi` must be present. Prefer preserving the paper
version in `arxiv` or `version_used` when the version matters for formulas,
plots, or numerical values.

### `title`

Full paper title as a string. Do not invent a shortened internal title here.

### `authors[]`

Nonempty array of author strings. Preserve ordering from the paper metadata
source when available.

### `year`

Integer publication or preprint year.

### Optional metadata

- `journal`: journal or proceedings citation when known
- `version_used`: paper version such as `v2`, publisher version, or local PDF tag
- `retrieved`: date in `YYYY-MM-DD`
- `hash`: `sha256:<digest>` for the PDF or source file used

## Hard Invariants

- `arxiv` or `doi` must exist.
- `title`, `authors[]`, and `year` must exist.
- `retrieved` uses an ISO date, not a timestamp.
- `hash` uses `sha256:<digest>` if present.
- This file describes the paper as used for reproduction; do not update it to
  newer paper versions without also updating `repro-targets.json`.

## Authoring Checklist

- Confirm that `paper_id` in `repro-targets.json` can be traced to this file.
- Record the exact version used for figures and formulas.
- Include a hash when the input is a local PDF.
- Validate against `schemas/paper-meta.schema.json`.
