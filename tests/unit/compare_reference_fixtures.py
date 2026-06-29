from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


ENUM_REASONS = {
    "manual_tree_algebra_on_tree_task",
    "literature_formula_imported",
    "benchmark_used_as_input",
    "unsupported_manual_loop",
    "result_meta_missing",
    "provenance_blocked",
}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def default_target(target_id: str = "fig-3a", *, kind: str = "figure_curve") -> dict[str, Any]:
    if kind == "exclusion_region":
        return {
            "id": target_id,
            "kind": kind,
            "x_param": "M_Zp",
            "y_param": "g_prime",
            "observables": ["delta_a_mu"],
            "fixed": {},
            "constraints_in_paper": [],
            "data_file": f"literature/digitized/{target_id}.csv",
            "tolerance": {"kind": "absolute", "value": 0.1},
        }
    return {
        "id": target_id,
        "kind": kind,
        "x_param": "M_Zp",
        "y_param": "delta_a_mu",
        "observables": ["delta_a_mu"],
        "fixed": {},
        "constraints_in_paper": [],
        "data_file": f"literature/digitized/{target_id}.csv",
        "tolerance": {"kind": "relative", "value": 0.01},
    }


def make_compare_project(
    tmp_path: Path,
    *,
    project_name: str = "minimal-repro",
    targets: list[dict[str, Any]] | None = None,
    task_type: str = "loop",
    loop_order: int = 1,
    provenance: str = "package_x_derived",
    benchmark_used_as_input: bool = False,
    include_result_meta: bool = True,
    manifest_text: str | None = None,
) -> Path:
    project_dir = tmp_path / "workspace" / "projects" / project_name
    project_dir.mkdir(parents=True, exist_ok=True)
    if manifest_text is None:
        manifest_text = json.dumps({"project_name": project_name}) + "\n"
    (project_dir / "manifest.json").write_text(manifest_text, encoding="utf-8")

    write_json(
        project_dir / "model" / "calc-tasks.json",
        {
            "model_name": "Synthetic Reproduction Model",
            "model_version": "v1",
            "tasks": [
                {
                    "task_id": "task-001",
                    "title": "Synthetic observable",
                    "type": task_type,
                    "loop_order": loop_order,
                    "process": "mu -> mu",
                    "lagrangian_terms": ["L_int = g_prime Zp_mu bar{mu} gamma^mu mu"],
                    "external_particles": {
                        "incoming": [{"particle": "mu", "momentum": "p1"}],
                        "outgoing": [{"particle": "mu", "momentum": "p2"}],
                    },
                    "loop_particles": [{"propagator": "mu", "mass": "m_mu"}],
                    "target_quantity": "delta_a_mu",
                    "on_shell": True,
                    "priority": "high",
                    "notes": "Synthetic test task.",
                }
            ],
        },
    )

    if include_result_meta:
        write_json(
            project_dir / "calculations" / "task-001" / "result-meta.json",
            {
                "task_id": "task-001",
                "observable": "delta_a_mu",
                "python_function": "compute_delta_a_mu",
                "python_file": "result-python.py",
                "parameters": [{"canonical_name": "M_Zp", "role": "scan", "unit": "GeV"}],
                "return_value": {
                    "name": "delta_a_mu",
                    "unit": "dimensionless",
                    "description": "Synthetic observable.",
                },
                "translation_status": "complete",
                "translation_notes": "Synthetic fixture.",
                "source_wl": "result.wl",
                "calculation_provenance": provenance,
                "benchmark_used_as_input": benchmark_used_as_input,
                "package_x_methods": ["LoopIntegrate"] if provenance == "package_x_derived" else [],
                "provenance_notes": "Synthetic fixture.",
                "benchmark_status": "pass",
                "depends_on": {"model_version": "v1", "model_checksum": "sha256:abc123"},
            },
        )

    if targets is None:
        targets = [default_target()]

    write_json(
        project_dir / "literature" / "repro-targets.json",
        {"paper_id": "arxiv:2601.01234v2", "targets": targets},
    )
    digitized_dir = project_dir / "literature" / "digitized"
    digitized_dir.mkdir(parents=True, exist_ok=True)
    for target in targets:
        data_path = project_dir / target["data_file"]
        data_path.parent.mkdir(parents=True, exist_ok=True)
        if target["kind"] == "exclusion_region":
            data_path.write_text("M_Zp,g_prime\n1,2\n2,4\n3,6\n", encoding="utf-8")
        else:
            data_path.write_text("M_Zp,delta_a_mu\n1,2\n2,4\n3,6\n", encoding="utf-8")

    scan_dir = project_dir / "numerics" / "scan-results" / "analysis-001"
    scan_dir.mkdir(parents=True, exist_ok=True)
    scan_rows = [
        "M_Zp,g_prime,delta_a_mu",
        "1,2,2",
        "2,4,4",
        "3,6,6",
    ]
    (scan_dir / "scan.csv").write_text("\n".join(scan_rows) + "\n", encoding="utf-8")
    (scan_dir / "scan.meta.json").write_text('{"synthetic": true}\n', encoding="utf-8")
    return project_dir


def run_compare(repo_root: Path, project_dir: Path, repro_id: str, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(repo_root / "scripts" / "compare_to_reference.py"),
            "--project-dir",
            str(project_dir),
            "--analysis-id",
            "analysis-001",
            "--repro-id",
            repro_id,
            *extra,
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )


def load_result(project_dir: Path, repro_id: str) -> dict[str, Any]:
    path = project_dir / "reproduction" / "runs" / repro_id / "reproduction-result.json"
    return json.loads(path.read_text(encoding="utf-8"))
