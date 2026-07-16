from __future__ import annotations

from copy import deepcopy
import json

from jsonschema import Draft202012Validator


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_only_package_x_results_may_declare_derivation_evidence(repo_root) -> None:
    schema = _load(repo_root / "schemas" / "result-meta.schema.json")
    example = _load(repo_root / "schemas" / "examples" / "result-meta.example.json")
    validator = Draft202012Validator(schema)
    assert not list(validator.iter_errors(example))

    manual = deepcopy(example)
    manual["calculation_provenance"] = "manual_tree_algebra"
    manual["package_x_methods"] = []

    evidence_errors = list(validator.iter_errors(manual))

    assert evidence_errors
    assert any("should not be valid" in error.message for error in evidence_errors)

    misleading_methods = deepcopy(manual)
    misleading_methods.pop("derivation_evidence")
    misleading_methods["package_x_methods"] = ["LoopIntegrate"]
    method_errors = list(validator.iter_errors(misleading_methods))

    assert method_errors
    assert any(list(error.absolute_path) == ["package_x_methods"] for error in method_errors)


def test_model_and_result_units_cannot_be_blank(repo_root) -> None:
    model_schema = _load(repo_root / "schemas" / "model-spec.schema.json")
    model = _load(repo_root / "schemas" / "examples" / "model-spec.example.json")
    model["parameters"][0]["unit"] = "   "
    assert list(Draft202012Validator(model_schema).iter_errors(model))

    result_schema = _load(repo_root / "schemas" / "result-meta.schema.json")
    result = _load(repo_root / "schemas" / "examples" / "result-meta.example.json")
    result["return_value"]["unit"] = "   "
    assert list(Draft202012Validator(result_schema).iter_errors(result))
