from __future__ import annotations

import json

from scripts._calculation_provenance import derivation_artifact_errors
from tests.unit.compare_reference_fixtures import hash_file, make_compare_project


def _inputs(project_dir):
    tasks = json.loads(
        (project_dir / "model" / "calc-tasks.json").read_text(encoding="utf-8")
    )
    task = next(item for item in tasks["tasks"] if item["task_id"] == "task-001")
    task_dir = project_dir / "calculations" / "task-001"
    meta = json.loads((task_dir / "result-meta.json").read_text(encoding="utf-8"))
    return task_dir, task, meta


def test_valid_static_derivation_artifacts_are_structurally_verified(tmp_path) -> None:
    project_dir = make_compare_project(tmp_path)
    task_dir, task, meta = _inputs(project_dir)

    assert not derivation_artifact_errors(task_dir, "task-001", task, meta)


def test_constant_python_return_is_not_derivation_dataflow(tmp_path) -> None:
    project_dir = make_compare_project(tmp_path)
    task_dir, task, meta = _inputs(project_dir)
    python_path = task_dir / meta["python_file"]
    python_path.write_text(
        f"def {meta['python_function']}(**kwargs):\n    return 1.0\n",
        encoding="utf-8",
    )
    meta["derivation_evidence"]["python_file_sha256"] = hash_file(python_path)

    errors = derivation_artifact_errors(task_dir, "task-001", task, meta)

    assert any("return value data-dependent" in error for error in errors)


def test_later_constant_python_assignment_kills_input_dataflow(tmp_path) -> None:
    project_dir = make_compare_project(tmp_path)
    task_dir, task, meta = _inputs(project_dir)
    python_path = task_dir / meta["python_file"]
    function_name = meta["python_function"]
    parameter_name = meta["parameters"][0]["canonical_name"]
    python_path.write_text(
        f"def {function_name}({parameter_name}):\n"
        f"    candidate = {parameter_name}\n"
        "    candidate = 1.0\n"
        "    return candidate\n",
        encoding="utf-8",
    )
    meta["derivation_evidence"]["python_file_sha256"] = hash_file(python_path)

    errors = derivation_artifact_errors(task_dir, "task-001", task, meta)

    assert any("return value data-dependent" in error for error in errors)


def test_dead_or_string_only_package_x_markers_do_not_count(tmp_path) -> None:
    project_dir = make_compare_project(tmp_path)
    task_dir, task, meta = _inputs(project_dir)
    source_path = task_dir / meta["source_wl"]
    source_path.write_text(
        'marker = "LoopIntegrate[k, k]";\n'
        "If[False, derived = LoopIntegrate[k, k]];\n"
        "result = importedFormula;\n",
        encoding="utf-8",
    )
    meta["derivation_evidence"]["source_wl_sha256"] = hash_file(source_path)

    errors = derivation_artifact_errors(task_dir, "task-001", task, meta)

    assert any("no executable call outside comments/strings" in error for error in errors)
    assert any("not data-dependent" in error for error in errors)


def test_later_imported_wolfram_assignment_kills_package_x_dataflow(tmp_path) -> None:
    project_dir = make_compare_project(tmp_path)
    task_dir, task, meta = _inputs(project_dir)
    source_path = task_dir / meta["source_wl"]
    method = meta["package_x_methods"][0]
    source_path.write_text(
        f"candidate = {method}[input];\n"
        "candidate = importedFormula;\n"
        "finalResult = candidate;\n",
        encoding="utf-8",
    )
    meta["derivation_evidence"]["source_wl_sha256"] = hash_file(source_path)
    meta["derivation_evidence"]["wolfram_result_symbol"] = "finalResult"

    errors = derivation_artifact_errors(task_dir, "task-001", task, meta)

    assert any("not data-dependent" in error for error in errors)


def test_package_x_provenance_rejects_benchmark_input(tmp_path) -> None:
    project_dir = make_compare_project(tmp_path)
    task_dir, task, meta = _inputs(project_dir)
    meta["benchmark_used_as_input"] = True

    errors = derivation_artifact_errors(task_dir, "task-001", task, meta)

    assert any("benchmark_used_as_input == false" in error for error in errors)


def test_loop_task_rejects_manual_tree_algebra_provenance(tmp_path) -> None:
    project_dir = make_compare_project(tmp_path)
    task_dir, task, meta = _inputs(project_dir)
    task["type"] = "loop"
    meta["calculation_provenance"] = "manual_tree_algebra"
    meta["package_x_methods"] = []
    meta.pop("derivation_evidence", None)

    errors = derivation_artifact_errors(task_dir, "task-001", task, meta)

    assert any("loop task cannot use manual_tree_algebra" in error for error in errors)


def test_derivation_evidence_hash_and_task_observable_are_exactly_bound(tmp_path) -> None:
    project_dir = make_compare_project(tmp_path)
    task_dir, task, meta = _inputs(project_dir)
    meta["derivation_evidence"]["source_wl_sha256"] = "sha256:" + "0" * 64
    task["target_quantity"] = "different_observable"

    errors = derivation_artifact_errors(task_dir, "task-001", task, meta)

    assert any("source_wl_sha256" in error for error in errors)
    assert any("does not match task target_quantity" in error for error in errors)
