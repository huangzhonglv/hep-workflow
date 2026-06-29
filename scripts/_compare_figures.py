"""Figure helpers for compare_to_reference.py."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

MPLCONFIGDIR = Path(tempfile.gettempdir()) / "hep-workflow-mplconfig"
MPLCONFIGDIR.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("MPLCONFIGDIR", str(MPLCONFIGDIR))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from _compare_metrics import SeriesComparison


def apply_style(project_dir: Path) -> None:
    style_path = project_dir / "literature" / "style" / "paper-style.mplstyle"
    if style_path.exists():
        plt.style.use(str(style_path))


def relative_generated_files(repro_id: str, target_id: str) -> dict[str, dict[str, str]]:
    base = f"reproduction/figures/{repro_id}/{target_id}"
    return {
        "overlay": {
            "pdf": f"{base}-overlay.pdf",
            "png": f"{base}-overlay.png",
        },
        "side_by_side": {
            "pdf": f"{base}-side-by-side.pdf",
            "png": f"{base}-side-by-side.png",
        },
        "residual": {
            "pdf": f"{base}-residual.pdf",
            "png": f"{base}-residual.png",
        },
    }


def save_pdf_png(fig, pdf_path: Path, png_path: Path) -> None:
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    png_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(pdf_path, bbox_inches="tight")
    fig.savefig(png_path, dpi=160, bbox_inches="tight")
    plt.close(fig)


def _axis_labels(target: dict[str, Any], comparison: SeriesComparison | None) -> tuple[str, str]:
    x_label = str(target.get("x_param", "x"))
    y_label = comparison.y_label if comparison is not None else str(target.get("y_param", "y"))
    return x_label, y_label


def render_all_figures(
    *,
    project_dir: Path,
    generated_files: dict[str, dict[str, str]],
    target: dict[str, Any],
    comparison: SeriesComparison | None,
) -> None:
    overlay_paths = generated_files["overlay"]
    side_paths = generated_files["side_by_side"]
    residual_paths = generated_files["residual"]
    x_label, y_label = _axis_labels(target, comparison)

    fig, ax = plt.subplots()
    if comparison is not None:
        ax.plot(comparison.x, comparison.reference_y, label="paper digitized", marker="o")
        ax.plot(comparison.x, comparison.predicted_y, label="this work", marker="s")
    else:
        ax.text(0.5, 0.5, "No plottable comparison data", ha="center", va="center")
    ax.set_xlabel(x_label)
    ax.set_ylabel(y_label)
    ax.legend(loc="best")
    save_pdf_png(fig, project_dir / overlay_paths["pdf"], project_dir / overlay_paths["png"])

    fig, axes = plt.subplots(1, 2, figsize=(8, 3.2))
    if comparison is not None:
        axes[0].plot(comparison.x, comparison.reference_y, marker="o")
        axes[1].plot(comparison.x, comparison.predicted_y, marker="s")
    else:
        axes[0].text(0.5, 0.5, "No paper data", ha="center", va="center")
        axes[1].text(0.5, 0.5, "No scan data", ha="center", va="center")
    axes[0].set_title("Paper")
    axes[1].set_title("This work")
    for axis in axes:
        axis.set_xlabel(x_label)
        axis.set_ylabel(y_label)
    save_pdf_png(fig, project_dir / side_paths["pdf"], project_dir / side_paths["png"])

    fig, ax = plt.subplots()
    if comparison is not None:
        ax.axhline(0.0, color="black", linewidth=0.8)
        ax.plot(comparison.x, comparison.residual, marker="o")
    else:
        ax.text(0.5, 0.5, "No residual available", ha="center", va="center")
    ax.set_xlabel(x_label)
    ax.set_ylabel("this work - paper")
    save_pdf_png(fig, project_dir / residual_paths["pdf"], project_dir / residual_paths["png"])


def render_blocked_overlay(
    *,
    project_dir: Path,
    generated_files: dict[str, dict[str, str]],
    target: dict[str, Any],
    digitized_df: pd.DataFrame | None,
) -> None:
    overlay_paths = generated_files["overlay"]
    x_col = str(target.get("x_param", "x"))
    y_col = str(target.get("y_param", "y"))
    fig, ax = plt.subplots()
    if digitized_df is not None and x_col in digitized_df.columns:
        if y_col not in digitized_df.columns:
            numeric_cols = [
                col
                for col in digitized_df.columns
                if col != x_col and pd.api.types.is_numeric_dtype(digitized_df[col])
            ]
            y_col = numeric_cols[0] if numeric_cols else y_col
        if y_col in digitized_df.columns:
            ax.plot(digitized_df[x_col], digitized_df[y_col], marker="o", label="paper digitized")
            ax.legend(loc="best")
    ax.text(
        0.5,
        0.5,
        "Reproduction blocked: missing scan_config_hints",
        transform=ax.transAxes,
        ha="center",
        va="center",
        alpha=0.45,
        fontsize=12,
    )
    ax.set_xlabel(x_col)
    ax.set_ylabel(y_col)
    save_pdf_png(fig, project_dir / overlay_paths["pdf"], project_dir / overlay_paths["png"])
