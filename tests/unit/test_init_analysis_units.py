from __future__ import annotations

import importlib.util
import sys

import pytest


@pytest.fixture
def init_analysis_module(repo_root):
    path = (
        repo_root
        / ".agents"
        / "skills"
        / "hep-numerics"
        / "scripts"
        / "init_analysis.py"
    )
    spec = importlib.util.spec_from_file_location("phase0_init_analysis_units", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_custom_unit_resolves_from_constraint_or_interpolation(init_analysis_module) -> None:
    resolve = init_analysis_module.custom_observable_canonical_unit
    assert resolve({"observable": "x", "unit": "GeV"}) == "GeV"
    assert resolve(
        {
            "observable": "sigma",
            "interpolation": {"y_quantity": "sigma", "y_unit": "fb"},
        }
    ) == "fb"
    assert resolve(
        {
            "observable": "sigma",
            "unit": "fb",
            "interpolation": {"y_quantity": "sigma", "y_unit": "fb"},
        }
    ) == "fb"


@pytest.mark.parametrize(
    "constraint",
    [
        {"observable": "x"},
        {"observable": "x", "unit": "   "},
        {
            "observable": "x",
            "unit": "GeV",
            "interpolation": {"y_quantity": "x", "y_unit": "MeV"},
        },
    ],
)
def test_custom_unit_missing_or_ambiguous_fails_before_generation(
    init_analysis_module, constraint
) -> None:
    with pytest.raises(ValueError, match="exactly one authoritative canonical unit"):
        init_analysis_module.custom_observable_canonical_unit(constraint)
