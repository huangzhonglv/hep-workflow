# Custom Observables Guide

This file defines the contract for project-level
`numerics/custom_observables.py`.

## Source of Truth

- Schema syntax: custom bindings in `schemas/scan-config.schema.json`.
- Runtime behavior: `scripts/validate_scan_config.py` and `scripts/run_scan.py`.
- Template: skeletons emitted transactionally by `scripts/init_analysis.py`.
- This reference: function signatures, edit boundaries, smoke-test behavior, and
  failure modes.

## Trigger Checklist

Open this guide when:

1. `scan-config.json` has an observable with `source.type == "custom"`.
2. A constraint uses a derived observable that is produced by custom code.
3. A `parameter_combination` formula cannot be safe-evaluated and needs a
   fallback custom hook.
4. A generated `numerics/custom_observables.py` skeleton still contains a stub.

If all observables are task-backed and all parameter combinations are
safe-evaluable, no custom observable edit is needed.

## Directory Layout

Custom observables are project-level shared code:

```text
{project}/numerics/custom_observables.py
```

They are not stored under an `analysis_id`.
Multiple analyses in the same project may import the same module.
Do not add analysis-specific side effects or hidden global state.

## Edit Boundary

You may edit function bodies, local helpers, and short comments.
Do not change public function names, keyword names, return type behavior, or
unrelated functions unless every dependent config is updated too.

The safe change is to preserve the interface and improve only the implementation.

## Function Signature Contract

Every public custom observable function must:

- be importable from `numerics/custom_observables.py`
- accept keyword arguments
- use canonical model parameter names for parameter keywords
- never use LaTeX or Unicode aliases as keyword names
- optionally accept `task_outputs` for derived observables
- optionally accept the injected local `rng` for stochastic observables
- return one real finite scalar accepted by the runtime (`int`, `float`, or the
  corresponding NumPy scalar; never `bool`)
- raise a meaningful exception when it cannot compute a value

The matching scan-config custom source must declare a non-empty `canonical_unit`. The
runtime treats the returned scalar as already expressed in that canonical unit;
custom observables must not perform an undocumented output-unit fallback.

Do not return dictionaries, arrays, tuples, strings, or unit-bearing objects.
Convert the final numeric value to a plain scalar and explicitly reject or
raise on `NaN` and positive/negative infinity.

## Supported Signature Patterns

Parameter-only fallback:

```python
def observable_name(*, M_Hpp: float, v_Delta: float) -> float:
    return float(M_Hpp * v_Delta)
```

Derived observable with task access:

```python
def observable_name(*, task_outputs, M_Hpp: float, v_Delta: float) -> float:
    amp = task_outputs["task-001"](M_Hpp=M_Hpp, v_Delta=v_Delta)
    return float(abs(amp) ** 2)
```

Stochastic observable with the declared local RNG:

```python
def observable_name(*, rng, M_Hpp: float) -> float:
    return float(rng.normal(loc=M_Hpp, scale=1.0))
```

For a `task_outputs` consumer, the matching custom source must list the exact
non-empty `task_ids`. The immutable mapping exposes only those verified task
callables; undeclared or missing tasks are not available. Each wrapper accepts
only the task's declared canonical parameters and rejects non-finite results.

The runtime inspects the signature and passes only declared keyword arguments.
Extra model parameters do not need to appear in the signature.

## Minimal Runnable Example

Keep examples compact and import-light.

```python
from __future__ import annotations

import math

def sum_m_nu(*, m_lightest: float, Dm2_21: float, Dm2_31: float) -> float:
    m1 = float(m_lightest)
    m2 = math.sqrt(m1 * m1 + float(Dm2_21))
    m3 = math.sqrt(m1 * m1 + float(Dm2_31))
    return float(m1 + m2 + m3)
```

## Runtime Calling Convention

During validation and scanning, the runtime imports the module, locates active
functions, inspects signatures, passes declared canonical parameter keywords,
injects the exact declared `task_outputs` mapping and/or local `rng` only when
explicitly accepted, and requires the result to be one finite real scalar. A
task-output wrapper rejects undeclared keyword names and non-finite/boolean
arguments instead of silently dropping or coercing them.

Any exception from the function is treated as a real computation problem.
Do not hide invalid physics or invalid domains by returning arbitrary defaults.
At scan time, an exception, boolean, non-scalar, `NaN`, or infinity marks the
point failed and causes the entire scan to exit nonzero before any scan result,
summary, or manifest history is written. The runtime does not publish the
remaining valid subset.

## Pre-Scan Smoke Test

Before a full scan, validation calls active custom functions once on a
representative parameter point.

Representative values come from fixed config values, scan-range midpoints or
geometric means, model defaults or suggested ranges, and the same verified
finite task wrappers used during scanning. Smoke RNG uses a separate substream,
so validation cannot consume or reorder scan streams.

The smoke test checks importability, signature compatibility, and scalar return
behavior.
It is not a physics validation of the numeric value.

## Failure Modes

`NotImplementedError` residual: a generated stub was not filled in, so Step 2
hard fails before scanning.

Missing canonical keyword: the signature used a name not present in
`model-spec.json` or not declared by the runtime point.

LaTeX or Unicode alias: display labels are not valid Python interface names for
this contract; convert them to existing canonical names first.

Safe-eval failure: a `parameter_combination` formula could not be parsed safely;
runtime may use a fallback custom hook, otherwise a manual stub blocks the scan
until implemented.

Math-domain failure: the function reached an invalid formula domain at a valid
scan point; raise clearly or guard intentionally.

Non-finite return: an overflow, invalid branch, or division by zero produced
`NaN`/infinity. Raise with the relevant domain context; do not return the value
or clamp it silently.

Boolean return: Python treats booleans as integers, but this contract rejects
them as scientific scalar evidence.

Task-output failure: the function requested a task id not available in the
active project or config.

Ambient RNG failure: the backend imports or accesses process-global
Python/NumPy randomness, entropy APIs, or dynamic imports. Accept `rng` and use
only that local generator instead.

## Prohibited Behavior

Do not:

- mutate files on disk
- import and run the scan pipeline
- depend on global mutable caches for scientific results
- seed or call process-global RNG/entropy APIs
- print from hot-path functions at every grid point
- rename public functions without updating all configs
- return placeholder constants to bypass validation

## Reviewer Checklist

- [ ] Every active custom function exists.
- [ ] No active function raises `NotImplementedError`.
- [ ] Parameter keyword names are canonical names.
- [ ] No LaTeX or Unicode aliases appear in signatures.
- [ ] Return values are real finite numeric scalars and are not booleans.
- [ ] Derived functions use `task_outputs` only when declared.
- [ ] `source.task_ids` exactly declares every exposed task output.
- [ ] Stochastic functions accept only the injected `rng`; ambient randomness
      is absent.
- [ ] Safe-eval fallback hooks are implemented before scanning.
- [ ] Exceptions explain the real failed condition.
- [ ] Domain/overflow failures cannot degrade into a partial published scan.
