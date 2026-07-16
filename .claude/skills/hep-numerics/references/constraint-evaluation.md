# Constraint Evaluation Contract

This file defines how one prediction becomes constraint columns in `scan.csv`.

## Source of Truth

- Schema syntax: `schemas/constraints-data.schema.json`.
- Runtime behavior: `scripts/run_scan.py`.
- Template: `schemas/examples/constraints-data.example.json`.
- This reference: verdict semantics, formulas, skipped behavior, and allowed
  region rules.

## Highest Rule: Allowed Region

The strict allowed region is:

```text
all used constraints returned verdict == "allowed"
```

Any `excluded` constraint excludes the point.
Any `skipped` constraint means the point is not proven allowed by the used
constraint set.
`skipped` is never positive evidence.

In Phase 0, a skipped evaluation is also a run-level publication blocker. The
evaluator retains a typed skip result for diagnostics, but `run_scan.py` must
exit nonzero before writing or refreshing scan outputs, summaries, or manifest
history if any configured constraint or point is skipped.

Runtime summaries may also report coarse point status for operational purposes.
Do not replace the strict allowed-region rule with a looser summary count when
describing physics reach or drawing the allowed-region overlay.

## Evaluation Inputs

For each point the evaluator receives:

- model parameter values for that row
- observable predictions from task or custom bindings
- the selected constraint object from `constraints-data.json`
- interpolation tables, when the constraint is interpolated

The constraint's `observable` field names the prediction to evaluate.
If the observable is not available, the result is skipped rather than guessed.

## Output Tuple Contract

Every used constraint emits the same four logical values.

| Field | Required value | Meaning |
| --- | --- | --- |
| `verdict` | `allowed`, `excluded`, or `skipped` | Discrete result for this constraint at this point. |
| `margin` | number or null | Signed distance to the nearest boundary; positive is allowed side. |
| `chi2` | number or null | Chi-squared contribution for measurement constraints. |
| `skip_reason` | string or null | Machine-readable reason when verdict is `skipped`. |

For a successful completed run these values are stored in `scan.csv` as
`{id}_verdict`, `{id}_margin`, `{id}_chi2`, and `{id}_skip_reason`.
`skipped` tuples exist only as internal failure diagnostics because their
presence blocks publication of the result pair.

When `verdict == "skipped"`:

- `margin` is null
- `chi2` is null
- `skip_reason` is nonempty

When `verdict != "skipped"`:

- `skip_reason` is null
- `margin` is numeric
- `chi2` is numeric only where the constraint kind defines it

## Missing Predictions

If the prediction is unavailable, the evaluator returns the following
diagnostic and the full scan fails closed:

```json
{
  "verdict": "skipped",
  "margin": null,
  "chi2": null,
  "skip_reason": "prediction unavailable"
}
```

This covers external-only observables, missing derived values, and observable
functions that failed before producing a scalar.

The same rule applies when a prediction, interpolated limit, margin, or chi2 is
boolean, non-scalar, `NaN`, or positive/negative infinity. Such values are
invalid evidence, not missing values that can be silently serialized.

## Direct Constraint Kinds

The following formulas define the runtime meaning of direct constraints.
`pred` is the predicted observable value.

### `measurement`

A measurement defines a central value, one standard uncertainty, and an allowed
sigma band.

Formula:

```text
margin = (central_value - pred) / uncertainty; chi2 = ((pred - central_value) / uncertainty)^2; allowed iff abs(margin) <= sigma
```

Rules:

- `margin` is dimensionless.
- Positive `margin` means the prediction lies below the central value.
- Negative `margin` means the prediction lies above the central value.
- `chi2` is always nonnegative.
- `skip_reason` is null for a successful evaluation.

### `upper_limit`

An upper limit allows predictions at or below the limit.

Formula:

```text
normalizer = abs(limit_value) if limit_value != 0 else 1; margin = (limit_value - pred) / normalizer; allowed iff pred <= limit_value
```

Rules:

- Positive margin is allowed side.
- Negative margin is excluded side.
- `chi2` is null.
- A zero limit uses normalizer `1` to avoid division by zero.

### `lower_limit`

A lower limit allows predictions at or above the limit.

Formula:

```text
normalizer = abs(limit_value) if limit_value != 0 else 1; margin = (pred - limit_value) / normalizer; allowed iff pred >= limit_value
```

Rules:

- Positive margin is allowed side.
- Negative margin is excluded side.
- `chi2` is null.
- A zero limit uses normalizer `1`.

### `allowed_band`

An allowed band accepts predictions between lower and upper bounds.

Formula:

```text
margin = min(limit_value_max - pred, pred - limit_value_min); allowed iff limit_value_min <= pred <= limit_value_max
```

Rules:

- The nearest boundary controls the margin.
- Positive margin means inside the band.
- Negative margin means outside the band.
- `chi2` is null unless a future schema explicitly adds measurement semantics.

