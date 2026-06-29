from __future__ import annotations

import copy
import json
from pathlib import Path

from jsonschema import Draft202012Validator


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_scan_config_schema_is_valid_json_schema(repo_root: Path) -> None:
    schema = load_json(repo_root / "schemas" / "scan-config.schema.json")
    Draft202012Validator.check_schema(schema)


def test_scan_config_example_passes_schema_validation(repo_root: Path) -> None:
    schema = load_json(repo_root / "schemas" / "scan-config.schema.json")
    example = load_json(repo_root / "schemas" / "examples" / "scan-config.example.json")

    validator = Draft202012Validator(schema)
    errors = list(validator.iter_errors(example))

    assert errors == []


def test_invalid_analysis_id_is_rejected(repo_root: Path) -> None:
    schema = load_json(repo_root / "schemas" / "scan-config.schema.json")
    example = load_json(repo_root / "schemas" / "examples" / "scan-config.example.json")
    candidate = copy.deepcopy(example)
    candidate["analysis_id"] = "scan-001"

    errors = list(Draft202012Validator(schema).iter_errors(candidate))

    assert errors
    assert any(list(error.absolute_path) == ["analysis_id"] for error in errors)


def test_empty_scan_parameters_is_rejected(repo_root: Path) -> None:
    schema = load_json(repo_root / "schemas" / "scan-config.schema.json")
    example = load_json(repo_root / "schemas" / "examples" / "scan-config.example.json")
    candidate = copy.deepcopy(example)
    candidate["scan_parameters"] = []

    errors = list(Draft202012Validator(schema).iter_errors(candidate))

    assert errors
    assert any(list(error.absolute_path) == ["scan_parameters"] for error in errors)


def test_observable_source_without_valid_variant_is_rejected(repo_root: Path) -> None:
    schema = load_json(repo_root / "schemas" / "scan-config.schema.json")
    example = load_json(repo_root / "schemas" / "examples" / "scan-config.example.json")
    candidate = copy.deepcopy(example)
    candidate["observables"][0]["source"] = {}

    errors = list(Draft202012Validator(schema).iter_errors(candidate))

    assert errors
    assert any(list(error.absolute_path) == ["observables", 0, "source"] for error in errors)


def test_unknown_nested_scan_config_properties_are_rejected(repo_root: Path) -> None:
    schema = load_json(repo_root / "schemas" / "scan-config.schema.json")
    example = load_json(repo_root / "schemas" / "examples" / "scan-config.example.json")
    validator = Draft202012Validator(schema)

    cases = [
        (["depends_on"], "unexpected"),
        (["scan_parameters", 0], "unexpected"),
        (["fixed_parameters", 0], "unexpected"),
        (["observables", 0], "unexpected"),
        (["observables", 0, "source"], "unexpected"),
        (["figures", 0], "unexpected"),
    ]

    for path, field in cases:
        candidate = copy.deepcopy(example)
        target = candidate
        for part in path:
            target = target[part]
        target[field] = True

        errors = list(validator.iter_errors(candidate))

        assert errors, f"{path} accepted unknown field {field!r}"
        assert any(list(error.absolute_path) == path for error in errors)
