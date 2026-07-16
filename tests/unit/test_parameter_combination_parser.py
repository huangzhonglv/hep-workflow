from __future__ import annotations

import math

import pytest


@pytest.mark.parametrize(
    ("formula", "parameters", "expected"),
    [
        ("a + 2*b", {"a": 1.0, "b": 3.0}, 7.0),
        ("sqrt(x) + abs(y)", {"x": 9.0, "y": -4.0}, 7.0),
        ("sin(theta)**2 + cos(theta)**2", {"theta": 0.73}, 1.0),
        ("mass**2 / 4", {"mass": 6.0}, 9.0),
        ("exp(log(z)) + log10(w)", {"z": 5.0, "w": 100.0}, 7.0),
    ],
)
def test_safe_parameter_combination_expressions_evaluate_correctly(
    run_scan_module,
    formula,
    parameters,
    expected,
) -> None:
    compiled = run_scan_module.compile_parameter_combination(formula)
    result = compiled.evaluate(parameters)

    assert result == pytest.approx(expected)


@pytest.mark.parametrize(
    "formula",
    [
        "__import__('os').system('echo hacked')",
        "x.real",
        "max(x, y)",
    ],
)
def test_unsafe_parameter_combination_expressions_are_rejected(run_scan_module, formula) -> None:
    with pytest.raises((ValueError, SyntaxError)):
        run_scan_module.compile_parameter_combination(formula)


@pytest.mark.parametrize(
    "formula",
    [
        "mass^2 / scale where scale=4",
        "m_eff_bb compare with KamLAND-Zen",
        "sigma(for M_Hpp)",
        "2 TeV",
    ],
)
def test_parameter_combination_requires_a_pure_python_expression(run_scan_module, formula) -> None:
    with pytest.raises((ValueError, SyntaxError)):
        run_scan_module.compile_parameter_combination(formula)


def test_parameter_combination_falls_back_to_custom_observable_when_formula_is_natural_language(
    tmp_path,
    project_copy_factory,
    read_json,
    write_json,
    run_scan_module,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    (project_dir / "numerics" / "custom_observables.py").write_text(
        """
from __future__ import annotations


def m_eff_bb(*, m_lightest: float, **kwargs) -> float:
    return float(m_lightest)
""".strip()
        + "\n",
        encoding="utf-8",
    )

    manifest = read_json(project_dir / "manifest.json")
    scan_config = {
        "analysis_id": "analysis-303",
        "model_name": "Minimal Type II Seesaw (scalar triplet extension)",
        "description": "Parameter-combination fallback test",
        "depends_on": {
            "model_version": manifest["active_model_version"],
            "model_checksum": manifest["artifacts"]["model"]["checksum"],
            "task_ids": [],
        },
        "scan_parameters": [
            {"canonical_name": "M_Hpp", "range": [100.0, 300.0], "grid": 3, "scale": "linear"}
        ],
        "fixed_parameters": [
            {"canonical_name": "m_lightest", "value": 0.01}
        ],
        "observables": [
            {"observable": "m_eff_bb", "source": {"type": "custom", "function": "m_eff_bb", "canonical_unit": "eV"}}
        ],
        "constraints_used": ["c-003"],
        "figures": [],
        "seed": 0,
        "parallelism": 1,
    }
    write_json(project_dir / "numerics" / "scan-configs" / "analysis-303.json", scan_config)

    inputs = run_scan_module.load_inputs(project_dir=project_dir, analysis_id="analysis-303")
    validation = run_scan_module.validate(inputs)
    assert not validation["report"].has_errors

    runtime = run_scan_module.prepare_runtime(inputs, validation["runtime"])
    assert "c-003" in runtime["parameter_combination_backends"]
    assert "c-003" not in runtime["formula_evaluators"]

    point = run_scan_module.evaluate_point(
        {"M_Hpp": 200.0, "m_lightest": 0.01},
        inputs,
        runtime,
    )
    assert point["row"]["m_eff_bb"] == pytest.approx(0.01)
    assert point["row"]["c-003_verdict"] == "allowed"


def test_unparseable_formula_fails_without_runtime_stub_side_effects(
    tmp_path,
    project_copy_factory,
    ensure_task_result,
    read_json,
    write_json,
    run_scan_module,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    ensure_task_result(project_dir)
    custom_path = project_dir / "numerics" / "custom_observables.py"
    custom_path.unlink()

    scan_config_path = (
        project_dir / "numerics" / "scan-configs" / "analysis-001.json"
    )
    scan_config = read_json(scan_config_path)
    scan_config["constraints_used"] = ["c-003"]
    write_json(scan_config_path, scan_config)

    inputs = run_scan_module.load_inputs(
        project_dir=project_dir,
        analysis_id="analysis-001",
    )
    validation = run_scan_module.validate(inputs)
    assert not validation["report"].has_errors

    with pytest.raises(RuntimeError, match="c-003 could not be parsed safely"):
        run_scan_module.prepare_runtime(inputs, validation["runtime"])

    assert not custom_path.exists()


def test_numerics_contract_parameter_combinations_prepare_without_manual_stubs(
    tmp_path,
    project_copy_factory,
    run_scan_module,
) -> None:
    project_dir = project_copy_factory(tmp_path)

    inputs = run_scan_module.load_inputs(project_dir=project_dir, analysis_id="analysis-001")
    validation = run_scan_module.validate(inputs)
    assert not validation["report"].has_errors

    runtime = run_scan_module.prepare_runtime(inputs, validation["runtime"])
    assert "c-005" not in runtime["formula_evaluators"]
    assert "c-006" in runtime["formula_evaluators"]

    scan_config = inputs["scan_config"]
    parameters = {
        entry["canonical_name"]: float(entry["value"])
        for entry in scan_config["fixed_parameters"]
    }
    for entry in scan_config["scan_parameters"]:
        parameters[entry["canonical_name"]] = float(entry["range"][0])

    point = run_scan_module.evaluate_point(parameters, inputs, runtime)
    assert point["row"]["c-005_verdict"] == "allowed"
    assert point["row"]["c-006_verdict"] == "allowed"
    assert point["row"]["c-005_skip_reason"] is None
    assert point["row"]["c-006_skip_reason"] is None


def test_custom_observables_template_is_used_for_new_project_headers(
    tmp_path,
    run_scan_module,
) -> None:
    project_dir = tmp_path / "workspace" / "projects" / "demo-project"
    path = run_scan_module.ensure_custom_observables_file(project_dir)

    text = path.read_text(encoding="utf-8")
    assert "Custom observables for demo-project." in text
    assert "Each function here must:" in text
    assert 'task_outputs: Mapping[str, Callable[..., float]]' in text
