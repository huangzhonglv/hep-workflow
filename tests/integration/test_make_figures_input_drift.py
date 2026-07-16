from __future__ import annotations

import importlib
import hashlib
import subprocess
import sys
from pathlib import Path
from typing import Any

from scripts import _publication_transaction
from scripts._publication_transaction import active_transactions


def prepare_scanned_project(
    tmp_path: Path,
    project_copy_factory,
    ensure_task_result,
    read_json,
    write_json,
    run_scan_script: Path,
) -> tuple[Path, Path]:
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
    analysis_id = "analysis-301"
    scan_config_path = (
        project_dir / "numerics" / "scan-configs" / f"{analysis_id}.json"
    )
    write_json(
        scan_config_path,
        {
            "analysis_id": analysis_id,
            "model_name": "Minimal Type II Seesaw (scalar triplet extension)",
            "description": "Figure input drift test",
            "depends_on": {
                "model_version": manifest["active_model_version"],
                "model_checksum": manifest["artifacts"]["model"]["checksum"],
                "task_ids": ["task-001"],
            },
            "scan_parameters": [
                {
                    "canonical_name": "M_Hpp",
                    "range": [100.0, 400.0],
                    "grid": 4,
                    "scale": "linear",
                }
            ],
            "fixed_parameters": [
                {"canonical_name": "v_Delta", "value": 1.0e-3}
            ],
            "observables": [
                {
                    "observable": "Br_mu_to_egamma",
                    "source": {"type": "task", "task_id": "task-001"},
                }
            ],
            "constraints_used": ["c-001"],
            "figures": [
                {
                    "kind": "scan_1d",
                    "x": "M_Hpp",
                    "observables": ["Br_mu_to_egamma"],
                    "overlay_constraint_bands": True,
                }
            ],
            "allow_formula_fallback": True,
            "seed": 0,
            "parallelism": 1,
        },
    )
    result = subprocess.run(
        [
            sys.executable,
            str(run_scan_script),
            "--project-dir",
            str(project_dir),
            "--analysis-id",
            analysis_id,
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return project_dir, scan_config_path


def drift_description(scan_config_path: Path, read_json, write_json) -> None:
    config = read_json(scan_config_path)
    config["description"] += " (concurrent drift)"
    write_json(scan_config_path, config)


def run_make_figures_main(
    monkeypatch,
    make_figures_module: Any,
    make_figures_script: Path,
    project_dir: Path,
    *,
    overwrite: bool = False,
) -> int:
    argv = [
        str(make_figures_script),
        "--project-dir",
        str(project_dir),
        "--analysis-id",
        "analysis-301",
    ]
    if overwrite:
        argv.append("--overwrite")
    monkeypatch.setattr(
        sys,
        "argv",
        argv,
    )
    return make_figures_module.main()


def test_title_only_replot_preserves_scan_proof_and_rebinds_renderer_provenance(
    tmp_path,
    monkeypatch,
    project_copy_factory,
    ensure_task_result,
    read_json,
    write_json,
    run_scan_script,
    make_figures_script,
    make_figures_module,
) -> None:
    project_dir, scan_config_path = prepare_scanned_project(
        tmp_path,
        project_copy_factory,
        ensure_task_result,
        read_json,
        write_json,
        run_scan_script,
    )
    assert run_make_figures_main(
        monkeypatch,
        make_figures_module,
        make_figures_script,
        project_dir,
    ) == 0

    result_dir = project_dir / "numerics" / "scan-results" / "analysis-301"
    csv_path = result_dir / "scan.csv"
    meta_path = result_dir / "scan.meta.json"
    figure_meta_path = (
        project_dir
        / "numerics"
        / "figures"
        / "analysis-301"
        / "figures.meta.json"
    )
    csv_before = csv_path.read_bytes()
    meta_before = meta_path.read_bytes()
    figure_meta_before = read_json(figure_meta_path)

    config = read_json(scan_config_path)
    config["figures"][0]["title"] = "Updated presentation title"
    write_json(scan_config_path, config)

    assert run_make_figures_main(
        monkeypatch,
        make_figures_module,
        make_figures_script,
        project_dir,
        overwrite=True,
    ) == 0

    figure_meta_after = read_json(figure_meta_path)
    assert csv_path.read_bytes() == csv_before
    assert meta_path.read_bytes() == meta_before
    assert (
        figure_meta_after["scan_execution_sha256"]
        == figure_meta_before["scan_execution_sha256"]
    )
    assert (
        figure_meta_after["render_config_snapshot"]["figures"][0]["title"]
        == "Updated presentation title"
    )
    assert (
        figure_meta_after["input_provenance"]["root_sha256"]
        != figure_meta_before["input_provenance"]["root_sha256"]
    )


def test_semantic_figure_change_requires_a_new_scan_and_publishes_nothing(
    tmp_path,
    monkeypatch,
    capsys,
    project_copy_factory,
    ensure_task_result,
    read_json,
    write_json,
    run_scan_script,
    make_figures_script,
    make_figures_module,
) -> None:
    project_dir, scan_config_path = prepare_scanned_project(
        tmp_path,
        project_copy_factory,
        ensure_task_result,
        read_json,
        write_json,
        run_scan_script,
    )
    manifest_path = project_dir / "manifest.json"
    summary_path = project_dir / "numerics" / "analysis-summary-analysis-301.md"
    manifest_before = manifest_path.read_bytes()
    summary_before = summary_path.read_bytes()
    config = read_json(scan_config_path)
    config["figures"][0]["overlay_constraint_bands"] = False
    write_json(scan_config_path, config)

    assert run_make_figures_main(
        monkeypatch,
        make_figures_module,
        make_figures_script,
        project_dir,
    ) == 1

    assert "execution semantics do not match" in capsys.readouterr().err
    assert manifest_path.read_bytes() == manifest_before
    assert summary_path.read_bytes() == summary_before
    assert not (
        project_dir / "numerics" / "figures" / "analysis-301"
    ).exists()


def test_stale_analysis_validates_its_frozen_figure_generation(
    tmp_path,
    monkeypatch,
    project_copy_factory,
    ensure_task_result,
    read_json,
    write_json,
    run_scan_script,
    make_figures_script,
    make_figures_module,
    repo_root,
) -> None:
    project_dir, scan_config_path = prepare_scanned_project(
        tmp_path,
        project_copy_factory,
        ensure_task_result,
        read_json,
        write_json,
        run_scan_script,
    )
    assert run_make_figures_main(
        monkeypatch,
        make_figures_module,
        make_figures_script,
        project_dir,
    ) == 0

    config = read_json(scan_config_path)
    config["figures"][0]["overlay_constraint_bands"] = False
    write_json(scan_config_path, config)

    manifest_path = project_dir / "manifest.json"
    manifest = read_json(manifest_path)
    numerics = manifest["artifacts"]["numerics"]
    numerics["status"] = "stale"
    for analysis in numerics["analyses"]:
        analysis["status"] = "stale"
    write_json(manifest_path, manifest)

    result = subprocess.run(
        [
            sys.executable,
            str(repo_root / "scripts" / "validate_workspace_projects.py"),
            "--workspace-root",
            str(project_dir.parent),
            project_dir.name,
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "historical graph structure/coverage verified" in result.stdout
    assert "renderer provenance and outputs" in result.stdout

    figure_meta_path = (
        project_dir
        / "numerics"
        / "figures"
        / "analysis-301"
        / "figures.meta.json"
    )
    figure_meta = read_json(figure_meta_path)
    figure_meta["render_config_snapshot"]["analysis_id"] = "analysis-999"
    write_json(figure_meta_path, figure_meta)
    tampered = subprocess.run(
        [
            sys.executable,
            str(repo_root / "scripts" / "validate_workspace_projects.py"),
            "--workspace-root",
            str(project_dir.parent),
            project_dir.name,
        ],
        capture_output=True,
        text=True,
    )

    assert tampered.returncode != 0
    assert "changes immutable scan execution semantics" in tampered.stdout


def test_config_drift_during_render_never_publishes_staged_outputs(
    tmp_path,
    monkeypatch,
    capsys,
    project_copy_factory,
    ensure_task_result,
    read_json,
    write_json,
    run_scan_script,
    make_figures_script,
    make_figures_module,
) -> None:
    project_dir, scan_config_path = prepare_scanned_project(
        tmp_path,
        project_copy_factory,
        ensure_task_result,
        read_json,
        write_json,
        run_scan_script,
    )
    manifest_path = project_dir / "manifest.json"
    summary_path = project_dir / "numerics" / "analysis-summary-analysis-301.md"
    manifest_before = manifest_path.read_bytes()
    summary_before = summary_path.read_bytes()
    original_render = make_figures_module.render_figures

    def render_then_drift(*args, **kwargs):
        result = original_render(*args, **kwargs)
        drift_description(scan_config_path, read_json, write_json)
        return result

    monkeypatch.setattr(make_figures_module, "render_figures", render_then_drift)

    assert (
        run_make_figures_main(
            monkeypatch,
            make_figures_module,
            make_figures_script,
            project_dir,
        )
        == 1
    )

    assert "before summary staging" in capsys.readouterr().err
    assert manifest_path.read_bytes() == manifest_before
    assert summary_path.read_bytes() == summary_before
    figure_dir = project_dir / "numerics" / "figures" / "analysis-301"
    assert not figure_dir.exists()
    assert active_transactions(project_dir) == ()


def test_config_drift_inside_manifest_update_blocks_manifest_publication(
    tmp_path,
    monkeypatch,
    capsys,
    project_copy_factory,
    ensure_task_result,
    read_json,
    write_json,
    run_scan_script,
    make_figures_script,
    make_figures_module,
) -> None:
    project_dir, scan_config_path = prepare_scanned_project(
        tmp_path,
        project_copy_factory,
        ensure_task_result,
        read_json,
        write_json,
        run_scan_script,
    )
    manifest_path = project_dir / "manifest.json"
    summary_path = project_dir / "numerics" / "analysis-summary-analysis-301.md"
    manifest_before = manifest_path.read_bytes()
    summary_before = summary_path.read_bytes()
    original_build = make_figures_module.MANIFEST.build_manifest_for_numerics

    def drift_then_build(*args, **kwargs):
        drift_description(scan_config_path, read_json, write_json)
        return original_build(*args, **kwargs)

    monkeypatch.setattr(
        make_figures_module.MANIFEST,
        "build_manifest_for_numerics",
        drift_then_build,
    )

    assert (
        run_make_figures_main(
            monkeypatch,
            make_figures_module,
            make_figures_script,
            project_dir,
        )
        == 1
    )

    assert "transaction publication guard" in capsys.readouterr().err
    assert manifest_path.read_bytes() == manifest_before
    assert summary_path.read_bytes() == summary_before
    assert not (
        project_dir / "numerics" / "figures" / "analysis-301"
    ).exists()
    assert active_transactions(project_dir) == ()


def test_replot_publish_failure_restores_figures_summary_and_manifest(
    tmp_path,
    monkeypatch,
    project_copy_factory,
    ensure_task_result,
    read_json,
    write_json,
    run_scan_script,
    make_figures_script,
    make_figures_module,
) -> None:
    project_dir, _ = prepare_scanned_project(
        tmp_path,
        project_copy_factory,
        ensure_task_result,
        read_json,
        write_json,
        run_scan_script,
    )
    assert run_make_figures_main(
        monkeypatch,
        make_figures_module,
        make_figures_script,
        project_dir,
    ) == 0

    figure_dir = project_dir / "numerics" / "figures" / "analysis-301"
    summary_path = project_dir / "numerics" / "analysis-summary-analysis-301.md"
    manifest_path = project_dir / "manifest.json"
    owned_paths = [
        *sorted(figure_dir.iterdir()),
        summary_path,
        manifest_path,
    ]
    before = {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in owned_paths
    }
    transaction_module = importlib.import_module(
        make_figures_module.PublicationTransaction.__module__
    )
    original_replace = transaction_module._rename_no_replace
    failure_injected = False

    def fail_manifest_once(source, destination):
        nonlocal failure_injected
        if destination == manifest_path and not failure_injected:
            failure_injected = True
            raise OSError("injected replot manifest publication failure")
        return original_replace(source, destination)

    monkeypatch.setattr(transaction_module, "_rename_no_replace", fail_manifest_once)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(make_figures_script),
            "--project-dir",
            str(project_dir),
            "--analysis-id",
            "analysis-301",
            "--overwrite",
        ],
    )
    assert make_figures_module.main() == 1
    assert {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in owned_paths
    } == before
    assert active_transactions(project_dir) == ()

    monkeypatch.setattr(transaction_module, "_rename_no_replace", original_replace)
    assert make_figures_module.main() == 0


def test_committed_figure_cleanup_warning_is_success_without_retry(
    tmp_path,
    monkeypatch,
    capsys,
    project_copy_factory,
    ensure_task_result,
    read_json,
    write_json,
    run_scan_script,
    make_figures_script,
    make_figures_module,
) -> None:
    project_dir, _ = prepare_scanned_project(
        tmp_path,
        project_copy_factory,
        ensure_task_result,
        read_json,
        write_json,
        run_scan_script,
    )
    original_commit = make_figures_module.PublicationTransaction.commit

    def commit_then_report_pending_cleanup(self, *args, **kwargs):
        original_commit(self, *args, **kwargs)
        raise make_figures_module.TransactionCommittedCleanupError(
            self.transaction_id,
            OSError("injected cleanup interruption"),
        )

    monkeypatch.setattr(
        make_figures_module.PublicationTransaction,
        "commit",
        commit_then_report_pending_cleanup,
    )

    assert run_make_figures_main(
        monkeypatch,
        make_figures_module,
        make_figures_script,
        project_dir,
    ) == 0
    warning = capsys.readouterr().err
    assert "committed successfully" in warning
    assert "Do not retry" in warning
    assert "injected cleanup interruption" in warning
    figure_dir = project_dir / "numerics" / "figures" / "analysis-301"
    assert any(figure_dir.iterdir())
    manifest = read_json(project_dir / "manifest.json")
    analysis = next(
        entry
        for entry in manifest["artifacts"]["numerics"]["analyses"]
        if entry["analysis_id"] == "analysis-301"
    )
    assert any(path.startswith("numerics/figures/analysis-301/") for path in analysis["files"])
