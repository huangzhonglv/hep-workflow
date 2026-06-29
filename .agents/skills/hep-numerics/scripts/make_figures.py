#!/usr/bin/env python3
"""Generate hep-numerics figures from scan outputs."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import textwrap
from datetime import datetime
from pathlib import Path
from typing import Any

_CACHE_ROOT = Path(tempfile.gettempdir()) / "hep-numerics-mpl-cache"
_CACHE_ROOT.mkdir(parents=True, exist_ok=True)
(_CACHE_ROOT / "matplotlib").mkdir(parents=True, exist_ok=True)
(_CACHE_ROOT / "xdg-cache").mkdir(parents=True, exist_ok=True)
os.environ.setdefault("MPLCONFIGDIR", str(_CACHE_ROOT / "matplotlib"))
os.environ.setdefault("XDG_CACHE_HOME", str(_CACHE_ROOT / "xdg-cache"))

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D
from matplotlib.patches import Patch


FIGURE_COLORS = [
    "#D1495B",
    "#00798C",
    "#EDAe49",
    "#30638E",
    "#003D5B",
    "#8F2D56",
]

TEX_SPECIAL_CHARACTERS = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}


def load_manifest_helpers() -> object:
    """Load the sibling manifest helper module from disk."""

    helper_path = Path(__file__).resolve().parent / "_manifest.py"
    spec = importlib.util.spec_from_file_location("hep_numerics_manifest_helpers", helper_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load manifest helpers from {helper_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


MANIFEST = load_manifest_helpers()


def load_run_scan_module() -> object:
    """Load the sibling run_scan helpers so summary generation stays aligned."""

    script_path = Path(__file__).resolve()
    target = script_path.parent / "run_scan.py"
    spec = importlib.util.spec_from_file_location("hep_numerics_make_figures_run_scan", target)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load run_scan helpers from {target}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


RUN_SCAN = load_run_scan_module()


def resolve_repo_root() -> Path:
    """Infer the repository root from the current skill layout."""

    script_dir = Path(__file__).resolve().parent
    skill_dir = script_dir.parent
    skills_dir = skill_dir.parent
    platform_dir = skills_dir.parent

    if (
        script_dir.name == "scripts"
        and skill_dir.name == "hep-numerics"
        and skills_dir.name == "skills"
        and platform_dir.name in {".agents", ".claude"}
    ):
        return platform_dir.parent

    raise RuntimeError(
        "Cannot infer repository root from the current skill layout. "
        "Expected the script under "
        "<repo>/.agents/skills/hep-numerics/scripts/ or "
        "<repo>/.claude/skills/hep-numerics/scripts/."
    )


def load_json(path: Path) -> Any:
    """Load JSON from disk."""

    return json.loads(path.read_text(encoding="utf-8"))


def find_project_dir(start: Path) -> Path:
    """Walk upward until a project root with manifest.json is found."""

    candidate = start.resolve()
    for current in (candidate, *candidate.parents):
        if (current / "manifest.json").exists():
            return current
    raise FileNotFoundError(
        f"could not infer project directory from {start}; no manifest.json found"
    )


def build_parser() -> argparse.ArgumentParser:
    """Construct the CLI argument parser."""

    parser = argparse.ArgumentParser(
        description=(
            "Generate hep-numerics figures for numerics/scan-results/{analysis_id}/ "
            "from an analysis ID or a scan-config path."
        )
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--scan-config",
        type=Path,
        help="Path to a scan-config JSON file. The project root is inferred from it.",
    )
    group.add_argument(
        "--analysis-id",
        help="Analysis identifier under numerics/scan-configs/, for example analysis-001.",
    )
    parser.add_argument(
        "--project-dir",
        type=Path,
        help="Workspace project directory. Required together with --analysis-id.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Allow overwriting existing rendered figures.",
    )
    return parser


def resolve_cli_inputs(args: argparse.Namespace) -> tuple[Path, Path, str]:
    """Resolve the project directory, scan-config path, and analysis ID."""

    if args.scan_config is not None:
        scan_config_path = args.scan_config.resolve()
        project_dir = find_project_dir(scan_config_path.parent)
        scan_config = load_json(scan_config_path)
        analysis_id = scan_config.get("analysis_id")
        if not isinstance(analysis_id, str) or not analysis_id:
            raise ValueError(
                f"scan-config at {scan_config_path} does not contain a valid analysis_id"
            )
        return project_dir, scan_config_path, analysis_id

    if args.project_dir is None:
        raise ValueError("--project-dir is required when using --analysis-id")

    project_dir = args.project_dir.resolve()
    analysis_id = args.analysis_id
    scan_config_path = project_dir / "numerics" / "scan-configs" / f"{analysis_id}.json"
    return project_dir, scan_config_path, analysis_id


def latex_available() -> bool:
    """Probe whether matplotlib can use a system LaTeX installation."""

    checker = getattr(matplotlib, "checkdep_usetex", None)
    if checker is not None:
        try:
            return bool(checker(True))
        except TypeError:
            try:
                return bool(checker())
            except Exception:
                pass
        except Exception:
            pass

    return all(shutil.which(command) for command in ("latex", "dvipng", "gs"))


def configure_matplotlib() -> bool:
    """Set figure-wide plotting defaults and return whether usetex is active."""

    use_tex = latex_available()
    plt.rcParams.update(
        {
            "text.usetex": use_tex,
            "font.size": 12,
            "axes.labelsize": 14,
            "axes.titlesize": 14,
            "xtick.labelsize": 12,
            "ytick.labelsize": 12,
            "legend.fontsize": 10,
        }
    )
    return use_tex


def load_inputs(
    *,
    project_dir: Path | None = None,
    analysis_id: str | None = None,
    scan_config_path: Path | None = None,
) -> dict[str, Any]:
    """Load the config, scan CSV, and metadata required for figure generation."""

    if scan_config_path is None:
        if project_dir is None or analysis_id is None:
            raise ValueError(
                "load_inputs requires either scan_config_path or both project_dir and analysis_id"
            )
        scan_config_path = (
            project_dir.resolve() / "numerics" / "scan-configs" / f"{analysis_id}.json"
        )
    else:
        scan_config_path = scan_config_path.resolve()

    if project_dir is None:
        project_dir = find_project_dir(scan_config_path.parent)
    else:
        project_dir = project_dir.resolve()

    scan_config = load_json(scan_config_path)
    if analysis_id is None:
        analysis_id = scan_config["analysis_id"]

    scan_csv_path = project_dir / "numerics" / "scan-results" / analysis_id / "scan.csv"
    if not scan_csv_path.exists():
        raise FileNotFoundError(f"scan.csv not found: {scan_csv_path}")
    scan_meta_path = project_dir / "numerics" / "scan-results" / analysis_id / "scan.meta.json"
    if not scan_meta_path.exists():
        raise FileNotFoundError(f"scan.meta.json not found: {scan_meta_path}")

    model_spec_path = project_dir / "model" / "model-spec.json"
    constraints_path = project_dir / "constraints" / "constraints-data.json"
    manifest_path = project_dir / "manifest.json"
    model_spec = load_json(model_spec_path)
    constraints_data = load_json(constraints_path)
    manifest = load_json(manifest_path)
    scan_meta = load_json(scan_meta_path)
    dataframe = pd.read_csv(scan_csv_path)

    return {
        "repo_root": resolve_repo_root(),
        "project_dir": project_dir,
        "analysis_id": analysis_id,
        "manifest_path": manifest_path,
        "manifest": manifest,
        "scan_config_path": scan_config_path,
        "scan_config": scan_config,
        "scan_csv_path": scan_csv_path,
        "scan_meta_path": scan_meta_path,
        "scan_meta": scan_meta,
        "dataframe": dataframe,
        "model_spec": model_spec,
        "constraints_data": constraints_data,
        "model_parameters_by_name": {
            parameter["name"]: parameter for parameter in model_spec.get("parameters", [])
        },
        "constraints_by_id": {
            constraint["id"]: constraint for constraint in constraints_data.get("constraints", [])
        },
    }


def get_parameter_scale(scan_config: dict[str, Any], name: str) -> str | None:
    """Return the configured scan scale for one parameter, if available."""

    for parameter in scan_config.get("scan_parameters", []):
        if parameter["canonical_name"] == name:
            return parameter.get("scale")
    return None


def sanitize_name(name: str) -> str:
    """Convert a logical identifier into a filesystem-friendly fragment."""

    return name.replace("/", "-").replace(" ", "_")


def tex_escape(text: str) -> str:
    """Escape plain text for matplotlib's usetex mode."""

    if not plt.rcParams.get("text.usetex", False):
        return text
    return "".join(TEX_SPECIAL_CHARACTERS.get(character, character) for character in text)


