# Canonical Name Convention

This document is the local source of truth for canonical parameter naming rules.

## Rule

All machine-readable JSON interfaces must use a single canonical parameter name.
This rule applies to:

- `model/model-spec.json`
- `model/calc-tasks.json`
- `calculations/task-NNN/result-meta.json`
- `constraints/constraints-data.json`
- Python argument names in `calculations/task-NNN/result-python.py`

## Requirements

- Use ASCII letters, digits, and underscores only.
- Do not use LaTeX commands, Unicode symbols, prime symbols, braces, or spaces.
- A canonical name is project-global and immutable once introduced in `model-spec.json`.

Recommended regex:

```text
^[A-Za-z0-9_]+$
```

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
