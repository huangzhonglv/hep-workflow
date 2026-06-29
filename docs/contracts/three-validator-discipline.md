# Three-Validator Discipline

Project-level rule: three validation layers must stay green on `main` at all
times. A red main is treated as an incident, not a normal state.

## The three layers

```bash
python3 scripts/validate_examples.py
python3 scripts/validate_workspace_projects.py
python3 -m pytest -q
```

| Layer | What it checks |
| --- | --- |
| `validate_examples.py` | JSON Schema validity of `schemas/examples/*.json` and consistency between `schemas/*.schema.json` and their canonical examples |
| `validate_workspace_projects.py` | All `workspace/projects/*` are schema-valid, canonical-name compliant, and cross-file consistent |
| `pytest -q` | Unit + contract + integration tests under `tests/` |

End-to-end suite (`tests/e2e/`) is gated by `--run-e2e` / `HEP_E2E=1` and is
not part of the always-green discipline; it is run when changing
`hep-numerics` scripts, manifest schema behavior used by e2e, or the
`smoke-e2e` fixture.

## Discipline

- A PR that breaks any of the three layers does not land.
- A flake in any layer is treated as a real failure until proven otherwise:
  the test must be either fixed or quarantined with an issue reference,
  not silently retried.
- Skipping a test or marking xfail without an issue reference is forbidden.
- Schema or contract changes require running both validators **before**
  committing, even if `pytest` passes.

## Adding a new validator or test layer

If a new layer is added (e.g., a future end-to-end gate), update this file
to list it, decide whether it joins the always-green set or sits behind a
gate, and update `CLAUDE.md` / `AGENTS.md` "Common commands" section if
applicable.