def wrapped_legend_label(text: str, *, width: int = 52) -> str:
    """Wrap long legend labels so they do not expand the figure canvas."""

    wrapped = textwrap.wrap(text, width=width, break_long_words=False)
    return tex_escape("\n".join(wrapped) if wrapped else text)


def constraint_legend_label(constraint: dict[str, Any], fallback_id: str) -> str:
    """Build a compact constraint legend label from stable local metadata."""

    constraint_id = constraint.get("id", fallback_id)
    name = constraint.get("name", fallback_id)
    return wrapped_legend_label(f"{constraint_id}: {name}")


def label_with_unit(name: str, latex: str | None, unit: str | None) -> str:
    """Format a parameter/observable label with optional latex and unit."""

    label = f"${latex}$" if latex else tex_escape(name)
    if unit and unit != "dimensionless":
        return f"{label} [{tex_escape(unit)}]"
    return label


def parameter_label(inputs: dict[str, Any], name: str) -> str:
    """Build the axis label for a model parameter."""

    parameter = inputs["model_parameters_by_name"].get(name, {})
    return label_with_unit(name, parameter.get("latex"), parameter.get("unit"))


def observable_label(inputs: dict[str, Any], name: str) -> str:
    """Build the axis label for an observable."""

    unit = None
    for constraint in inputs["constraints_by_id"].values():
        if constraint.get("observable") == name and constraint.get("unit"):
            unit = constraint["unit"]
            break
    return label_with_unit(name, None, unit)


