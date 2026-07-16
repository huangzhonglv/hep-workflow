from __future__ import annotations

import csv
import hashlib
import importlib
import subprocess
import sys

import pytest

from scripts import _publication_transaction
from scripts._scan_artifact_validation import validate_figure_artifact_set


def test_formula_fallback_requires_explicit_opt_in(
    tmp_path,
    project_copy_factory,
    ensure_task_result,
    read_json,
    write_json,
    run_scan_module,
    run_scan_script,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    ensure_task_result(
        project_dir,
        task_id="task-001",
        observable="Br_mu_to_egamma",
        function_name="compute_br_mu_to_egamma",
        parameter_specs=[
            {"canonical_name": "M_Hpp", "role": "scan", "unit": "GeV"},
            {"canonical_name": "v_Delta", "role": "scan", "unit": "GeV"},
        ],
    )

    manifest = read_json(project_dir / "manifest.json")
    scan_config = {
        "analysis_id": "analysis-100",
        "model_name": "Minimal Type II Seesaw (scalar triplet extension)",
        "description": "Fallback gate test",
        "depends_on": {
            "model_version": manifest["active_model_version"],
            "model_checksum": manifest["artifacts"]["model"]["checksum"],
            "task_ids": ["task-001"],
        },
        "scan_parameters": [
            {"canonical_name": "M_Hpp", "range": [100.0, 200.0], "grid": 2, "scale": "linear"}
        ],
        "fixed_parameters": [
            {"canonical_name": "v_Delta", "value": 1.0e-3}
        ],
        "observables": [
            {"observable": "Br_mu_to_egamma", "source": {"type": "task", "task_id": "task-001"}}
        ],
        "constraints_used": ["c-001"],
        "figures": [],
        "seed": 0,
        "parallelism": 1,
    }
    write_json(project_dir / "numerics" / "scan-configs" / "analysis-100.json", scan_config)

    inputs = run_scan_module.load_inputs(project_dir=project_dir, analysis_id="analysis-100")
    validation = run_scan_module.validate(inputs)
    assert validation["report"].has_errors
    report_text = "\n".join(
        detail
        for check in validation["report"].checks
        for detail in check.details
    )
    assert "allow_formula_fallback" in report_text

    result = subprocess.run(
        [
            sys.executable,
            str(run_scan_script),
            "--project-dir",
            str(project_dir),
            "--analysis-id",
            "analysis-100",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    combined_output = result.stdout + result.stderr
    assert "allow_formula_fallback" in combined_output
    assert not (project_dir / "numerics" / "scan-results" / "analysis-100" / "scan.csv").exists()


def _sha256(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_scan_history_classification_rejects_unregistered_orphan_artifacts(
    tmp_path,
    project_copy_factory,
    read_json,
    run_scan_module,
    repo_root,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    analysis_id = "analysis-104"
    orphan_dir = project_dir / "numerics" / "scan-results" / analysis_id
    orphan_dir.mkdir(parents=True)
    (orphan_dir / "scan.csv").write_text("orphan\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="unregistered prior scan artifacts"):
        run_scan_module.determine_scan_history_action(
            project_dir,
            analysis_id,
            read_json(project_dir / "manifest.json"),
            repo_root,
        )


def test_scan_history_classification_requires_registered_valid_prior_pair(
    tmp_path,
    project_copy_factory,
    read_json,
    run_scan_module,
    repo_root,
) -> None:
    project_dir = project_copy_factory(tmp_path)

    assert run_scan_module.determine_scan_history_action(
        project_dir,
        "analysis-001",
        read_json(project_dir / "manifest.json"),
        repo_root,
    ) == "numerics_analysis_rerun"


def test_scan_rerun_failure_restores_the_complete_previous_generation(
    tmp_path,
    monkeypatch,
    project_copy_factory,
    ensure_task_result,
    read_json,
    write_json,
    run_scan_module,
    run_scan_script,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    ensure_task_result(
        project_dir,
        task_id="task-001",
        observable="Br_mu_to_egamma",
        function_name="compute_br_mu_to_egamma",
        python_body=(
            "from __future__ import annotations\n\n"
            "def compute_br_mu_to_egamma(*, M_Hpp: float) -> float:\n"
            "    return float(1.0e-13 * (100.0 / float(M_Hpp)) ** 2)\n"
        ),
        parameter_specs=[
            {"canonical_name": "M_Hpp", "role": "scan", "unit": "GeV"},
        ],
    )
    manifest = read_json(project_dir / "manifest.json")
    analysis_id = "analysis-102"
    write_json(
        project_dir / "numerics" / "scan-configs" / f"{analysis_id}.json",
        {
            "analysis_id": analysis_id,
            "model_name": "Minimal Type II Seesaw (scalar triplet extension)",
            "description": "Transactional rerun test",
            "depends_on": {
                "model_version": manifest["active_model_version"],
                "model_checksum": manifest["artifacts"]["model"]["checksum"],
                "task_ids": ["task-001"],
            },
            "scan_parameters": [
                {
                    "canonical_name": "M_Hpp",
                    "range": [100.0, 200.0],
                    "grid": 2,
                    "scale": "linear",
                }
            ],
            "fixed_parameters": [],
            "observables": [
                {
                    "observable": "Br_mu_to_egamma",
                    "source": {"type": "task", "task_id": "task-001"},
                }
            ],
            "constraints_used": ["c-001"],
            "figures": [],
            "allow_formula_fallback": True,
            "seed": 0,
            "parallelism": 1,
        },
    )
    command = [
        sys.executable,
        str(run_scan_script),
        "--project-dir",
        str(project_dir),
        "--analysis-id",
        analysis_id,
    ]
    first = subprocess.run(command, capture_output=True, text=True)
    assert first.returncode == 0, first.stdout + first.stderr

    result_dir = project_dir / "numerics" / "scan-results" / analysis_id
    summary_path = project_dir / "numerics" / f"analysis-summary-{analysis_id}.md"
    manifest_path = project_dir / "manifest.json"
    owned_paths = [
        result_dir / "scan.csv",
        result_dir / "scan.meta.json",
        summary_path,
        manifest_path,
    ]
    before = {path: _sha256(path) for path in owned_paths}
    transaction_module = importlib.import_module(
        run_scan_module.PublicationTransaction.__module__
    )
    original_replace = transaction_module._rename_no_replace
    failure_injected = False

    def fail_manifest_publish(source, destination):
        nonlocal failure_injected
        if destination == manifest_path and not failure_injected:
            failure_injected = True
            raise OSError("injected manifest publication failure")
        return original_replace(source, destination)

    monkeypatch.setattr(transaction_module, "_rename_no_replace", fail_manifest_publish)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(run_scan_script),
            "--project-dir",
            str(project_dir),
            "--analysis-id",
            analysis_id,
        ],
    )
    assert run_scan_module.main() == 1
    assert {path: _sha256(path) for path in owned_paths} == before
    assert _publication_transaction.active_transactions(project_dir) == ()

    monkeypatch.setattr(transaction_module, "_rename_no_replace", original_replace)
    assert run_scan_module.main() == 0


def test_second_analysis_and_rerun_preserve_first_analysis_ownership(
    tmp_path,
    project_copy_factory,
    read_json,
    write_json,
    run_scan_script,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    manifest_before = read_json(project_dir / "manifest.json")
    first_before = manifest_before["artifacts"]["numerics"]["analyses"][0]
    history_length_before = len(manifest_before["history"])

    analysis_id = "analysis-103"
    config = read_json(
        project_dir / "numerics" / "scan-configs" / "analysis-001.json"
    )
    config["analysis_id"] = analysis_id
    config["description"] = "Second analysis ownership integration test."
    write_json(
        project_dir / "numerics" / "scan-configs" / f"{analysis_id}.json",
        config,
    )
    command = [
        sys.executable,
        str(run_scan_script),
        "--project-dir",
        str(project_dir),
        "--analysis-id",
        analysis_id,
    ]

    first_run = subprocess.run(command, capture_output=True, text=True)
    assert first_run.returncode == 0, first_run.stdout + first_run.stderr
    after_first_run = read_json(project_dir / "manifest.json")
    analyses = {
        entry["analysis_id"]: entry
        for entry in after_first_run["artifacts"]["numerics"]["analyses"]
    }
    assert sorted(analyses) == ["analysis-001", analysis_id]
    assert analyses["analysis-001"] == first_before
    assert after_first_run["artifacts"]["numerics"]["files"] == sorted(
        {
            path
            for entry in analyses.values()
            for path in entry["files"]
        }
    )

    second_run = subprocess.run(command, capture_output=True, text=True)
    assert second_run.returncode == 0, second_run.stdout + second_run.stderr
    after_second_run = read_json(project_dir / "manifest.json")
    rerun_analyses = {
        entry["analysis_id"]: entry
        for entry in after_second_run["artifacts"]["numerics"]["analyses"]
    }
    assert sorted(rerun_analyses) == ["analysis-001", analysis_id]
    assert rerun_analyses["analysis-001"] == first_before
    assert len(after_second_run["history"]) == history_length_before + 2
    assert len(
        {
            entry["event_id"]
            for entry in after_second_run["history"][-2:]
        }
    ) == 2


def test_minimal_scan_runs_and_generates_figures(
    tmp_path,
    project_copy_factory,
    ensure_task_result,
    read_json,
    write_json,
    run_scan_module,
    run_scan_script,
    make_figures_script,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    ensure_task_result(
        project_dir,
        task_id="task-001",
        observable="Br_mu_to_egamma",
        function_name="compute_br_mu_to_egamma",
        python_body="""
from __future__ import annotations


def compute_br_mu_to_egamma(*, M_Hpp: float, v_Delta: float = 1.0e-3) -> float:
    safe_mass = max(float(M_Hpp), 1.0)
    return float(5.0e-13 * (100.0 / safe_mass) ** 2 * (1.0 + 100.0 * float(v_Delta)))
""".strip()
        + "\n",
        parameter_specs=[
            {"canonical_name": "M_Hpp", "role": "scan", "unit": "GeV"},
            {"canonical_name": "v_Delta", "role": "scan", "unit": "GeV"},
        ],
    )

    manifest = read_json(project_dir / "manifest.json")
    scan_config = {
        "analysis_id": "analysis-101",
        "model_name": "Minimal Type II Seesaw (scalar triplet extension)",
        "description": "Integration test minimal scan",
        "depends_on": {
            "model_version": manifest["active_model_version"],
            "model_checksum": manifest["artifacts"]["model"]["checksum"],
            "task_ids": ["task-001"],
        },
        "scan_parameters": [
            {"canonical_name": "M_Hpp", "range": [100.0, 500.0], "grid": 2, "scale": "linear"},
            {"canonical_name": "v_Delta", "range": [1.0e-4, 1.0e-3], "grid": 2, "scale": "log"},
        ],
        "fixed_parameters": [],
        "observables": [
            {"observable": "Br_mu_to_egamma", "source": {"type": "task", "task_id": "task-001"}}
        ],
        "constraints_used": ["c-001"],
        "figures": [
            {
                "kind": "exclusion_2d",
                "x": "M_Hpp",
                "y": "v_Delta",
                "constraints": ["c-001"],
                "show_allowed_region": True,
            },
            {
                "kind": "scan_1d",
                "x": "M_Hpp",
                "observables": ["Br_mu_to_egamma"],
                "fixed": {"v_Delta": 1.0e-3},
                "overlay_constraint_bands": True,
            }
        ],
        "allow_formula_fallback": True,
        "seed": 0,
        "parallelism": 1,
    }
    scan_config_path = project_dir / "numerics" / "scan-configs" / "analysis-101.json"
    write_json(scan_config_path, scan_config)

    inputs = run_scan_module.load_inputs(project_dir=project_dir, analysis_id="analysis-101")
    validation = run_scan_module.validate(inputs)
    assert not validation["report"].has_errors
    runtime = run_scan_module.prepare_runtime(inputs, validation["runtime"])
    one_point = run_scan_module.evaluate_point(
        {"M_Hpp": 100.0, "v_Delta": 1.0e-3},
        inputs,
        runtime,
    )
    assert one_point["row"]["Br_mu_to_egamma"] is not None
    assert one_point["row"]["c-001_verdict"] in {"allowed", "excluded"}

    run_result = subprocess.run(
        [
            sys.executable,
            str(run_scan_script),
            "--project-dir",
            str(project_dir),
            "--analysis-id",
            "analysis-101",
        ],
        capture_output=True,
        text=True,
    )
    assert run_result.returncode == 0, run_result.stdout + run_result.stderr

    summary_path = project_dir / "numerics" / "analysis-summary-analysis-101.md"
    assert summary_path.exists()
    summary_text = summary_path.read_text(encoding="utf-8")
    assert "## Key findings" in summary_text
    assert "## Reproducibility" in summary_text
    assert "Integration test minimal scan" in summary_text
    assert "Br_mu_to_egamma" in summary_text
    assert "## Formula fallback provenance" in summary_text
    manifest = read_json(project_dir / "manifest.json")
    assert manifest["history"][-1]["action"] == "numerics_analysis_complete"
    assert manifest["artifacts"]["numerics"]["status"] == "partial"
    assert "numerics/scan-results/analysis-101/scan.csv" in manifest["artifacts"]["numerics"]["files"]
    assert "numerics/scan-results/analysis-101/scan.meta.json" in manifest["artifacts"]["numerics"]["files"]
    assert "numerics/analysis-summary-analysis-101.md" in manifest["artifacts"]["numerics"]["files"]
    assert not any("/figures/analysis-101/" in path for path in manifest["artifacts"]["numerics"]["files"])
    scan_meta = read_json(project_dir / "numerics" / "scan-results" / "analysis-101" / "scan.meta.json")
    assert scan_meta["formula_fallbacks"][0]["task_id"] == "task-001"
    assert any("formula fallback enabled" in warning for warning in scan_meta["warnings"])

    figure_result = subprocess.run(
        [
            sys.executable,
            str(make_figures_script),
            "--project-dir",
            str(project_dir),
            "--analysis-id",
            "analysis-101",
        ],
        capture_output=True,
        text=True,
    )
    assert figure_result.returncode == 0, figure_result.stdout + figure_result.stderr

    summary_text = summary_path.read_text(encoding="utf-8")
    assert "exclusion-M_Hpp-v_Delta.pdf" in summary_text
    assert "scan1d-M_Hpp-Br_mu_to_egamma.pdf" in summary_text
    assert "scan-M_Hpp-Br_mu_to_egamma" not in summary_text
    manifest = read_json(project_dir / "manifest.json")
    assert manifest["artifacts"]["numerics"]["status"] == "done"
    assert "numerics/analysis-summary-analysis-101.md" in manifest["artifacts"]["numerics"]["files"]
    assert "numerics/figures/analysis-101/exclusion-M_Hpp-v_Delta.pdf" in manifest["artifacts"]["numerics"]["files"]
    assert "numerics/figures/analysis-101/scan1d-M_Hpp-Br_mu_to_egamma.pdf" in manifest["artifacts"]["numerics"]["files"]
    assert not any(
        path.endswith("/scan-M_Hpp-Br_mu_to_egamma.pdf")
        or path.endswith("/scan-M_Hpp-Br_mu_to_egamma.png")
        for path in manifest["artifacts"]["numerics"]["files"]
    )

    scan_csv_path = project_dir / "numerics" / "scan-results" / "analysis-101" / "scan.csv"
    with scan_csv_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames is not None
        assert "c-001_skip_reason" in reader.fieldnames
        rows = list(reader)
    assert len(rows) == 4
    assert {row["c-001_skip_reason"] for row in rows} == {""}

    figures_dir = project_dir / "numerics" / "figures" / "analysis-101"
    assert (figures_dir / "exclusion-M_Hpp-v_Delta.pdf").exists()
    assert (figures_dir / "exclusion-M_Hpp-v_Delta.png").exists()
    assert (figures_dir / "scan1d-M_Hpp-Br_mu_to_egamma.pdf").exists()
    assert (figures_dir / "scan1d-M_Hpp-Br_mu_to_egamma.png").exists()
    assert not (figures_dir / "scan-M_Hpp-Br_mu_to_egamma.pdf").exists()
    assert not (figures_dir / "scan-M_Hpp-Br_mu_to_egamma.png").exists()

    legacy_alias = figures_dir / "scan-M_Hpp-Br_mu_to_egamma.pdf"
    legacy_alias.write_bytes(
        (figures_dir / "scan1d-M_Hpp-Br_mu_to_egamma.pdf").read_bytes()
    )
    figure_issues = validate_figure_artifact_set(
        project_dir,
        "analysis-101",
        read_json(project_dir / "numerics" / "scan-configs" / "analysis-101.json"),
        read_json(
            project_dir
            / "numerics"
            / "scan-results"
            / "analysis-101"
            / "scan.meta.json"
        ),
        read_json(figures_dir / "figures.meta.json"),
    )
    assert any(
        "figure directory contents do not exactly match" in issue
        for issue in figure_issues
    )


def test_committed_scan_cleanup_warning_is_success_without_duplicate_retry(
    tmp_path,
    monkeypatch,
    capsys,
    project_copy_factory,
    ensure_task_result,
    read_json,
    run_scan_module,
    run_scan_script,
) -> None:
    project_dir = project_copy_factory(tmp_path)
    ensure_task_result(project_dir)
    manifest_path = project_dir / "manifest.json"
    history_length = len(read_json(manifest_path)["history"])
    original_commit = run_scan_module.PublicationTransaction.commit

    def commit_then_report_pending_cleanup(self, *args, **kwargs):
        original_commit(self, *args, **kwargs)
        raise run_scan_module.TransactionCommittedCleanupError(
            self.transaction_id,
            OSError("injected cleanup interruption"),
        )

    monkeypatch.setattr(
        run_scan_module.PublicationTransaction,
        "commit",
        commit_then_report_pending_cleanup,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(run_scan_script),
            "--project-dir",
            str(project_dir),
            "--analysis-id",
            "analysis-001",
        ],
    )

    assert run_scan_module.main() == 0
    warning = capsys.readouterr().err
    assert "committed successfully" in warning
    assert "Do not retry" in warning
    assert "injected cleanup interruption" in warning
    manifest = read_json(manifest_path)
    assert len(manifest["history"]) == history_length + 1
    assert manifest["history"][-1]["action"] == "numerics_analysis_rerun"
