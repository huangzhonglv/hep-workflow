from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from scripts._identity import (
    PYTHON_KEYWORDS,
    figure_output_key,
    resolve_contained,
    validate_analysis_id,
    validate_canonical_identifier,
    validate_constraint_id,
    validate_figure_output_keys,
    validate_named_json_path,
    validate_repro_id,
    validate_task_id,
)


CANONICAL_SCHEMA_NAMES = (
    "model-spec.schema.json",
    "calc-tasks.schema.json",
    "constraints-data.schema.json",
    "benchmarks.schema.json",
    "result-meta.schema.json",
    "scan-config.schema.json",
    "paper-extract.schema.json",
    "repro-targets.schema.json",
    "normalization-record.schema.json",
)


@pytest.mark.parametrize("value", ["_", "_x", "A", "A1", "M_Zp", "match"])
def test_canonical_identifier_accepts_python_compatible_names(value: str) -> None:
    assert validate_canonical_identifier(value) == value


@pytest.mark.parametrize(
    "value",
    [
        "",
        "1x",
        "class",
        "False",
        "M-Zp",
        "M Zp",
        "M_{Zp}",
        "λ",
        None,
        1,
    ],
)
def test_canonical_identifier_rejects_invalid_or_reserved_names(value: object) -> None:
    with pytest.raises(ValueError):
        validate_canonical_identifier(value, "test identifier")


def test_hard_keyword_set_is_stable_and_excludes_soft_keywords() -> None:
    assert {"False", "None", "True", "class", "for", "yield"} <= PYTHON_KEYWORDS
    assert {"_", "case", "match", "type"}.isdisjoint(PYTHON_KEYWORDS)


def test_scan_1d_uses_one_canonical_output_basename() -> None:
    figure = {
        "kind": "scan_1d",
        "x": "M_Hpp",
        "observables": ["BR_toy", "delta_a_mu"],
    }

    assert figure_output_key(figure) == "scan1d-M_Hpp-BR_toy--delta_a_mu"
    assert validate_figure_output_keys({"figures": [figure]}) == [
        "scan1d-M_Hpp-BR_toy--delta_a_mu"
    ]
    assert not figure_output_key(figure).startswith("scan-")


@pytest.mark.parametrize(
    ("validator", "valid"),
    [
        (validate_analysis_id, "analysis-001"),
        (validate_repro_id, "run-999"),
        (validate_task_id, "task-000"),
        (validate_constraint_id, "c-123"),
    ],
)
def test_workflow_ids_use_exact_ascii_patterns(validator, valid: str) -> None:
    assert validator(valid) == valid
    prefix = valid.rsplit("-", 1)[0]
    for invalid in (
        f"{prefix}-12",
        f"{prefix}-1234",
        f"{prefix}-٠٠١",
        f"../{valid}",
        f"{valid}/child",
        f"{valid}\\child",
    ):
        with pytest.raises(ValueError):
            validator(invalid)


