# Figure Styles Contract

This file defines the rendering contract for `numerics/figures/{analysis_id}/`.

## Source of Truth

- Schema syntax: figure specs in `schemas/scan-config.schema.json`.
- Runtime behavior: `scripts/make_figures.py`.
- Template: figure entries in `schemas/examples/scan-config.example.json`.
- This reference: visual semantics, label policy, filenames, and review checks.

## Supported Figure Kinds

- `exclusion_2d`: render excluded regions and the strict all-constraints-allowed
  region over two scanned parameters.
- `scan_1d`: render configured observable values against one scanned parameter,
  optionally with constraint bands or limit lines.

New kinds require schema, renderer, tests, and this reference to change together.

## Global Style Defaults

| Setting | Default |
| --- | --- |
| Backend | non-interactive matplotlib `Agg` |
| Base font size | 12 |
| Axis label and title size | 14 |
| Tick label size | 12 |
| Legend font size | 10 |
| Default figure size | `8 x 6` inches |
| `exclusion_2d` size | `8 x 8` inches |
| Output formats | PDF and PNG |
| PNG dpi | 300 |
| Layout | `tight_layout()` before saving |

## Axis Label Rules

Axis labels resolve in this order:

1. for model parameters, use `model-spec.json.parameters[].latex` when present
2. otherwise use the canonical parameter name
3. append units when a non-dimensionless unit is available
4. for observables, use the observable column name plus any unit inferred from a
   matching constraint

Filenames never use LaTeX labels.
Use canonical names or observable names sanitized for the filesystem.

## Matplotlib LaTeX Fallback

The renderer probes for a usable system LaTeX stack.

- If LaTeX is available, `text.usetex` may be enabled.
- If LaTeX is unavailable, fall back to matplotlib mathtext or plain text.
- Figure generation must not fail only because the host lacks LaTeX.
- Escape plain-text labels when `usetex` is active.

## `exclusion_2d` Contract

Inputs:

- `x` and `y` must be scanned parameter columns.
- The CSV must contain `{constraint}_verdict` columns for every listed
  constraint.
- The selected rows must form a rectangular grid in `(x, y)`.

Visual semantics:

- Each constraint gets a distinct fill color for excluded cells.
- Boundary contours use the same hue as the fill.
- The allowed-region overlay is drawn only where every listed constraint verdict
  is `allowed`.
- Skipped constraints never count as allowed.
- The allowed region uses neutral gray fill and a dark boundary.
- The legend names each constraint and the allowed region when present.

Filename pattern:

```text
exclusion-{x}-{y}.pdf
exclusion-{x}-{y}.png
```

## `scan_1d` Contract

Inputs: `x` must be a scanned parameter column.
- Every requested observable must exist as a CSV column.

Visual semantics:

- Plot observable values against the x parameter.
- If repeated x values exist, aggregate consistently before drawing summary
  curves.
- When `overlay_constraint_bands` is true, draw matching measurement bands,
  allowed bands, or limit lines for constraints on the same observable.
- Use log axis scaling when the scan-config declares the x parameter log-scale.

Filename pattern:

```text
scan-{x}-{observable}.pdf
scan-{x}-{observable}.png
```

## Error Tolerance

- Missing scan result files are hard failures, and missing columns fail the affected figure.
- One failed figure must not corrupt scan data.
- Replot-only workflows may overwrite stale figures but must not run the scan.
- Never substitute a different axis, observable, or constraint to make a plot.

## Reviewer Checklist

- [ ] Figure kind is `exclusion_2d` or `scan_1d`.
- [ ] Axes and observables exist in `scan.csv`.
- [ ] Labels use LaTeX/display text only for humans.
- [ ] Filenames use canonical or sanitized machine names.
- [ ] Exclusion overlays use strict all-allowed semantics.
- [ ] PDF and PNG outputs exist or a failure reason is recorded.
