#!/usr/bin/env python3
"""Validate example JSON files against the repository schemas."""

from __future__ import annotations

import json
import sys
from pathlib import Path


SCHEMA_TO_EXAMPLE = {
    "manifest.schema.json": "manifest.example.json",
    "model-spec.schema.json": "model-spec.example.json",
    "calc-tasks.schema.json": "calc-tasks.example.json",
    "benchmarks.schema.json": "benchmarks.example.json",
    "result-meta.schema.json": "result-meta.example.json",
    "paper-meta.schema.json": "paper-meta.example.json",
    "repro-targets.schema.json": "repro-targets.example.json",
    "reproduction-result.schema.json": "reproduction-result.example.json",
    "constraints-data.schema.json": "constraints-data.example.json",
    "scan-config.schema.json": "scan-config.example.json",
    "scan-meta.schema.json": "scan-meta.example.json",
}


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    try:
        from jsonschema import Draft202012Validator
    except ImportError:
        print(
            "error: jsonschema is not installed in the active Python environment.\n"
            "Create and activate a virtual environment, then install the dev requirements:\n"
            "  python3 -m venv .venv\n"
            "  source .venv/bin/activate\n"
            "  python3 -m pip install -r requirements-dev.txt",
            file=sys.stderr,
        )
        return 1

    repo_root = Path(__file__).resolve().parent.parent
    schemas_dir = repo_root / "schemas"
    examples_dir = schemas_dir / "examples"

    failures = 0
    for schema_name, example_name in SCHEMA_TO_EXAMPLE.items():
        schema_path = schemas_dir / schema_name
        example_path = examples_dir / example_name
        schema = load_json(schema_path)
        example = load_json(example_path)
        validator = Draft202012Validator(schema)
        errors = sorted(validator.iter_errors(example), key=lambda err: list(err.absolute_path))

        if errors:
            failures += 1
            print(f"FAIL {schema_name} <- {example_name}")
            for err in errors:
                path = ".".join(str(part) for part in err.absolute_path) or "<root>"
                print(f"  - {path}: {err.message}")
        else:
            print(f"OK   {schema_name} <- {example_name}")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