def test_resolve_contained_accepts_only_normal_relative_paths(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    expected = root / "nested" / "artifact.json"

    assert resolve_contained(root, "nested/artifact.json", "artifact") == expected

    for invalid in (
        "",
        ".",
        "..",
        "./artifact.json",
        "nested/../artifact.json",
        "nested//artifact.json",
        "nested\\artifact.json",
        str(expected),
    ):
        with pytest.raises(ValueError):
            resolve_contained(root, invalid, "artifact")


def test_resolve_contained_rejects_symlink_components_and_escapes(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    inside = root / "inside"
    outside = tmp_path / "outside"
    inside.mkdir(parents=True)
    outside.mkdir()
    (inside / "artifact.json").write_text("{}\n", encoding="utf-8")
    (outside / "artifact.json").write_text("{}\n", encoding="utf-8")
    try:
        (root / "inside-link").symlink_to(inside, target_is_directory=True)
        (root / "outside-link").symlink_to(outside, target_is_directory=True)
    except OSError as exc:  # pragma: no cover - platform/permission dependent
        pytest.skip(f"symlinks unavailable: {exc}")

    with pytest.raises(ValueError, match="symlink"):
        resolve_contained(root, "inside-link/artifact.json", "artifact")
    with pytest.raises(ValueError, match="escapes"):
        resolve_contained(
            root,
            "outside-link/artifact.json",
            "artifact",
            reject_symlinks=False,
        )


def test_resolve_contained_supports_a_not_yet_created_root(tmp_path: Path) -> None:
    root = tmp_path / "new" / "root"
    assert resolve_contained(root, "artifact.json", "artifact") == (
        root / "artifact.json"
    ).resolve(strict=False)


def test_named_json_path_binds_relative_or_absolute_path_to_identifier(
    tmp_path: Path,
) -> None:
    root = tmp_path / "scan-configs"
    root.mkdir()
    expected = root / "analysis-001.json"

    assert (
        validate_named_json_path(
            "analysis-001.json", root, "analysis-001", "scan config"
        )
        == expected
    )
    assert (
        validate_named_json_path(expected, root, "analysis-001", "scan config")
        == expected
    )

    for path, identifier in (
        ("analysis-002.json", "analysis-001"),
        ("nested/analysis-001.json", "analysis-001"),
        (tmp_path / "analysis-001.json", "analysis-001"),
        ("analysis-001.json", "../analysis-001"),
        ("analysis-1000.json", "analysis-1000"),
        ("not-an-analysis.json", "not-an-analysis"),
    ):
        with pytest.raises(ValueError):
            validate_named_json_path(path, root, identifier, "scan config")


def test_named_json_path_rejects_symlinked_file(tmp_path: Path) -> None:
    root = tmp_path / "scan-configs"
    outside = tmp_path / "outside.json"
    root.mkdir()
    outside.write_text("{}\n", encoding="utf-8")
    link = root / "analysis-001.json"
    try:
        link.symlink_to(outside)
    except OSError as exc:  # pragma: no cover - platform/permission dependent
        pytest.skip(f"symlinks unavailable: {exc}")

    with pytest.raises(ValueError):
        validate_named_json_path(link, root, "analysis-001", "scan config")


@pytest.mark.parametrize("schema_name", CANONICAL_SCHEMA_NAMES)
def test_schema_canonical_primitive_matches_runtime_rule(
    repo_root: Path,
    schema_name: str,
) -> None:
    schema = json.loads((repo_root / "schemas" / schema_name).read_text(encoding="utf-8"))
    canonical = schema["$defs"]["canonical_name"]
    assert canonical["pattern"] == "^[A-Za-z_][A-Za-z0-9_]*$"
    assert frozenset(canonical["not"]["enum"]) == PYTHON_KEYWORDS

    validator = Draft202012Validator(canonical)
    for valid in ("_", "_x", "A1", "match"):
        assert not list(validator.iter_errors(valid)), (schema_name, valid)
    for invalid in ("1x", "class", "False", "M-Zp", "λ"):
        assert list(validator.iter_errors(invalid)), (schema_name, invalid)


def _schema_and_example(repo_root: Path, base: str) -> tuple[dict, dict]:
    schema = json.loads(
        (repo_root / "schemas" / f"{base}.schema.json").read_text(encoding="utf-8")
    )
    example = json.loads(
        (repo_root / "schemas" / "examples" / f"{base}.example.json").read_text(
            encoding="utf-8"
        )
    )
    return schema, example


@pytest.mark.parametrize(
    ("base", "path"),
    [
        ("model-spec", ("parameters", 0, "name")),
        ("calc-tasks", ("tasks", 0, "target_quantity")),
        ("constraints-data", ("constraints", 0, "observable")),
        ("benchmarks", ("benchmarks", 0, "observable")),
        ("result-meta", ("observable",)),
        ("result-meta", ("python_function",)),
        ("result-meta", ("return_value", "name")),
        ("scan-config", ("observables", 0, "observable")),
        ("paper-extract", ("observables", 0, "name")),
        ("repro-targets", ("targets", 0, "observables", 0)),
    ],
)
def test_machine_identifier_fields_reject_python_keywords(
    repo_root: Path,
    base: str,
    path: tuple[object, ...],
) -> None:
    schema, example = _schema_and_example(repo_root, base)
    candidate = copy.deepcopy(example)
    node = candidate
    for part in path[:-1]:
        node = node[part]
    node[path[-1]] = "class"

    assert list(Draft202012Validator(schema).iter_errors(candidate)), (base, path)


def test_benchmark_input_and_normalization_column_names_are_canonical(
    repo_root: Path,
) -> None:
    benchmark_schema, benchmark = _schema_and_example(repo_root, "benchmarks")
    inputs = benchmark["benchmarks"][0]["numerical_test_point"]["inputs"]
    first_input = next(iter(inputs))
    inputs["class"] = inputs.pop(first_input)
    assert list(Draft202012Validator(benchmark_schema).iter_errors(benchmark))

    normalization_schema, normalization = _schema_and_example(
        repo_root, "normalization-record"
    )
    first_column = next(iter(normalization["source_units"]))
    normalization["source_units"]["class"] = normalization["source_units"].pop(
        first_column
    )
    assert list(
        Draft202012Validator(normalization_schema).iter_errors(normalization)
    )


@pytest.mark.parametrize(
    ("base", "path", "value"),
    [
        ("scan-config", ("analysis_id",), "analysis-٠٠١"),
        ("calc-tasks", ("tasks", 0, "task_id"), "task-٠٠١"),
        ("constraints-data", ("constraints", 0, "id"), "c-٠٠١"),
        ("benchmarks", ("benchmarks", 0, "task_id"), "task-٠٠١"),
        ("result-meta", ("task_id",), "task-٠٠١"),
    ],
)
def test_schema_workflow_ids_reject_unicode_digits(
    repo_root: Path,
    base: str,
    path: tuple[object, ...],
    value: str,
) -> None:
    schema, example = _schema_and_example(repo_root, base)
    candidate = copy.deepcopy(example)
    node = candidate
    for part in path[:-1]:
        node = node[part]
    node[path[-1]] = value
    assert list(Draft202012Validator(schema).iter_errors(candidate)), (base, path)
