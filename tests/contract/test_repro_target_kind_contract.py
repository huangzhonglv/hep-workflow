from __future__ import annotations

from copy import deepcopy
import json

from jsonschema import Draft202012Validator


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_formula_target_cannot_retain_quantitative_runtime_fields(repo_root) -> None:
    schema = _load(repo_root / "schemas" / "repro-targets.schema.json")
    example = _load(repo_root / "schemas" / "examples" / "repro-targets.example.json")
    validator = Draft202012Validator(schema)
    assert not list(validator.iter_errors(example))

    candidate = deepcopy(example)
    target = next(item for item in candidate["targets"] if item["kind"] == "figure_curve")
    target["kind"] = "formula"
    target["tolerance"] = {"kind": "qualitative", "value": None}

    errors = list(validator.iter_errors(candidate))

    assert errors
    assert any(list(error.absolute_path)[:2] == ["targets", 4] for error in errors)


def test_quantitative_target_kinds_reject_cross_kind_fields(repo_root) -> None:
    schema = _load(repo_root / "schemas" / "repro-targets.schema.json")
    example = _load(repo_root / "schemas" / "examples" / "repro-targets.example.json")
    validator = Draft202012Validator(schema)
    valid_boundary = deepcopy(
        next(
            item for item in example["targets"] if item["kind"] == "exclusion_region"
        )["boundary"]
    )
    cases = {
        "benchmark_point": {"boundary": valid_boundary},
        "keyed_benchmark_set": {"comparison_domain": {"x_min": 0, "x_max": 1}},
        "scan_table": {"coordinate_scales": {"x": 1, "y": 1}},
        "figure_curve": {"match_columns": ["M_Zp"]},
        "parametric_curve": {"comparison_domain": {"x_min": 0, "x_max": 1}},
        "exclusion_region": {"curve_representation": "single_valued_y_of_x"},
    }

    for kind, extra in cases.items():
        candidate = deepcopy(example)
        target = next(item for item in candidate["targets"] if item["kind"] == kind)
        target.update(extra)
        assert list(validator.iter_errors(candidate)), kind


def test_parametric_curve_has_a_disjoint_explicit_geometry_contract(repo_root) -> None:
    schema = _load(repo_root / "schemas" / "repro-targets.schema.json")
    example = _load(repo_root / "schemas" / "examples" / "repro-targets.example.json")
    validator = Draft202012Validator(schema)
    target = next(item for item in example["targets"] if item["kind"] == "parametric_curve")
    assert not list(validator.iter_errors(example))

    for field in (
        "curve_parameter",
        "parameter_domain",
        "curve_representation",
        "curve_closed",
        "coordinate_scales",
    ):
        candidate = deepcopy(example)
        selected = next(
            item for item in candidate["targets"] if item["kind"] == "parametric_curve"
        )
        selected.pop(field)
        assert list(validator.iter_errors(candidate)), field

    wrong_tolerance = deepcopy(example)
    selected = next(
        item for item in wrong_tolerance["targets"] if item["kind"] == "parametric_curve"
    )
    selected["tolerance"] = {"kind": "relative", "value": 0.1}
    assert list(validator.iter_errors(wrong_tolerance))

    implicit_projection = deepcopy(example)
    selected = next(
        item for item in implicit_projection["targets"] if item["kind"] == "parametric_curve"
    )
    selected["projection"] = {"kind": "any"}
    assert list(validator.iter_errors(implicit_projection))
    assert target["curve_representation"] == "ordered_parametric_xy"


def test_exclusion_boundary_uses_exactly_one_legacy_probe_or_face_contract(repo_root) -> None:
    schema = _load(repo_root / "schemas" / "repro-targets.schema.json")
    example = _load(repo_root / "schemas" / "examples" / "repro-targets.example.json")
    validator = Draft202012Validator(schema)
    target = next(item for item in example["targets"] if item["kind"] == "exclusion_region")

    missing = deepcopy(example)
    selected = next(
        item for item in missing["targets"] if item["kind"] == "exclusion_region"
    )
    selected["boundary"].pop("reference_excluded_probe")
    assert list(validator.iter_errors(missing))

    both = deepcopy(example)
    selected = next(
        item for item in both["targets"] if item["kind"] == "exclusion_region"
    )
    selected["boundary"]["reference_faces"] = [
        {
            "id": "outer",
            "parent_id": None,
            "closed": True,
            "excluded_side": "interior",
            "excluded_probe": {"x": 100.0, "y": 0.01},
        }
    ]
    assert list(validator.iter_errors(both))

    faces = deepcopy(example)
    selected = next(
        item for item in faces["targets"] if item["kind"] == "exclusion_region"
    )
    probe = selected["boundary"].pop("reference_excluded_probe")
    selected["boundary"]["reference_faces"] = [
        {
            "id": "outer",
            "parent_id": None,
            "closed": True,
            "excluded_side": "interior",
            "excluded_probe": probe,
        }
    ]
    assert not list(validator.iter_errors(faces))

    selected["boundary"]["reference_faces"][0]["closed"] = False
    assert list(validator.iter_errors(faces))