def apply_axis_scale(ax: Any, scan_config: dict[str, Any], x: str, y: str | None = None) -> None:
    """Apply log/linear axis scales from the scan-config."""

    x_scale = get_parameter_scale(scan_config, x)
    if x_scale == "log":
        ax.set_xscale("log")

    if y is not None:
        y_scale = get_parameter_scale(scan_config, y)
        if y_scale == "log":
            ax.set_yscale("log")


def missing_columns(dataframe: pd.DataFrame, columns: list[str]) -> list[str]:
    """Return any missing dataframe columns."""

    return [column for column in columns if column not in dataframe.columns]


def save_figure(fig: Any, base_path: Path) -> list[Path]:
    """Save a figure as PDF and PNG."""

    pdf_path = base_path.with_suffix(".pdf")
    png_path = base_path.with_suffix(".png")
    fig.savefig(pdf_path)
    fig.savefig(png_path, dpi=300)
    return [pdf_path, png_path]


def collect_existing_figure_paths(project_dir: Path, analysis_id: str) -> list[Path]:
    """Return any already-rendered figure files for this analysis."""

    figure_dir = project_dir / "numerics" / "figures" / analysis_id
    if not figure_dir.exists():
        return []
    return sorted(
        path
        for path in figure_dir.iterdir()
        if path.is_file() and path.suffix.lower() in {".pdf", ".png"}
    )


