from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator


TEMPLATE_SCHEMA_BY_NAME = {
    "manifest.example.json": "manifest.schema.json",
    "model-spec.example.json": "model-spec.schema.json",
    "calc-tasks.example.json": "calc-tasks.schema.json",
    "benchmarks.example.json": "benchmarks.schema.json",
    "constraints-data.example.json": "constraints-data.schema.json",
    "scan-config.example.json": "scan-config.schema.json",
    "paper-meta.example.json": "paper-meta.schema.json",
    "paper-extract.example.json": "paper-extract.schema.json",
    "repro-targets.example.json": "repro-targets.schema.json",
}

CANONICAL_TEMPLATE_COPIES = {
    "hep-numerics/templates/scan-config.example.json": "scan-config.example.json",
    "hep-paper-formalize/templates/paper-meta.example.json": "paper-meta.example.json",
    "hep-paper-formalize/templates/paper-extract.example.json": (
        "paper-extract.example.json"
    ),
    "hep-paper-formalize/templates/repro-targets.example.json": (
        "repro-targets.example.json"
    ),
}


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def format_error_path(error) -> str:
    return ".".join(str(part) for part in error.absolute_path) or "<root>"


def test_all_skill_json_templates_validate_against_their_schemas(
    repo_root: Path,
) -> None:
    skills_root = repo_root / ".claude" / "skills"
    template_paths = sorted(skills_root.glob("*/templates/*.example.json"))
    assert template_paths, "no skill JSON templates found"

    unknown_templates = [
        path.relative_to(skills_root).as_posix()
        for path in template_paths
        if path.name not in TEMPLATE_SCHEMA_BY_NAME
    ]
    assert not unknown_templates, f"templates without schema mapping: {unknown_templates}"

    for template_path in template_paths:
        schema_name = TEMPLATE_SCHEMA_BY_NAME[template_path.name]
        schema = load_json(repo_root / "schemas" / schema_name)
        Draft202012Validator.check_schema(schema)
        errors = sorted(
            Draft202012Validator(schema).iter_errors(load_json(template_path)),
            key=lambda error: list(error.absolute_path),
        )
        details = "; ".join(
            f"{format_error_path(error)}: {error.message}" for error in errors
        )
        relative_path = template_path.relative_to(repo_root).as_posix()
        assert not errors, f"{relative_path} fails {schema_name}: {details}"


def test_canonical_skill_templates_are_byte_identical_to_schema_examples(
    repo_root: Path,
) -> None:
    skills_root = repo_root / ".claude" / "skills"
    examples_root = repo_root / "schemas" / "examples"

    for template_relative_path, example_name in CANONICAL_TEMPLATE_COPIES.items():
        template_path = skills_root / template_relative_path
        example_path = examples_root / example_name
        assert template_path.read_bytes() == example_path.read_bytes(), (
            f"{template_relative_path} drifted from schemas/examples/{example_name}"
        )


def test_repro_target_example_normalizes_every_runtime_comparison_column(
    repo_root: Path,
) -> None:
    payload = load_json(
        repo_root / "schemas" / "examples" / "repro-targets.example.json"
    )
    for target in payload["targets"]:
        if target["kind"] == "formula":
            continue
        required = {target["x_param"], target["y_param"]}
        if target["kind"] == "parametric_curve":
            required.add(target["curve_parameter"])
        if target["kind"] in {"benchmark_point", "keyed_benchmark_set", "scan_table"}:
            required.update(target.get("match_columns", []))
            required.update(target["observables"])
        boundary = target.get("boundary", {})
        if boundary.get("mode") == "observable_threshold":
            required.add(boundary["observable"])

        normalization = target["normalization"]
        for field in ("source_units", "canonical_units", "conversions"):
            missing = required - set(normalization[field])
            assert not missing, f"{target['id']} {field} misses {sorted(missing)}"
