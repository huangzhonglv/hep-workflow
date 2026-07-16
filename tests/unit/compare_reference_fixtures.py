from __future__ import annotations

import csv
import hashlib
import itertools
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from scripts._dependency_graph import build_dependency_graph
from scripts._workflow_dependencies import (
    calculation_dependency_specs,
    scan_dependency_specs,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
ENUM_REASONS = {
    "manual_tree_algebra_on_tree_task",
    "literature_formula_imported",
    "benchmark_used_as_input",
    "unsupported_manual_loop",
    "result_meta_missing",
    "provenance_blocked",
    "benchmark_validation_failed",
    "benchmark_validation_skipped",
    "derivation_evidence_not_runtime_verified",
    "reference_independence_unverified",
    "boundary_provenance_unverified",
    "synthetic_reference_evidence",
    "input_provenance_unverified",
    "formula_reference_only",
}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return f"sha256:{digest.hexdigest()}"


def rebind_calculation_graph(project_dir: Path, task_id: str = "task-001") -> None:
    """Rebuild a test calculation graph after an intentional fixture mutation."""

    meta_path = project_dir / "calculations" / task_id / "result-meta.json"
    metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    metadata["input_provenance"] = build_dependency_graph(
        project_dir,
        REPO_ROOT,
        calculation_dependency_specs(
            project_dir,
            REPO_ROOT,
            task_id,
            metadata,
        ),
    )
    write_json(meta_path, metadata)


def rebind_scan_graph(project_dir: Path, analysis_id: str = "analysis-001") -> None:
    """Rebuild a test scan graph/checksum without changing its scientific rows."""

    config_path = project_dir / "numerics" / "scan-configs" / f"{analysis_id}.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    result_dir = project_dir / "numerics" / "scan-results" / analysis_id
    meta_path = result_dir / "scan.meta.json"
    metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    metadata["scan_config_snapshot"] = config
    metadata["scan_config_source"] = config_path.read_text(encoding="utf-8")
    metadata["scan_config_sha256"] = hash_file(config_path)
    metadata["rng"] = {
        "algorithm": "numpy.random.PCG64",
        "algorithm_version": "pcg64-v1",
        "substream_scheme": "numpy-seedsequence-v1",
        "seed": config["seed"],
        "substreams": {"smoke": 0, "scan": 1},
        "consumers": [],
    }
    metadata["scan_csv_sha256"] = hash_file(result_dir / "scan.csv")
    metadata["input_provenance"] = build_dependency_graph(
        project_dir,
        REPO_ROOT,
        scan_dependency_specs(
            project_dir,
            REPO_ROOT,
            config_path,
            config,
            producer_script=(
                REPO_ROOT
                / ".agents"
                / "skills"
                / "hep-numerics"
                / "scripts"
                / "run_scan.py"
            ),
        ),
    )
    write_json(meta_path, metadata)


def default_target(target_id: str = "fig-3a", *, kind: str = "figure_curve") -> dict[str, Any]:
    if kind == "formula":
        return {
            "id": target_id,
            "kind": kind,
            "x_param": "M_Zp",
            "y_param": "delta_a_mu",
            "observables": ["delta_a_mu"],
            "fixed": {},
            "constraints_in_paper": [],
            "data_file": f"literature/digitized/{target_id}.json",
            "tolerance": {"kind": "qualitative", "value": None},
        }
    if kind == "exclusion_region":
        target = {
            "id": target_id,
            "kind": kind,
            "x_param": "M_Zp",
            "y_param": "g_prime",
            "observables": ["delta_a_mu"],
            "fixed": {},
            "constraints_in_paper": [],
            "data_file": f"literature/digitized/{target_id}.csv",
            "tolerance": {"kind": "normalized_distance", "value": 0.1},
            "scan_parameters": ["M_Zp", "g_prime"],
            "boundary": {
                "mode": "observable_threshold",
                "observable": "delta_a_mu",
                "operator": "greater_than_or_equal",
                "value": 4.0,
                "value_unit": "dimensionless",
                "component_column": "component_id",
                "reference_order_column": "point_order",
                "reference_closed_column": "is_closed",
                "reference_excluded_probe": {"x": 3.0, "y": 2.0},
            },
            "coordinate_scales": {"M_Zp": 1.0, "g_prime": 1.0},
        }
        target["normalization"] = normalization_for_target(target)
        return target
    if kind == "scan_table":
        target = {
            "id": target_id,
            "kind": kind,
            "x_param": "M_Zp",
            "y_param": "g_prime",
            "match_columns": ["M_Zp", "g_prime"],
            "observables": ["delta_a_mu"],
            "fixed": {},
            "constraints_in_paper": [],
            "data_file": f"literature/digitized/{target_id}.csv",
            "tolerance": {"kind": "relative", "value": 0.01},
            "scan_parameters": ["M_Zp", "g_prime"],
        }
        target["normalization"] = normalization_for_target(target)
        return target
    if kind == "parametric_curve":
        target = {
            "id": target_id,
            "kind": kind,
            "x_param": "M_Zp",
            "y_param": "delta_a_mu",
            "curve_parameter": "M_Zp",
            "observables": ["delta_a_mu"],
            "fixed": {},
            "constraints_in_paper": [],
            "data_file": f"literature/digitized/{target_id}.csv",
            "tolerance": {"kind": "normalized_distance", "value": 0.01},
            "scan_parameters": ["M_Zp"],
            "parameter_domain": {"parameter_min": 1.0, "parameter_max": 3.0},
            "curve_representation": "ordered_parametric_xy",
            "curve_closed": False,
            "coordinate_scales": {"M_Zp": 1.0, "delta_a_mu": 1.0},
        }
        target["normalization"] = normalization_for_target(target)
        return target
    if kind in {"benchmark_point", "keyed_benchmark_set"}:
        target = {
            "id": target_id,
            "kind": kind,
            "x_param": "M_Zp",
            "y_param": "g_prime",
            "match_columns": ["M_Zp", "g_prime"],
            "observables": ["delta_a_mu"],
            "fixed": {},
            "constraints_in_paper": [],
            "data_file": f"literature/digitized/{target_id}.csv",
            "tolerance": {"kind": "relative", "value": 0.01},
            "scan_parameters": ["M_Zp", "g_prime"],
        }
        target["normalization"] = normalization_for_target(target)
        return target
    target = {
        "id": target_id,
        "kind": kind,
        "x_param": "M_Zp",
        "y_param": "delta_a_mu",
        "observables": ["delta_a_mu"],
        "fixed": {},
        "constraints_in_paper": [],
        "data_file": f"literature/digitized/{target_id}.csv",
        "tolerance": {"kind": "relative", "value": 0.01},
        "scan_parameters": ["M_Zp"],
        "comparison_domain": {"x_min": 1.0, "x_max": 3.0},
        "curve_representation": "single_valued_y_of_x",
    }
    target["normalization"] = normalization_for_target(target)
    return target


def normalization_for_target(target: dict[str, Any]) -> dict[str, Any]:
    target_id = str(target["id"])
    columns = {
        str(target.get("x_param")),
        str(target.get("y_param")),
        *[str(item) for item in target.get("match_columns", [])],
    }
    if target.get("kind") == "parametric_curve":
        columns.add(str(target.get("curve_parameter")))
    if target.get("kind") != "exclusion_region":
        columns.update(str(item) for item in target.get("observables", []))
    elif target.get("boundary", {}).get("mode") == "observable_threshold":
        columns.add(str(target["boundary"]["observable"]))
    columns.discard("None")
    units = {
        column: ("GeV" if column == "M_Zp" else "dimensionless")
        for column in sorted(columns)
    }
    conversions = {
        column: {"operation": "linear", "factor": 1.0, "offset": 0.0}
        for column in sorted(columns)
    }
    fixed_parameters = {}
    for name, value in sorted(target.get("fixed", {}).items()):
        if isinstance(value, str) or value is None or isinstance(value, bool):
            unit = "categorical"
        elif name.startswith(("M_", "m_", "v_")):
            unit = "GeV"
        else:
            unit = "dimensionless"
        fixed_parameters[name] = {
            "source_value": value,
            "source_unit": unit,
            "canonical_value": value,
            "canonical_unit": unit,
            "operation": "linear",
            "factor": 1.0,
            "offset": 0.0,
        }
    return {
        "status": "canonical",
        "method": "identity",
        "source_units": dict(units),
        "canonical_units": dict(units),
        "conversions": conversions,
        "fixed_parameters": fixed_parameters,
        "acquisition": {
            "source_type": "synthetic_fixture",
            "paper_id": "arxiv:2601.01234v2",
            "source_locator": f"pytest:{target_id}",
            "method": "synthetic_fixture",
            "acquired_at": "2026-07-13T00:00:00Z",
            "notes": "Independent synthetic reference fixture.",
        },
        "source_data_file": f"literature/digitized/{target_id}.raw.csv",
        "record_file": f"literature/digitized/{target_id}.normalization.json",
    }


def enrich_target(target: dict[str, Any]) -> dict[str, Any]:
    enriched = json.loads(json.dumps(target))
    kind = enriched.get("kind")
    if kind == "formula":
        return enriched
    if "scan_parameters" not in enriched:
        enriched["scan_parameters"] = (
            [str(item) for item in enriched.get("match_columns", [])]
            or [str(enriched["x_param"])]
        )
    if kind == "benchmark_point" and "match_columns" not in enriched:
        enriched["match_columns"] = [enriched["x_param"], enriched["y_param"]]
    if kind == "figure_curve":
        enriched.setdefault("comparison_domain", {"x_min": 1.0, "x_max": 3.0})
        enriched.setdefault("curve_representation", "single_valued_y_of_x")
    if kind == "parametric_curve":
        enriched.setdefault(
            "parameter_domain", {"parameter_min": 1.0, "parameter_max": 3.0}
        )
        enriched.setdefault("curve_representation", "ordered_parametric_xy")
        enriched.setdefault("curve_closed", False)
    enriched.setdefault("normalization", normalization_for_target(enriched))
    return enriched


def write_normalized_reference(
    project_dir: Path,
    target: dict[str, Any],
    text: str,
) -> None:
    """Write a raw/canonical fixture pair and its exact integrity record."""

    normalization = target["normalization"]
    source_path = project_dir / normalization["source_data_file"]
    canonical_path = project_dir / target["data_file"]
    source_path.parent.mkdir(parents=True, exist_ok=True)
    canonical_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text(text, encoding="utf-8")
    canonical_path.write_text(text, encoding="utf-8")
    write_json(
        project_dir / normalization["record_file"],
        {
            "status": normalization["status"],
            "method": normalization["method"],
            "source_data_file": normalization["source_data_file"],
            "canonical_data_file": target["data_file"],
            "source_units": normalization["source_units"],
            "canonical_units": normalization["canonical_units"],
            "conversions": normalization["conversions"],
            "fixed_parameters": normalization["fixed_parameters"],
            "acquisition": normalization["acquisition"],
            "source_checksum": hash_file(source_path),
            "canonical_checksum": hash_file(canonical_path),
        },
    )


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

    model_spec = json.loads(
        (REPO_ROOT / "schemas" / "examples" / "model-spec.example.json").read_text(
            encoding="utf-8"
        )
    )
    model_spec["model_name"] = "Synthetic Reproduction Model"
    write_json(project_dir / "model" / "model-spec.json", model_spec)
    model_checksum = hash_file(project_dir / "model" / "model-spec.json")
    generated_default_manifest = manifest_text is None
    if manifest_text is None:
        manifest_text = json.dumps(
            {
                "manifest_version": 2,
                "project_name": project_name,
                "created": "2026-07-13",
                "last_updated": "2026-07-13",
                "active_model_version": "v1",
                "artifacts": {
                    "idea": {
                        "status": "not_started",
                        "files": [],
                        "produced_by": None,
                        "timestamp": None,
                    },
                    "model": {
                        "status": "done",
                        "version": "v1",
                        "files": ["model/model-spec.json"],
                        "checksum": model_checksum,
                        "produced_by": "pytest-fixture",
                        "timestamp": "2026-07-13T00:00:00Z",
                    },
                    "calculations": {
                        "status": "not_started",
                        "completed_tasks": [],
                        "pending_tasks": [],
                        "depends_on": {
                            "model": {"version": "v1", "checksum": model_checksum}
                        },
                        "produced_by": None,
                        "timestamp": None,
                    },
                    "constraints": {
                        "status": "not_started",
                        "files": [],
                        "depends_on": {
                            "model": {"version": "v1", "checksum": model_checksum}
                        },
                        "produced_by": None,
                        "timestamp": None,
                    },
                    "numerics": {
                        "status": "not_started",
                        "files": [],
                        "analyses": [],
                        "produced_by": None,
                        "timestamp": None,
                    },
                },
                "history": [],
            }
        ) + "\n"
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
    write_json(
        project_dir / "constraints" / "constraints-data.json",
        {
            "model_name": "Synthetic Reproduction Model",
            "model_version": "v1",
            "parameters": ["M_Zp", "g_prime"],
            "constraints": [
                {
                    "id": "c-001",
                    "name": "Synthetic permissive upper limit",
                    "type": "upper_limit",
                    "observable": "delta_a_mu",
                    "limit_value": 1000000000.0,
                    "source": "pytest synthetic fixture",
                    "implementation_status": "direct",
                    "notes": "Classifies every finite synthetic point as allowed.",
                    "computed_by": {"type": "task", "task_id": "task-001"},
                }
            ],
        },
    )

    task_dir = project_dir / "calculations" / "task-001"
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "request.md").write_text(
        "# Request\n\nSynthetic comparison fixture request.\n",
        encoding="utf-8",
    )
    (task_dir / "result-summary.md").write_text(
        "# Result Summary\n\n## Benchmark Verification\n\nSynthetic fixture.\n",
        encoding="utf-8",
    )
    source_wl = task_dir / "result.wl"
    source_wl.write_text(
        "projected = Projector[amplitude, MagneticFormFactor];\n"
        "loopResult = LoopIntegrate[projected, k];\n"
        "finalResult = LoopRefine[loopResult];\n",
        encoding="utf-8",
    )
    python_file = task_dir / "result-python.py"
    python_file.write_text(
        "def compute_delta_a_mu(M_Zp):\n"
        "    return 2.0 * M_Zp\n",
        encoding="utf-8",
    )

    if include_result_meta:
        methods = ["LoopIntegrate"] if provenance == "package_x_derived" else []
        metadata = {
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
            "package_x_methods": methods,
            "provenance_notes": "Synthetic fixture.",
            "benchmark_status": "pass",
            "depends_on": {
                "model_version": "v1",
                "model_checksum": model_checksum,
            },
        }
        if provenance == "package_x_derived":
            metadata["derivation_evidence"] = {
                "source_wl_sha256": hash_file(source_wl),
                "python_file_sha256": hash_file(python_file),
                "wolfram_result_symbol": "finalResult",
                "observable": "delta_a_mu",
                "python_function": "compute_delta_a_mu",
                "package_x_methods": methods,
            }
        metadata["input_provenance"] = build_dependency_graph(
            project_dir,
            REPO_ROOT,
            calculation_dependency_specs(
                project_dir,
                REPO_ROOT,
                "task-001",
                metadata,
            ),
        )
        write_json(task_dir / "result-meta.json", metadata)

    if targets is None:
        targets = [default_target()]
    targets = [enrich_target(target) for target in targets]

    write_json(
        project_dir / "literature" / "repro-targets.json",
        {"paper_id": "arxiv:2601.01234v2", "targets": targets},
    )
    paper_extract = json.loads(
        (REPO_ROOT / "schemas" / "examples" / "paper-extract.example.json").read_text(
            encoding="utf-8"
        )
    )
    paper_extract["paper_id"] = "arxiv:2601.01234v2"
    paper_extract["scan_config_hints"] = [
        {
            "target_id": str(target["id"]),
            "scan_parameters": [
                {
                    "canonical_name": str(name),
                    "range": [1.0, 6.0],
                    "scale": "linear",
                }
                for name in target.get("scan_parameters", [])
            ],
            "fixed_parameters": dict(target.get("fixed", {})),
            "constraints_used": list(target.get("constraints_in_paper", [])),
            "grid": {str(name): 3 for name in target.get("scan_parameters", [])},
            "missing_fields": [],
            "source_anchor": f"pytest:{target['id']}",
        }
        for target in targets
        if target.get("kind") != "formula"
    ]
    write_json(project_dir / "literature" / "paper-extract.json", paper_extract)
    digitized_dir = project_dir / "literature" / "digitized"
    digitized_dir.mkdir(parents=True, exist_ok=True)
    for target in targets:
        data_path = project_dir / target["data_file"]
        data_path.parent.mkdir(parents=True, exist_ok=True)
        kind = target["kind"]
        if kind == "formula":
            write_json(
                data_path,
                {
                    "paper_id": "arxiv:2601.01234v2",
                    "target_id": target["id"],
                    "expression": "delta_a_mu = 2 M_Zp",
                    "source_locator": "synthetic equation 1",
                    "acquired_at": "2026-07-13T00:00:00Z",
                },
            )
        elif kind == "exclusion_region":
            write_normalized_reference(
                project_dir,
                target,
                "M_Zp,g_prime,delta_a_mu,component_id,point_order,is_closed\n"
                "2,2,4,outer,0,false\n2,4,4,outer,1,false\n2,6,4,outer,2,false\n",
            )
        elif kind == "benchmark_point":
            write_normalized_reference(
                project_dir,
                target,
                "M_Zp,g_prime,delta_a_mu\n1,2,2.002\n",
            )
        else:
            columns = (
                "M_Zp,g_prime,delta_a_mu"
                if kind in {"scan_table", "keyed_benchmark_set"}
                else "M_Zp,delta_a_mu"
            )
            rows = (
                "1.0,2.0,2.002\n2.0,4.0,4.004\n3.0,6.0,6.006\n"
                if "g_prime" in columns
                else "1.0,2.002\n2.0,4.004\n3.0,6.006\n"
            )
            write_normalized_reference(project_dir, target, f"{columns}\n{rows}")

    non_formula_targets = [target for target in targets if target["kind"] != "formula"]
    scan_parameter_names: list[str] = []
    for target in non_formula_targets:
        for name in target.get("scan_parameters", []):
            if name not in scan_parameter_names:
                scan_parameter_names.append(str(name))
    if not scan_parameter_names:
        scan_parameter_names = ["M_Zp"]

    precomputed_boundary = any(
        target["kind"] == "exclusion_region"
        and target.get("boundary", {}).get("mode") == "precomputed_boundary"
        for target in targets
    )
    auxiliary_observables = (
        [
            "is_boundary",
            "component_id",
            "boundary_order",
            "boundary_closed",
            "region_status",
        ]
        if precomputed_boundary
        else []
    )
    custom_lines = [
        "def synthetic_delta_a_mu(**parameters):",
        "    return 2.0 * float(parameters['M_Zp'])",
        "",
        "def compute_delta_a_mu_custom(**parameters):",
        "    return 2.0 * float(parameters['M_Zp'])",
    ]
    for name in auxiliary_observables:
        custom_lines.extend(
            [
                "",
                f"def synthetic_{name}(**parameters):",
                "    return 0.0",
            ]
        )
    custom_path = project_dir / "numerics" / "custom_observables.py"
    custom_path.parent.mkdir(parents=True, exist_ok=True)
    custom_path.write_text("\n".join(custom_lines) + "\n", encoding="utf-8")

    observable_bindings = [
        {
            "observable": "delta_a_mu",
            "source": (
                {"type": "task", "task_id": "task-001"}
                if include_result_meta
                else {
                    "type": "custom",
                    "function": "synthetic_delta_a_mu",
                    "canonical_unit": "dimensionless",
                }
            ),
        }
    ]
    observable_bindings.extend(
        {
            "observable": name,
            "source": {
                "type": "custom",
                "function": f"synthetic_{name}",
                "canonical_unit": "dimensionless",
            },
        }
        for name in auxiliary_observables
    )

    axis_values: dict[str, list[float]] = {
        "M_Zp": [1.0, 2.0, 3.0],
        "g_prime": [2.0, 4.0, 6.0],
    }
    for name in scan_parameter_names:
        axis_values.setdefault(name, [1.0, 2.0, 3.0])
    scan_config = {
        "analysis_id": "analysis-001",
        "model_name": "Synthetic Reproduction Model",
        "depends_on": {
            "model_version": "v1",
            "model_checksum": model_checksum,
            "task_ids": ["task-001"] if include_result_meta else [],
        },
        "scan_parameters": [
            {
                "canonical_name": name,
                "range": [axis_values[name][0], axis_values[name][-1]],
                "grid": len(axis_values[name]),
                "scale": "linear",
            }
            for name in scan_parameter_names
        ],
        "fixed_parameters": [],
        "observables": observable_bindings,
        "constraints_used": ["c-001"],
        "figures": [],
        "allow_formula_fallback": provenance
        in {"literature_formula_imported", "manual_tree_algebra"},
        "seed": 0,
        "parallelism": 1,
    }
    scan_config_path = (
        project_dir / "numerics" / "scan-configs" / "analysis-001.json"
    )
    write_json(scan_config_path, scan_config)

    scan_dir = project_dir / "numerics" / "scan-results" / "analysis-001"
    scan_dir.mkdir(parents=True, exist_ok=True)
    columns = [*scan_parameter_names, "delta_a_mu", *auxiliary_observables]
    columns.extend(
        [
            "c-001_verdict",
            "c-001_margin",
            "c-001_chi2",
            "c-001_skip_reason",
        ]
    )
    scan_rows: list[dict[str, Any]] = []
    for point in itertools.product(*(axis_values[name] for name in scan_parameter_names)):
        row = dict(zip(scan_parameter_names, point, strict=True))
        mass = float(row.get("M_Zp", 1.0))
        coupling = float(row.get("g_prime", 2.0 * mass))
        is_boundary = coupling == 2.0 * mass
        row["delta_a_mu"] = 2.0 * mass
        if precomputed_boundary:
            row.update(
                {
                    "is_boundary": 1.0 if is_boundary else 0.0,
                    "component_id": 0.0,
                    "boundary_order": mass - 1.0 if is_boundary else 0.0,
                    "boundary_closed": 0.0,
                    "region_status": 1.0,
                }
            )
        row.update(
            {
                "c-001_verdict": "allowed",
                "c-001_margin": 1.0,
                "c-001_chi2": "",
                "c-001_skip_reason": "",
            }
        )
        scan_rows.append(row)

    scan_csv_path = scan_dir / "scan.csv"
    with scan_csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(scan_rows)

    summary_path = project_dir / "numerics" / "analysis-summary-analysis-001.md"
    summary_path.write_text(
        "# Synthetic Analysis\n\n"
        "- Analysis ID: `analysis-001`\n"
        f"- Total points: {len(scan_rows)}\n"
        f"- Allowed: {len(scan_rows)}\n"
        "- Excluded: 0\n"
        "- Skipped: 0\n",
        encoding="utf-8",
    )
    scan_graph = build_dependency_graph(
        project_dir,
        REPO_ROOT,
        scan_dependency_specs(
            project_dir,
            REPO_ROOT,
            scan_config_path,
            scan_config,
            producer_script=(
                REPO_ROOT
                / ".agents"
                / "skills"
                / "hep-numerics"
                / "scripts"
                / "run_scan.py"
            ),
        ),
    )
    scan_config_source = scan_config_path.read_text(encoding="utf-8")
    write_json(
        scan_dir / "scan.meta.json",
        {
            "analysis_id": "analysis-001",
            "history_action": "numerics_analysis_complete",
            "scan_config_snapshot": scan_config,
            "scan_config_source": scan_config_source,
            "scan_config_sha256": hash_file(scan_config_path),
            "model_version": "v1",
            "model_checksum": model_checksum,
            "seed": 0,
            "rng": {
                "algorithm": "numpy.random.PCG64",
                "algorithm_version": "pcg64-v1",
                "substream_scheme": "numpy-seedsequence-v1",
                "seed": 0,
                "substreams": {"smoke": 0, "scan": 1},
                "consumers": [],
            },
            "started_at": "2026-07-13T00:00:00Z",
            "finished_at": "2026-07-13T00:00:01Z",
            "timing_seconds": 1.0,
            "timing": {
                "started_at": "2026-07-13T00:00:00Z",
                "finished_at": "2026-07-13T00:00:01Z",
                "seconds": 1.0,
            },
            "n_points": len(scan_rows),
            "n_allowed": len(scan_rows),
            "n_excluded": 0,
            "n_skipped": 0,
            "environment": {"python": "pytest-fixture"},
            "formula_fallbacks": [],
            "warnings": [],
            "scan_csv_sha256": hash_file(scan_csv_path),
            "input_provenance": scan_graph,
        },
    )
    if generated_default_manifest:
        manifest_path = project_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        analysis_files = [
            "numerics/analysis-summary-analysis-001.md",
            "numerics/scan-configs/analysis-001.json",
            "numerics/scan-results/analysis-001/scan.csv",
            "numerics/scan-results/analysis-001/scan.meta.json",
        ]
        if any(
            isinstance(binding.get("source"), dict)
            and binding["source"].get("type") == "custom"
            for binding in observable_bindings
        ):
            analysis_files.append("numerics/custom_observables.py")
        analysis_entry = {
            "analysis_id": "analysis-001",
            "status": "done",
            "files": sorted(analysis_files),
            "depends_on": {
                "model": {"version": "v1", "checksum": model_checksum},
                "calculations": {
                    "tasks": sorted(scan_config["depends_on"]["task_ids"]),
                    "model_version": "v1",
                },
                "constraints": {
                    "checksum": hash_file(
                        project_dir / "constraints" / "constraints-data.json"
                    )
                },
            },
            "produced_by": "pytest-fixture",
            "timestamp": "2026-07-13T00:00:01Z",
        }
        manifest["artifacts"]["numerics"] = {
            "status": "done",
            "files": list(analysis_entry["files"]),
            "analyses": [analysis_entry],
            "produced_by": analysis_entry["produced_by"],
            "timestamp": analysis_entry["timestamp"],
        }
        manifest["history"].append(
            {
                "action": "numerics_analysis_complete",
                "analysis_id": "analysis-001",
                "event_id": "0" * 32,
                "timestamp": analysis_entry["timestamp"],
                "by": analysis_entry["produced_by"],
                "note": "analysis_id=analysis-001 synthetic comparison fixture",
            }
        )
        write_json(manifest_path, manifest)
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


def mark_scan_hint_blocked(project_dir: Path, target_id: str) -> None:
    path = project_dir / "literature" / "paper-extract.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    hint = next(
        item
        for item in payload["scan_config_hints"]
        if item["target_id"] == target_id
    )
    hint["missing_fields"] = ["pytest_missing_scan_hint"]
    write_json(path, payload)


def load_result(project_dir: Path, repro_id: str) -> dict[str, Any]:
    path = project_dir / "reproduction" / "runs" / repro_id / "reproduction-result.json"
    return json.loads(path.read_text(encoding="utf-8"))
