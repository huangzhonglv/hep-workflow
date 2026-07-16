from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

import pytest


def run_workspace_validator(repo_root: Path, project_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "scripts/validate_workspace_projects.py",
            "--workspace-root",
            str(project_dir.parent),
            project_dir.name,
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )


def _sha256(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _bind_package_x_derivation_evidence(
    task_dir: Path,
    result_meta: dict,
) -> None:
    result_meta["derivation_evidence"] = {
        "source_wl_sha256": _sha256(task_dir / result_meta["source_wl"]),
        "python_file_sha256": _sha256(task_dir / result_meta["python_file"]),
        "wolfram_result_symbol": "finalResult",
        "observable": result_meta["observable"],
        "python_function": result_meta["python_function"],
        "package_x_methods": result_meta["package_x_methods"],
    }


def test_workspace_static_artifacts_valid_project_passes(
    tmp_path,
    project_copy_factory,
    repo_root,
) -> None:
    project_dir = project_copy_factory(tmp_path)

    result = run_workspace_validator(repo_root, project_dir)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "analysis-summary-analysis-001.md" in result.stdout
    assert "custom_observables.py" in result.stdout
    assert "result-python.py" in result.stdout


def test_workspace_validator_validates_smoke_literature_artifacts(
    tmp_path,
    project_copy_factory,
    smoke_e2e_fixture_path,
    repo_root,
) -> None:
    project_dir = project_copy_factory(
        tmp_path,
        project_name="smoke-literature",
        source_project_path=smoke_e2e_fixture_path,
    )

    result = run_workspace_validator(repo_root, project_dir)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "literature/paper-meta.json <- paper-meta.schema.json" in result.stdout
    assert "literature/repro-targets.json <- repro-targets.schema.json" in result.stdout
    assert "literature/paper-extract.json <- paper-extract.schema.json" in result.stdout
    assert "OK   manifest.json literature artifact" in result.stdout


def test_workspace_validator_rejects_semantically_drifted_canonical_reference(
    tmp_path,
    project_copy_factory,
    smoke_e2e_fixture_path,
    read_json,
    write_json,
    repo_root,
) -> None:
    project_dir = project_copy_factory(
        tmp_path,
        project_name="smoke-drifted-canonical-reference",
        source_project_path=smoke_e2e_fixture_path,
    )
    canonical_path = (
        project_dir / "literature" / "digitized" / "target-001.csv"
    )
    canonical_path.write_text(
        canonical_path.read_text(encoding="utf-8").replace(
            "600.0,2.7777777777777775e-16",
            "600.0,2.777777777777778e-16",
        ),
        encoding="utf-8",
    )
    record_path = (
        project_dir
        / "literature"
        / "digitized"
        / "target-001.normalization.json"
    )
    record = read_json(record_path)
    record["canonical_checksum"] = _sha256(canonical_path)
    write_json(record_path, record)

    result = run_workspace_validator(repo_root, project_dir)

    assert result.returncode != 0
    combined_output = result.stdout + result.stderr
    assert "literature reference evidence 'target-001'" in combined_output
    assert "identity normalization changed tabular values" in combined_output


def test_workspace_validator_rejects_invalid_paper_meta(
    tmp_path,
    project_copy_factory,
    smoke_e2e_fixture_path,
    read_json,
    write_json,
    repo_root,
) -> None:
    project_dir = project_copy_factory(
        tmp_path,
        project_name="smoke-invalid-paper-meta",
        source_project_path=smoke_e2e_fixture_path,
    )
    paper_meta_path = project_dir / "literature" / "paper-meta.json"
    paper_meta = read_json(paper_meta_path)
    paper_meta.pop("title")
    write_json(paper_meta_path, paper_meta)

    result = run_workspace_validator(repo_root, project_dir)

    assert result.returncode != 0
    combined_output = result.stdout + result.stderr
    assert "literature/paper-meta.json <- paper-meta.schema.json" in combined_output
    assert "title" in combined_output


def test_workspace_validator_rejects_invalid_paper_extract_json(
    tmp_path,
    project_copy_factory,
    smoke_e2e_fixture_path,
    repo_root,
) -> None:
    project_dir = project_copy_factory(
        tmp_path,
        project_name="smoke-invalid-paper-extract",
        source_project_path=smoke_e2e_fixture_path,
    )
    (project_dir / "literature" / "paper-extract.json").write_text(
        "{not-json\n",
        encoding="utf-8",
    )

    result = run_workspace_validator(repo_root, project_dir)

    assert result.returncode != 0
    combined_output = result.stdout + result.stderr
    assert "literature/paper-extract.json: invalid JSON" in combined_output


def test_workspace_validator_rejects_invalid_paper_extract_shape(
    tmp_path,
    project_copy_factory,
    smoke_e2e_fixture_path,
    read_json,
    write_json,
    repo_root,
) -> None:
    project_dir = project_copy_factory(
        tmp_path,
        project_name="smoke-invalid-paper-extract-shape",
        source_project_path=smoke_e2e_fixture_path,
    )
    paper_extract_path = project_dir / "literature" / "paper-extract.json"
    paper_extract = read_json(paper_extract_path)
    paper_extract["formulas"][0]["human_review_required"] = False
    write_json(paper_extract_path, paper_extract)

    result = run_workspace_validator(repo_root, project_dir)

    assert result.returncode != 0
    combined_output = result.stdout + result.stderr
    assert (
        "literature/paper-extract.json <- paper-extract.schema.json"
        in combined_output
    )
    assert "human_review_required" in combined_output


def test_workspace_validator_rejects_invalid_reproduction_result(
    tmp_path,
    project_copy_factory,
    read_json,
    write_json,
    repo_root,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    reproduction_result = read_json(
        repo_root / "schemas" / "examples" / "reproduction-result.example.json"
    )
    reproduction_result.pop("results")
    write_json(
        project_dir
        / "reproduction"
        / "runs"
        / "run-001"
        / "reproduction-result.json",
        reproduction_result,
    )

    result = run_workspace_validator(repo_root, project_dir)

    assert result.returncode != 0
    combined_output = result.stdout + result.stderr
    assert (
        "reproduction-result.json <- reproduction-result.schema.json"
        in combined_output
    )
    assert "results" in combined_output


def test_workspace_validator_rejects_semantically_inconsistent_reproduction_result(
    tmp_path,
    project_copy_factory,
    read_json,
    write_json,
    repo_root,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    reproduction_result = read_json(
        repo_root / "schemas" / "examples" / "reproduction-result.example.json"
    )
    reproduction_result["run_summary"]["n_targets_total"] = 99
    for target_result in reproduction_result["results"]:
        for pair in target_result["generated_files"].values():
            for relpath in pair.values():
                path = project_dir / relpath
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"synthetic figure evidence")
    diagnostic = project_dir / reproduction_result["diagnostic_file"]
    diagnostic.parent.mkdir(parents=True, exist_ok=True)
    diagnostic.write_text("diagnostic\n", encoding="utf-8")
    write_json(
        project_dir
        / "reproduction"
        / "runs"
        / "run-001"
        / "reproduction-result.json",
        reproduction_result,
    )

    result = run_workspace_validator(repo_root, project_dir)

    assert result.returncode != 0
    combined_output = result.stdout + result.stderr
    assert "semantic reproduction-result validation" in combined_output
    assert "n_targets_total" in combined_output


def test_workspace_validator_rejects_empty_analysis_summary(
    tmp_path,
    project_copy_factory,
    repo_root,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    summary_path = project_dir / "numerics" / "analysis-summary-analysis-001.md"
    summary_path.write_text("   \n", encoding="utf-8")

    result = run_workspace_validator(repo_root, project_dir)

    assert result.returncode != 0
    combined_output = result.stdout + result.stderr
    assert "empty analysis-summary" in combined_output


def test_workspace_validator_rejects_invalid_custom_observables_syntax(
    tmp_path,
    project_copy_factory,
    repo_root,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    custom_path = project_dir / "numerics" / "custom_observables.py"
    custom_path.write_text("def broken(:\n    return 1\n", encoding="utf-8")

    result = run_workspace_validator(repo_root, project_dir)

    assert result.returncode != 0
    combined_output = result.stdout + result.stderr
    assert "custom_observables" in combined_output


def test_workspace_validator_rejects_python_function_mismatch(
    tmp_path,
    project_copy_factory,
    read_json,
    write_json,
    repo_root,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    result_meta_path = project_dir / "calculations" / "task-001" / "result-meta.json"
    result_meta = read_json(result_meta_path)
    result_meta["python_function"] = "missing_backend_function"
    write_json(result_meta_path, result_meta)

    result = run_workspace_validator(repo_root, project_dir)

    assert result.returncode != 0
    combined_output = result.stdout + result.stderr
    assert "python_function" in combined_output
    assert "missing_backend_function" in combined_output


def test_workspace_validator_rejects_metadata_parameter_omission(
    tmp_path,
    project_copy_factory,
    read_json,
    write_json,
    rebind_scan_result,
    repo_root,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    result_meta_path = project_dir / "calculations" / "task-001" / "result-meta.json"
    result_meta = read_json(result_meta_path)
    result_meta["parameters"] = [
        parameter
        for parameter in result_meta["parameters"]
        if parameter["canonical_name"] != "v_Delta"
    ]
    write_json(result_meta_path, result_meta)
    rebind_scan_result(project_dir)

    result = run_workspace_validator(repo_root, project_dir)

    assert result.returncode != 0
    assert "parameters must exactly match python_function" in result.stdout + result.stderr


def test_workspace_validator_rejects_undeclared_kwargs_channel(
    tmp_path,
    project_copy_factory,
    read_json,
    rebind_calculation_result,
    rebind_scan_result,
    repo_root,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    result_meta = read_json(
        project_dir / "calculations" / "task-001" / "result-meta.json"
    )
    python_path = project_dir / "calculations" / "task-001" / result_meta["python_file"]
    source = python_path.read_text(encoding="utf-8")
    mutated_source = source.replace(
        "v_Delta: float = 1.0e-3) -> float:",
        "v_Delta: float = 1.0e-3, **kwargs) -> float:",
    )
    assert mutated_source != source
    source = mutated_source
    python_path.write_text(source, encoding="utf-8")
    rebind_calculation_result(project_dir)
    rebind_scan_result(project_dir)

    result = run_workspace_validator(repo_root, project_dir)

    assert result.returncode != 0
    assert "must not accept **kwargs" in result.stdout + result.stderr


def test_workspace_validator_rejects_decorated_python_function(
    tmp_path,
    project_copy_factory,
    read_json,
    rebind_calculation_result,
    rebind_scan_result,
    repo_root,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    result_meta = read_json(
        project_dir / "calculations" / "task-001" / "result-meta.json"
    )
    python_path = project_dir / "calculations" / "task-001" / result_meta["python_file"]
    source = python_path.read_text(encoding="utf-8")
    marker = f"def {result_meta['python_function']}("
    mutated_source = source.replace(marker, f"@staticmethod\n{marker}")
    assert mutated_source != source
    python_path.write_text(mutated_source, encoding="utf-8")
    rebind_calculation_result(project_dir)
    rebind_scan_result(project_dir)

    result = run_workspace_validator(repo_root, project_dir)

    assert result.returncode != 0
    assert "must not use decorators" in result.stdout + result.stderr


def test_workspace_validator_rejects_later_python_function_rebinding(
    tmp_path,
    project_copy_factory,
    read_json,
    rebind_calculation_result,
    rebind_scan_result,
    repo_root,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    result_meta = read_json(
        project_dir / "calculations" / "task-001" / "result-meta.json"
    )
    python_path = project_dir / "calculations" / "task-001" / result_meta["python_file"]
    with python_path.open("a", encoding="utf-8") as handle:
        handle.write(
            f"\n{result_meta['python_function']} = lambda **kwargs: 0.0\n"
        )
    rebind_calculation_result(project_dir)
    rebind_scan_result(project_dir)

    result = run_workspace_validator(repo_root, project_dir)

    assert result.returncode != 0
    assert "is rebound after its selected definition" in result.stdout + result.stderr


@pytest.mark.parametrize(("field", "value"), [("role", "fixed"), ("unit", "TeV")])
def test_workspace_validator_rejects_result_parameter_model_contract_drift(
    tmp_path,
    project_copy_factory,
    read_json,
    write_json,
    rebind_scan_result,
    repo_root,
    field,
    value,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    result_meta_path = project_dir / "calculations" / "task-001" / "result-meta.json"
    result_meta = read_json(result_meta_path)
    parameter = next(
        item for item in result_meta["parameters"] if item["canonical_name"] == "v_Delta"
    )
    parameter[field] = value
    write_json(result_meta_path, result_meta)
    rebind_scan_result(project_dir)

    result = run_workspace_validator(repo_root, project_dir)

    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert f"'v_Delta' {field}" in combined
    assert "does not match model-spec" in combined


def test_workspace_validator_accepts_package_x_loop_provenance(
    tmp_path,
    project_copy_factory,
    read_json,
    write_json,
    rebind_calculation_result,
    rebind_scan_result,
    repo_root,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    task_dir = project_dir / "calculations" / "task-001"
    (task_dir / "result.wl").write_text(
        "amplitude = LoopIntegrate[numerator, k, {k, m}];\n"
        "finalResult = LoopRefine[amplitude];\n",
        encoding="utf-8",
    )
    result_meta_path = task_dir / "result-meta.json"
    result_meta = read_json(result_meta_path)
    result_meta["calculation_provenance"] = "package_x_derived"
    result_meta["benchmark_used_as_input"] = False
    result_meta["package_x_methods"] = ["LoopIntegrate", "LoopRefine"]
    result_meta["provenance_notes"] = "Test fixture with explicit Package-X loop markers."
    _bind_package_x_derivation_evidence(task_dir, result_meta)
    write_json(result_meta_path, result_meta)
    rebind_calculation_result(project_dir)
    rebind_scan_result(project_dir)

    result = run_workspace_validator(repo_root, project_dir)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "result-meta.json provenance" in result.stdout


def test_workspace_validator_rejects_package_x_marker_only_in_comment(
    tmp_path,
    project_copy_factory,
    read_json,
    write_json,
    repo_root,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    task_dir = project_dir / "calculations" / "task-001"
    (task_dir / "result.wl").write_text(
        "(* LoopIntegrate[numerator, k, {k, m}] is documentation only. *)\n"
        "finalResult = importedFormula;\n",
        encoding="utf-8",
    )
    result_meta_path = task_dir / "result-meta.json"
    result_meta = read_json(result_meta_path)
    result_meta["calculation_provenance"] = "package_x_derived"
    result_meta["benchmark_used_as_input"] = False
    result_meta["package_x_methods"] = ["LoopIntegrate"]
    result_meta["provenance_notes"] = "A comment must not prove an independent derivation."
    _bind_package_x_derivation_evidence(task_dir, result_meta)
    write_json(result_meta_path, result_meta)

    result = run_workspace_validator(repo_root, project_dir)

    assert result.returncode != 0
    combined_output = result.stdout + result.stderr
    assert "no executable call outside comments/strings" in combined_output


def test_workspace_validator_rejects_benchmark_formula_marked_package_x(
    tmp_path,
    project_copy_factory,
    read_json,
    write_json,
    repo_root,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    task_dir = project_dir / "calculations" / "task-001"
    (task_dir / "result.wl").write_text(
        "finalResult = benchmarkFormula;\n",
        encoding="utf-8",
    )
    (task_dir / "result-summary.md").write_text(
        "# Result Summary\n\n"
        "result.wl directly implements the benchmark formula.\n\n"
        "## Benchmark Verification\n\nPASS\n",
        encoding="utf-8",
    )
    result_meta_path = task_dir / "result-meta.json"
    result_meta = read_json(result_meta_path)
    result_meta["calculation_provenance"] = "package_x_derived"
    result_meta["benchmark_used_as_input"] = True
    result_meta["package_x_methods"] = ["LoopIntegrate"]
    result_meta["provenance_notes"] = "Incorrectly claims Package-X provenance."
    _bind_package_x_derivation_evidence(task_dir, result_meta)
    write_json(result_meta_path, result_meta)

    result = run_workspace_validator(repo_root, project_dir)

    assert result.returncode != 0
    combined_output = result.stdout + result.stderr
    assert "provenance" in combined_output
    assert "benchmark_used_as_input" in combined_output


def test_workspace_validator_accepts_valid_calculations_artifact(
    tmp_path,
    project_copy_factory,
    read_json,
    write_json,
    repo_root,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    manifest_path = project_dir / "manifest.json"
    manifest = read_json(manifest_path)
    calculations = manifest["artifacts"]["calculations"]
    calculations["completed_tasks"] = ["task-001"]
    calculations["pending_tasks"] = [
        task_id for task_id in calculations["pending_tasks"] if task_id != "task-001"
    ]
    write_json(manifest_path, manifest)

    result = run_workspace_validator(repo_root, project_dir)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK   manifest.json calculations artifact" in result.stdout


def test_workspace_validator_rejects_constraints_for_a_different_model_version(
    tmp_path,
    project_copy_factory,
    read_json,
    write_json,
    repo_root,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    constraints_path = project_dir / "constraints" / "constraints-data.json"
    constraints = read_json(constraints_path)
    constraints["model_version"] = "v999"
    write_json(constraints_path, constraints)

    result = run_workspace_validator(repo_root, project_dir)

    assert result.returncode != 0
    assert (
        "constraints/constraints-data.json model_version does not match "
        "the active model version"
    ) in result.stdout


def test_workspace_validator_rejects_completed_task_missing_result_meta(
    tmp_path,
    project_copy_factory,
    read_json,
    write_json,
    repo_root,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    manifest_path = project_dir / "manifest.json"
    manifest = read_json(manifest_path)
    calculations = manifest["artifacts"]["calculations"]
    calculations["completed_tasks"] = ["task-001"]
    calculations["pending_tasks"] = [
        task_id for task_id in calculations["pending_tasks"] if task_id != "task-001"
    ]
    write_json(manifest_path, manifest)
    (project_dir / "calculations" / "task-001" / "result-meta.json").unlink()

    result = run_workspace_validator(repo_root, project_dir)

    assert result.returncode != 0
    combined_output = result.stdout + result.stderr
    assert "calculations.completed_tasks references missing task" in combined_output
    assert "task-001" in combined_output


def test_workspace_validator_rejects_pending_task_not_in_calc_tasks(
    tmp_path,
    project_copy_factory,
    read_json,
    write_json,
    repo_root,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    manifest_path = project_dir / "manifest.json"
    manifest = read_json(manifest_path)
    manifest["artifacts"]["calculations"]["pending_tasks"].append("task-999")
    write_json(manifest_path, manifest)

    result = run_workspace_validator(repo_root, project_dir)

    assert result.returncode != 0
    combined_output = result.stdout + result.stderr
    assert "calculations.pending_tasks contains tasks not declared" in combined_output
    assert "task-999" in combined_output


def test_workspace_validator_rejects_stale_completed_task(
    tmp_path,
    project_copy_factory,
    read_json,
    write_json,
    repo_root,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    manifest_path = project_dir / "manifest.json"
    manifest = read_json(manifest_path)
    calculations = manifest["artifacts"]["calculations"]
    calculations["completed_tasks"] = ["task-001"]
    calculations["pending_tasks"] = [
        task_id for task_id in calculations["pending_tasks"] if task_id != "task-001"
    ]
    write_json(manifest_path, manifest)

    result_meta_path = project_dir / "calculations" / "task-001" / "result-meta.json"
    result_meta = read_json(result_meta_path)
    result_meta["depends_on"]["model_version"] = "v0"
    write_json(result_meta_path, result_meta)

    result = run_workspace_validator(repo_root, project_dir)

    assert result.returncode == 1
    assert "FAIL calculations/task-001/result-meta.json" in result.stdout
    assert "stale calculation" in result.stdout


def test_workspace_validator_accepts_explicit_stale_calculation_history(
    tmp_path,
    project_copy_factory,
    read_json,
    write_json,
    repo_root,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    calc_tasks_path = project_dir / "model" / "calc-tasks.json"
    calc_tasks = read_json(calc_tasks_path)
    calc_tasks["tasks"][0]["description"] += " Later task-definition revision."
    write_json(calc_tasks_path, calc_tasks)

    manifest_path = project_dir / "manifest.json"
    manifest = read_json(manifest_path)
    manifest["artifacts"]["calculations"]["status"] = "stale"
    manifest["artifacts"]["numerics"]["status"] = "stale"
    manifest["artifacts"]["numerics"]["analyses"][0]["status"] = "stale"
    write_json(manifest_path, manifest)

    result = run_workspace_validator(repo_root, project_dir)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "calculations artifact (stale historical evidence)" in result.stdout
    assert (
        "historical graph; current-byte equality intentionally skipped"
        in result.stdout
    )


def test_workspace_validator_rejects_stale_result_outside_preserved_generation(
    tmp_path,
    project_copy_factory,
    read_json,
    write_json,
    repo_root,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    manifest_path = project_dir / "manifest.json"
    manifest = read_json(manifest_path)
    calculations = manifest["artifacts"]["calculations"]
    calculations["status"] = "stale"
    calculations["depends_on"]["model"]["version"] = "v0"
    write_json(manifest_path, manifest)

    result = run_workspace_validator(repo_root, project_dir)

    assert result.returncode != 0
    assert "does not match preserved stale calculations dependency 'v0'" in result.stdout
