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

These values are stored in `scan.csv` as `{id}_verdict`, `{id}_margin`,
`{id}_chi2`, and `{id}_skip_reason`.

When `verdict == "skipped"`:

- `margin` is null
- `chi2` is null
- `skip_reason` is nonempty

When `verdict != "skipped"`:

- `skip_reason` is null
- `margin` is numeric
- `chi2` is numeric only where the constraint kind defines it

## Missing Predictions

If the prediction is unavailable, the evaluator returns:

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

1. Load the interpolation table named by the constraint metadata.
2. Read the configured x parameter from the current point.
3. Reject evaluation if the x value is outside `valid_range`.
4. Interpolate the limit using the declared method.
5. Insert the interpolated limit into the working constraint.
6. Evaluate the direct formula.

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

Do not extrapolate unless the runtime and constraint metadata explicitly support
that policy.

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

## Parameter-Combination Constraints

For `computed_by.type == "parameter_combination"`, the runner first tries to
safe-evaluate the declared formula using canonical model parameter names.

If safe evaluation succeeds, the resulting scalar becomes `pred`.
If safe evaluation fails, the runner may use a custom hook with the same
observable name.
If neither route is available, the point records a skipped constraint with the
failure message, and setup may create a manual stub for the project.

The formula must not require LaTeX names, display labels, file I/O, or mutable
global state.

## Derived And External Constraints

Derived constraints consume an observable produced elsewhere in the scan.
If the derived observable is unavailable, the constraint is skipped.

External constraints represent information not evaluated by the current numeric
pipeline.
Unless a concrete prediction source is present, they are skipped rather than
converted into arbitrary numbers.

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