def latest_mtime_ns(paths: list[Path]) -> int:
    """Return the latest filesystem mtime (ns) for a list of paths."""

    return max((path.stat().st_mtime_ns for path in paths if path.exists()), default=0)


def iso_timestamp_to_ns(value: str | None) -> int:
    """Convert an ISO-8601 timestamp into nanoseconds since the epoch."""

    if not value:
        return 0
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return 0
    return int(parsed.timestamp() * 1_000_000_000)


def determine_manifest_history_action(inputs: dict[str, Any]) -> str | None:
    """Choose the manifest history action for this figure generation pass."""

    scan_meta = inputs.get("scan_meta", {})
    scan_action = scan_meta.get("history_action", "numerics_analysis_complete")
    analysis_id = inputs["analysis_id"]
    manifest = inputs.get("manifest", {})
    prior_figure_latest_mtime_ns = int(inputs.get("prior_figure_latest_mtime_ns", 0))

    def has_history_action(action: str) -> bool:
        for entry in manifest.get("history", []):
            if entry.get("action") != action:
                continue
            if entry.get("analysis_id") == analysis_id:
                return True
            note = entry.get("note")
            if isinstance(note, str) and f"analysis_id={analysis_id}" in note:
                return True
        return False

    scan_output_paths = [inputs["scan_csv_path"], inputs["scan_meta_path"]]
    scan_outputs_mtime_ns = max(
        iso_timestamp_to_ns(scan_meta.get("finished_at")),
        latest_mtime_ns(scan_output_paths),
    )

    if prior_figure_latest_mtime_ns == 0:
        return None if has_history_action(str(scan_action)) else str(scan_action)
    if scan_outputs_mtime_ns > prior_figure_latest_mtime_ns:
        return None if has_history_action(str(scan_action)) else str(scan_action)
    return "numerics_figures_regenerated"


def scan_outputs_are_newer_than_figures(inputs: dict[str, Any]) -> bool:
    """Return whether figures should be refreshed after a newer scan run."""

    prior_figure_latest_mtime_ns = int(inputs.get("prior_figure_latest_mtime_ns", 0))
    if prior_figure_latest_mtime_ns == 0:
        return False

    scan_meta = inputs.get("scan_meta", {})
    scan_output_paths = [inputs["scan_csv_path"], inputs["scan_meta_path"]]
    scan_outputs_mtime_ns = max(
        iso_timestamp_to_ns(scan_meta.get("finished_at")),
        latest_mtime_ns(scan_output_paths),
    )
    return scan_outputs_mtime_ns > prior_figure_latest_mtime_ns


def draw_constraint_band(ax: Any, constraint: dict[str, Any], color: str) -> Line2D | Patch:
    """Overlay one observable-level constraint band/line on a 1D scan."""

    label = constraint_legend_label(constraint, constraint.get("id", constraint["name"]))
    constraint_type = constraint["type"]

    if constraint_type == "measurement":
        sigma = float(constraint["sigma"])
        center = float(constraint["central_value"])
        width = sigma * float(constraint["uncertainty"])
        ax.axhspan(center - width, center + width, color=color, alpha=0.12)
        ax.axhline(center, color=color, linestyle="--", linewidth=1.2)
        return Patch(facecolor=color, alpha=0.12, label=label)

    if "limit_value_min" in constraint and "limit_value_max" in constraint:
        low = float(constraint["limit_value_min"])
        high = float(constraint["limit_value_max"])
        ax.axhspan(low, high, color=color, alpha=0.12)
        if constraint_type == "allowed_band":
            ax.axhline(low, color=color, linestyle=":", linewidth=1.0)
            ax.axhline(high, color=color, linestyle=":", linewidth=1.0)
        else:
            ax.axhline(float(constraint.get("limit_value", high)), color=color, linestyle="--", linewidth=1.2)
        return Patch(facecolor=color, alpha=0.12, label=label)

    if "limit_value" in constraint:
        line = ax.axhline(float(constraint["limit_value"]), color=color, linestyle="--", linewidth=1.2)
        return Line2D([0], [0], color=line.get_color(), linestyle="--", label=label)

    return Line2D([0], [0], color=color, linestyle="--", label=label)


