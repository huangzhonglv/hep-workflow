from __future__ import annotations

import subprocess
import sys
from pathlib import Path


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
    assert "literature/paper-extract.json: valid JSON" in result.stdout
    assert "OK   manifest.json literature artifact" in result.stdout


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


def test_workspace_validator_accepts_package_x_loop_provenance(
    tmp_path,
    project_copy_factory,
    read_json,
    write_json,
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
    write_json(result_meta_path, result_meta)

    result = run_workspace_validator(repo_root, project_dir)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "result-meta.json provenance" in result.stdout


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


def test_workspace_validator_warns_stale_completed_task_without_failing(
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

    assert result.returncode == 0, result.stdout + result.stderr
    assert "WARN calculations/task-001/result-meta.json" in result.stdout
    assert "stale calculation" in result.stdout