### `ratio`

A ratio constraint reuses either upper-limit or band semantics, depending on
which limit fields are present.

Upper-style formula:

```text
margin = (limit_value - pred) / normalizer; allowed iff pred <= limit_value
```

Band-style formula:

```text
margin = min(limit_value_max - pred, pred - limit_value_min); allowed iff limit_value_min <= pred <= limit_value_max
```

Rules:

- If both min and max are present, use band-style semantics.
- If only `limit_value` is present, use upper-style semantics.
- If neither form is present, evaluation fails and the point records a skipped
  constraint with the failure message.
- `chi2` is null.

## Interpolated Constraints

An interpolated constraint first computes a point-dependent limit and then uses
the direct formula for its constraint kind.

Required semantics:

1. Resolve the declared project-contained table path.
2. Read the exact configured `x_column` and `y_column` from a real header.
3. Require at least two finite rows with unique, strictly increasing x nodes;
   never sort, deduplicate, or use positional/headerless fallbacks.
4. Verify `x_parameter` and `x_unit` against `model-spec.json`, and verify
   `y_quantity`/`y_unit` against the constraint observable/unit and every
   applicable model-parameter, custom-binding, or task-return unit.
5. For forbidden extrapolation, require node support to cover all of
   `valid_range`, then reject any point outside that range/support.
6. Interpolate the limit using only the declared supported method.
7. Insert the interpolated limit into the working constraint and evaluate the
   direct formula.

Out-of-range behavior:

```json
{
  "verdict": "skipped",
  "margin": null,
  "chi2": null,
  "skip_reason": "out of interpolation range"
}
```

Evaluation-failure behavior:

- malformed table: skipped with the table or parser error
- unsupported interpolation method: skipped with the method error
- missing x parameter: skipped with the missing-parameter error
- failed numeric conversion: skipped with the conversion error

Only `forbidden` and explicit `nearest` policies are supported. `nearest`
clamps to the declared range and node support; `forbidden` never extrapolates.
Every skipped interpolation result blocks publication of the entire configured
scan; the runner does not publish the in-range subset.

## Manual-Only Constraints

Constraints with `implementation_status == "manual_only"` are recorded but not
numerically applied.

They always evaluate as:

```json
{
  "verdict": "skipped",
  "margin": null,
  "chi2": null,
  "skip_reason": "manual_only constraint"
}
```

Manual-only constraints must not contribute exclusion shading or allowed-region
evidence.
If a manual-only constraint is included in `constraints_used[]`, its skipped
result therefore blocks automated scan publication. Remove it from the
automated constraint set or implement a supported numeric evaluator; do not
relax the fail-closed rule.

## Parameter-Combination Constraints

For `computed_by.type == "parameter_combination"`, the runner first tries to
safe-evaluate the declared formula using canonical model parameter names.

If safe evaluation succeeds, the resulting scalar becomes `pred`.
If safe evaluation fails, the runner may use a custom hook with the same
observable name.
If neither route is available, the point records a skipped constraint with the
failure message in memory, and setup may create a manual stub for the project.
The scan command exits nonzero without publishing outputs.

The formula must not require LaTeX names, display labels, file I/O, or mutable
global state.

## Derived And External Constraints

Derived constraints consume an observable produced elsewhere in the scan.
If the derived observable is unavailable, the constraint is skipped and the
scan fails closed.

External constraints represent information not evaluated by the current numeric
pipeline.
Unless a concrete prediction source is present, they are skipped rather than
converted into arbitrary numbers, and an automated scan that selects them is
not publishable.

## Worked Sign Checks

- Upper limit allowed: `limit = 3.1e-13`, `pred = 2.0e-13`, `margin = 0.3548`, verdict `allowed`.
- Upper limit excluded: `limit = 3.1e-13`, `pred = 5.0e-13`, `margin = -0.6129`, verdict `excluded`.
- Allowed band: `low = 0.90`, `high = 1.10`, `pred = 1.02`, `margin = 0.08`, verdict `allowed`.
- Measurement excluded: `central = 1.05`, `uncertainty = 0.10`, `sigma = 2`, `pred = 1.40`, `margin = -3.5`, `chi2 = 12.25`.

## Debug Checklist

- [ ] Is the constraint id included in `constraints_used[]`.
- [ ] Does the constraint observable have a prediction source.
- [ ] Is `implementation_status` direct, interpolated, or manual-only.
- [ ] For direct constraints, are the required numeric fields present.
- [ ] For interpolated constraints, is the point inside valid range.
- [ ] Does the sign of `margin` agree with the verdict.
- [ ] Is `chi2` present only for measurement-style semantics.
- [ ] Does every skipped verdict have a concrete `skip_reason`.
- [ ] Are skipped constraints excluded from allowed-region evidence.
- [ ] Did any skipped/non-finite/invalid result cause a nonzero scan exit before
      output or manifest mutation rather than a partial successful scan.