def render_exclusion_2d(
    inputs: dict[str, Any],
    figure_spec: dict[str, Any],
    output_dir: Path,
    *,
    overwrite: bool,
) -> dict[str, Any]:
    """Render one exclusion_2d plot and return its result metadata."""

    dataframe = inputs["dataframe"]
    x_name = figure_spec["x"]
    y_name = figure_spec["y"]
    constraint_ids = figure_spec["constraints"]
    required_columns = [x_name, y_name, *[f"{constraint_id}_verdict" for constraint_id in constraint_ids]]
    missing = missing_columns(dataframe, required_columns)
    if missing:
        return {
            "status": "FAIL",
            "details": [f"missing columns in scan.csv: {missing}"],
            "paths": [],
        }

    subset = dataframe[required_columns].drop_duplicates(subset=[x_name, y_name])
    x_values = np.sort(subset[x_name].unique())
    y_values = np.sort(subset[y_name].unique())
    expected_points = len(x_values) * len(y_values)
    if len(subset) != expected_points:
        return {
            "status": "FAIL",
            "details": [
                f"scan.csv does not form a rectangular ({x_name}, {y_name}) grid: "
                f"expected {expected_points} unique points, found {len(subset)}"
            ],
            "paths": [],
        }

    fig, ax = plt.subplots(figsize=(8, 8))
    legend_handles: list[Any] = []
    mesh_x, mesh_y = np.meshgrid(x_values, y_values)

    for index, constraint_id in enumerate(constraint_ids):
        color = FIGURE_COLORS[index % len(FIGURE_COLORS)]
        verdict_column = f"{constraint_id}_verdict"
        grid = (
            subset.pivot(index=y_name, columns=x_name, values=verdict_column)
            .reindex(index=y_values, columns=x_values)
        )
        values = (grid == "excluded").astype(float).to_numpy()
        if np.any(values > 0):
            ax.contourf(
                mesh_x,
                mesh_y,
                values,
                levels=[0.5, 1.5],
                colors=[color],
                alpha=0.35,
            )
            if np.any(values == 0):
                ax.contour(
                    mesh_x,
                    mesh_y,
                    values,
                    levels=[0.5],
                    colors=[color],
                    linewidths=1.5,
                )
        constraint = inputs["constraints_by_id"].get(
            constraint_id, {"id": constraint_id, "name": constraint_id}
        )
        legend_handles.append(
            Patch(
                facecolor=color,
                edgecolor=color,
                alpha=0.35,
                label=constraint_legend_label(constraint, constraint_id),
            )
        )

    if figure_spec.get("show_allowed_region", True):
        allowed_columns = [f"{constraint_id}_verdict" for constraint_id in constraint_ids]
        allowed_mask = (
            subset[allowed_columns]
            .eq("allowed")
            .all(axis=1)
            .astype(float)
        )
        allowed_grid = (
            pd.DataFrame(
                {
                    x_name: subset[x_name].to_numpy(),
                    y_name: subset[y_name].to_numpy(),
                    "allowed": allowed_mask.to_numpy(),
                }
            )
            .pivot(index=y_name, columns=x_name, values="allowed")
            .reindex(index=y_values, columns=x_values)
        )
        allowed_values = allowed_grid.to_numpy()
        if np.any(allowed_values > 0):
            ax.contourf(
                mesh_x,
                mesh_y,
                allowed_values,
                levels=[0.5, 1.5],
                colors=["#D9D9D9"],
                alpha=0.22,
            )
            if np.any(allowed_values == 0):
                ax.contour(
                    mesh_x,
                    mesh_y,
                    allowed_values,
                    levels=[0.5],
                    colors=["#111111"],
                    linewidths=1.8,
                )
            legend_handles.append(
                Patch(
                    facecolor="#D9D9D9",
                    edgecolor="#111111",
                    alpha=0.22,
                    label=tex_escape("Allowed region"),
                )
            )

    ax.set_xlabel(parameter_label(inputs, x_name))
    ax.set_ylabel(parameter_label(inputs, y_name))
    if figure_spec.get("title"):
        ax.set_title(tex_escape(figure_spec["title"]))
    apply_axis_scale(ax, inputs["scan_config"], x_name, y_name)
    if legend_handles:
        ax.legend(handles=legend_handles, loc="best")
    fig.tight_layout()

    base_path = output_dir / f"exclusion-{sanitize_name(x_name)}-{sanitize_name(y_name)}"
    if not overwrite and (base_path.with_suffix(".pdf").exists() or base_path.with_suffix(".png").exists()):
        plt.close(fig)
        return {
            "status": "FAIL",
            "details": [f"output already exists for {base_path.name}; rerun with --overwrite"],
            "paths": [],
        }

    paths = save_figure(fig, base_path)
    plt.close(fig)
    return {"status": "OK", "details": [], "paths": paths}


