# Strict JSON Trust-Boundary Contract

Project-level rule: every repository-controlled JSON reader at a schema,
workflow, fixture, manifest, calculation, numerics, or reproduction trust
boundary must reject ambiguous or non-standard input before any authoritative
artifact or state transition is written.

## Required behavior

- Decode as UTF-8 and report invalid byte sequences as an input error.
- Reject duplicate object keys at every nesting depth, including keys whose
  escaped spellings decode to the same string.
- Reject the non-standard constants `NaN`, `Infinity`, and `-Infinity`.
- Recursively reject any decoded number that is not finite, including a legal
  JSON exponent such as `1e400` that overflows the runtime float type.
- Preserve ordinary finite JSON values without coercing booleans into numbers.
- Return a controlled non-zero CLI result on failure. A parser error must not
  publish output, mutate manifest history, or occupy an immutable run ID.

## Shared implementation

`scripts/_strict_json.py` is the canonical implementation. The hep-numerics
skill vendors byte-identical standalone copies under both skill installation
trees. `scripts/sync_skill_mirrors.py --check` enforces all three copies.

## Verification

Run:

```bash
.venv/bin/python scripts/sync_skill_mirrors.py --check
.venv/bin/python -m pytest -q tests/unit/test_strict_json.py \
  tests/contract/test_strict_json_helper_mirrors.py
```
