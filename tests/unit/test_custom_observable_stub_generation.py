from __future__ import annotations


def test_canonical_stub_generator_supports_formula_and_derived_constraints(
    tmp_path,
    run_scan_module,
) -> None:
    helper = run_scan_module.CUSTOM_OBSERVABLES
    project_dir = tmp_path / "workspace" / "projects" / "demo-project"

    path, created = helper.ensure_custom_observables_file(project_dir)
    same_path, created_again = helper.ensure_custom_observables_file(project_dir)

    assert created is True
    assert created_again is False
    assert same_path == path

    formula_constraint = {
        "id": "c-101",
        "name": "Manual formula constraint",
        "computed_by": {
            "type": "parameter_combination",
            "formula": "manual expression involving M_Hpp",
        },
    }
    derived_constraint = {
        "id": "c-102",
        "name": "Task-derived constraint",
        "computed_by": {
            "type": "derived",
            "depends_on_tasks": ["task-001"],
            "derivation_note": "Combine the task amplitude with v_Delta.",
        },
    }

    assert helper.append_custom_observable_stub(
        path,
        function_name="manual_formula_obs",
        parameter_names=["M_Hpp", "v_Delta"],
        constraint=formula_constraint,
        needs_task_outputs=False,
    )
    assert helper.append_custom_observable_stub(
        path,
        function_name="derived_task_obs",
        parameter_names=["M_Hpp", "v_Delta"],
        constraint=derived_constraint,
        needs_task_outputs=True,
    )

    source = path.read_text(encoding="utf-8")
    compile(source, str(path), "exec")
    assert "Custom observables for demo-project." in source
    assert "Auto-generated observable stub for constraint c-101" in source
    assert "Original formula that could not be parsed safely:" in source
    assert "manual expression involving M_Hpp" in source
    assert "Auto-generated observable stub for constraint c-102" in source
    assert "task_outputs: Mapping[str, Callable[..., float]]," in source
    assert "Combine the task amplitude with v_Delta." in source

    before_duplicate = path.read_bytes()
    assert not helper.append_custom_observable_stub(
        path,
        function_name="manual_formula_obs",
        parameter_names=["M_Hpp", "v_Delta"],
        constraint=formula_constraint,
        needs_task_outputs=False,
    )
    assert path.read_bytes() == before_duplicate