def render_scan_1d(
    inputs: dict[str, Any],
    figure_spec: dict[str, Any],
    output_dir: Path,
    *,
    overwrite: bool,
) -> dict[str, Any]:
    """Render one scan_1d plot and return its result metadata."""

    dataframe = inputs["dataframe"]
    x_name = figure_spec["x"]
    observables = figure_spec["observables"]
    required_columns = [x_name, *observables]
    missing = missing_columns(dataframe, required_columns)
    if missing:
        return {
            "status": "FAIL",
            "details": [f"missing columns in scan.csv: {missing}"],
            "paths": [],
        }

    grouped = dataframe[required_columns].groupby(x_name, as_index=False).median(numeric_only=True)
    grouped = grouped.sort_values(by=x_name)

    fig, ax = plt.subplots(figsize=(8, 6))
    legend_handles: list[Any] = []
    for index, observable in enumerate(observables):
        color = FIGURE_COLORS[index % len(FIGURE_COLORS)]
        line = ax.plot(
            grouped[x_name].to_numpy(),
            pd.to_numeric(grouped[observable], errors="coerce").to_numpy(),
            color=color,
            linewidth=2.0,
            label=tex_escape(observable),
        )[0]
        legend_handles.append(line)

    if figure_spec.get("overlay_constraint_bands", True):
        for constraint in inputs["constraints_by_id"].values():
            if constraint.get("observable") not in observables:
                continue
            if constraint.get("implementation_status") == "manual_only":
                continue
            color = FIGURE_COLORS[(len(legend_handles)) % len(FIGURE_COLORS)]
            legend_handles.append(draw_constraint_band(ax, constraint, color))

    ax.set_xlabel(parameter_label(inputs, x_name))
    if len(observables) == 1:
        ax.set_ylabel(observable_label(inputs, observables[0]))
    else:
        ax.set_ylabel(tex_escape("Observable value"))
    if figure_spec.get("title"):
        ax.set_title(tex_escape(figure_spec["title"]))
    apply_axis_scale(ax, inputs["scan_config"], x_name)
    if legend_handles:
        ax.legend(handles=legend_handles, loc="best")
    fig.tight_layout()

    obs_fragment = "--".join(sanitize_name(observable) for observable in observables)
    base_path = output_dir / f"scan1d-{sanitize_name(x_name)}-{obs_fragment}"
    if not overwrite and (base_path.with_suffix(".pdf").exists() or base_path.with_suffix(".png").exists()):
        plt.close(fig)
        return {
            "status": "FAIL",
            "details": [f"output already exists for {base_path.name}; rerun with --overwrite"],
            "paths": [],
        }

    paths = save_figure(fig, base_path)
    plt.close(fig)
    return {"status": "OK", "details": [], "paths": paths}


