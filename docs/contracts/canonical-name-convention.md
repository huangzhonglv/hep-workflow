# Canonical Name Convention

This document is the local source of truth for canonical machine-identifier
rules.

## Rule

All machine-readable interfaces must use one exact canonical name for each
parameter, field, observable, generated Python function, and quantitative data
column. This rule applies to at least:

- `model/model-spec.json`
- `model/calc-tasks.json`
- `calculations/task-NNN/result-meta.json`
- `constraints/constraints-data.json`
- `model/benchmarks.json`
- `literature/paper-extract.json` and `literature/repro-targets.json`
- `numerics/scan-configs/analysis-NNN.json` and normalization records
- Python argument names in `calculations/task-NNN/result-python.py`

## Requirements

- Begin with an ASCII letter or underscore; subsequent characters may be ASCII
  letters, digits, or underscores.
- Do not use a Python hard keyword such as `class`, `for`, `None`, or `yield`.
  Contextual/soft keywords are not prohibited solely because they are soft
  keywords; the supported Python runtime must still accept the identifier in a
  function signature.
- Do not use LaTeX commands, Unicode symbols, prime symbols, braces, or spaces.
- A canonical name is project-global and immutable once introduced in `model-spec.json`.

Recommended regex:

```text
^[A-Za-z_][A-Za-z0-9_]*$
```

The regex is necessary but not sufficient: schema and runtime validation must
also reject every Python hard keyword. Workflow identifiers such as
`task-001`, `analysis-001`, `run-001`, and `c-001` are not canonical names;
they follow their own ASCII-digit patterns.

The keyword denylist is the explicit set of hard keywords shared by the
supported Python 3.11, 3.12, and 3.13 runtimes. It must not be derived from
soft-keyword APIs or silently change with the interpreter running a validator.
If a future supported Python version introduces a new hard keyword, update the
shared identity helper, every affected schema, examples, migration guidance,
and tests in one compatibility change before adding that runtime to CI.

## Dual Naming

Each parameter in `model-spec.json` should define both:

- `name`: canonical machine-readable name, for example `M_Zp`
- `latex`: display name, for example `M_{Z'}`

## Incorrect Example

```text
model-spec.json:    M_Zp
calc-tasks.json:    M_Zprime
constraints-data.json: M_Z'
result-python.py:   m_zp
```

This breaks automatic matching in `hep-numerics`.

## Correct Example

```text
model-spec.json:    "name": "M_Zp", "latex": "M_{Z'}"
calc-tasks.json:    "mass": "M_Zp"
constraints-data.json: "parameters": ["M_Zp", "g_prime"]
result-meta.json:   "canonical_name": "M_Zp"
result-python.py:   def delta_a_mu(m_mu, M_Zp, g_prime):
```

## Enforcement Guidance

- `hep-idea` is the source of truth for canonical names, including later
  model/constraint revisions (see `.claude/skills/hep-idea/SKILL.md` Branch II /
  III).
- Downstream skills must reuse the exact same strings.
- `hep-orchestrator` should reject outputs that introduce names missing from
  `model-spec.json`.
- Validation must reject an invalid identifier rather than sanitizing it,
  prefixing it, changing case, or silently installing an alias. Such transforms
  can collide and break exact cross-file identity.

## Explicit Legacy Migration

Previously accepted projects may contain a leading-digit name or Python hard
keyword. Current tooling must fail closed on those names; it does not migrate
them automatically.

An active legacy project may be migrated only as an explicit model revision:

1. Choose a one-to-one, collision-free old-name to new-name mapping.
2. Create a new `model-spec.json` model version. Do not relabel the existing
   version in place.
3. Record the mapping, the prior model version and SHA-256 checksum, the new
   model version, and the migration reason in the `model_updated` manifest
   history note (or a future schema-governed migration artifact).
4. Mark dependent calculations, constraints, scan configurations, and results
   stale and regenerate them under the new model version. Do not edit old scan
   snapshots to make them appear native to the new names.
5. Preserve completed `reproduction/runs/<repro-id>/` byte-for-byte. Their
   recorded dependency checksums remain the provenance link to the legacy
   inputs.

A migration is incomplete if any downstream artifact mixes old and new names,
if two old names map to one new name, or if its history record omits the exact
mapping or prior checksum needed to audit the claim. Compatibility never
justifies silently weakening the identifier rule.