def render_figures(inputs: dict[str, Any], *, overwrite: bool) -> tuple[list[dict[str, Any]], list[Path]]:
    """Render all configured figures, continuing past per-figure failures."""

    analysis_id = inputs["analysis_id"]
    output_dir = inputs["project_dir"] / "numerics" / "figures" / analysis_id
    output_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []
    generated_paths: list[Path] = []
    for figure_spec in inputs["scan_config"].get("figures", []):
        kind = figure_spec["kind"]
        if kind == "exclusion_2d":
            result = render_exclusion_2d(inputs, figure_spec, output_dir, overwrite=overwrite)
            label = f"exclusion_2d({figure_spec['x']}, {figure_spec['y']})"
        elif kind == "scan_1d":
            result = render_scan_1d(inputs, figure_spec, output_dir, overwrite=overwrite)
            label = f"scan_1d({figure_spec['x']})"
        else:
            result = {"status": "FAIL", "details": [f"unsupported figure kind {kind!r}"], "paths": []}
            label = kind

        result["label"] = label
        generated_paths.extend(result["paths"])
        results.append(result)

    return results, generated_paths


def print_results(results: list[dict[str, Any]]) -> None:
    """Print structured per-figure results."""

    print("== Figure Generation Report ==")
    for result in results:
        print(f"[{result['status']}] {result['label']}")
        for detail in result["details"]:
            print(f"  - {detail}")
        for path in result["paths"]:
            print(f"  - wrote {path}")


def main() -> int:
    """CLI entrypoint."""

    parser = build_parser()
    args = parser.parse_args()

    try:
        project_dir, scan_config_path, analysis_id = resolve_cli_inputs(args)
        configure_matplotlib()
        inputs = load_inputs(
            project_dir=project_dir,
            analysis_id=analysis_id,
            scan_config_path=scan_config_path,
        )
        inputs["prior_figure_latest_mtime_ns"] = latest_mtime_ns(
            collect_existing_figure_paths(project_dir, analysis_id)
        )
        overwrite = args.overwrite or scan_outputs_are_newer_than_figures(inputs)
        results, generated_paths = render_figures(inputs, overwrite=overwrite)
        print_results(results)
        if any(result["status"] == "FAIL" for result in results):
            return 1
        if not generated_paths:
            print("no figures were generated")
            return 1
        history_action = determine_manifest_history_action(inputs)
        summary_rows = (
            inputs["dataframe"].where(pd.notnull(inputs["dataframe"]), None).to_dict(orient="records")
        )
        summary_counts = RUN_SCAN.count_point_statuses(
            summary_rows,
            inputs["scan_config"].get("constraints_used", []),
        )
        summary_path = RUN_SCAN.write_analysis_summary(
            inputs,
            summary_rows,
            summary_counts,
            inputs["scan_csv_path"],
            generated_paths,
        )
        print(f"updated analysis summary: {summary_path}")
        manifest_path = MANIFEST.update_manifest_for_numerics(
            project_dir=inputs["project_dir"],
            analysis_id=analysis_id,
            scan_config=inputs["scan_config"],
            constraints_by_id=inputs["constraints_by_id"],
            scan_config_path=inputs["scan_config_path"],
            scan_csv_path=inputs["scan_csv_path"],
            scan_meta_path=inputs["scan_meta_path"],
            analysis_summary_path=project_dir / "numerics" / f"analysis-summary-{analysis_id}.md",
            custom_observables_path=project_dir / "numerics" / "custom_observables.py",
            figure_paths=generated_paths,
            history_action=history_action,
        )
        if history_action is not None:
            print(f"history action: {history_action}")
        else:
            print("history action: none (manifest already updated by run_scan)")
        print(f"updated manifest: {manifest_path}")
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
