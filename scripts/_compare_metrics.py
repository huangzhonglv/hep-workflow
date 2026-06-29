"""Pure metric helpers for compare_to_reference.py."""

from __future__ import annotations

from dataclasses import dataclass
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
import numpy as np
import pandas as pd


EPS = 1.0e-30


@dataclass(frozen=True)
class SeriesComparison:
    x: np.ndarray
    reference_y: np.ndarray
    predicted_y: np.ndarray
    y_label: str

    @property
    def residual(self) -> np.ndarray:
        return self.predicted_y - self.reference_y


def numeric_columns(df: pd.DataFrame) -> list[str]:
    return [
        column
        for column in df.columns
        if pd.api.types.is_numeric_dtype(df[column])
    ]


def first_existing(columns: list[str], candidates: list[str]) -> str | None:
    for candidate in candidates:
        if candidate in columns:
            return candidate
    return None


def load_csv(path: str | Any) -> pd.DataFrame:
    return pd.read_csv(path)


def choose_y_column(
    df: pd.DataFrame,
    *,
    x_column: str,
    target: dict[str, Any],
) -> str:
    candidates = [str(target.get("y_param", "")), *[str(item) for item in target.get("observables", [])]]
    chosen = first_existing(list(df.columns), candidates)
    if chosen is not None:
        return chosen

    for column in numeric_columns(df):
        if column != x_column:
            return column
    raise ValueError(f"could not identify y column for target {target.get('id')}")


def filter_fixed_rows(df: pd.DataFrame, fixed: dict[str, Any]) -> pd.DataFrame:
    filtered = df.copy()
    for key, value in sorted(fixed.items()):
        if key not in filtered.columns:
            continue
        if isinstance(value, (int, float)):
            filtered = filtered[np.isclose(filtered[key].astype(float), float(value), rtol=1e-9, atol=1e-12)]
        else:
            filtered = filtered[filtered[key].astype(str) == str(value)]
    return filtered


def relative_errors(predicted: np.ndarray, reference: np.ndarray) -> np.ndarray:
    denominator = np.maximum(np.abs(reference), EPS)
    return np.abs(predicted - reference) / denominator


def summarize_errors(predicted: np.ndarray, reference: np.ndarray) -> dict[str, float | int]:
    absolute = np.abs(predicted - reference)
    relative = relative_errors(predicted, reference)
    return {
        "max_relative_error": float(np.max(relative)) if relative.size else 0.0,
        "rms_relative_error": float(np.sqrt(np.mean(relative**2))) if relative.size else 0.0,
        "max_absolute_error": float(np.max(absolute)) if absolute.size else 0.0,
        "n_points_compared": int(reference.size),
    }


def interpolate_series(
    scan_df: pd.DataFrame,
    digitized_df: pd.DataFrame,
    target: dict[str, Any],
) -> SeriesComparison:
    x_column = str(target["x_param"])
    if x_column not in digitized_df.columns:
        raise ValueError(f"digitized data is missing x column {x_column}")
    if x_column not in scan_df.columns:
        raise ValueError(f"scan.csv is missing x column {x_column}")

    reference_y_column = choose_y_column(digitized_df, x_column=x_column, target=target)
    predicted_y_column = choose_y_column(scan_df, x_column=x_column, target=target)

    scan = filter_fixed_rows(scan_df, target.get("fixed", {}))
    scan = scan[[x_column, predicted_y_column]].dropna().sort_values(x_column)
    digitized = digitized_df[[x_column, reference_y_column]].dropna().sort_values(x_column)
    if scan.empty or digitized.empty:
        raise ValueError("scan or digitized data has no comparable points")

    scan_x = scan[x_column].astype(float).to_numpy()
    scan_y = scan[predicted_y_column].astype(float).to_numpy()
    digitized_x = digitized[x_column].astype(float).to_numpy()
    digitized_y = digitized[reference_y_column].astype(float).to_numpy()

    unique_x, unique_indices = np.unique(scan_x, return_index=True)
    unique_y = scan_y[unique_indices]
    if unique_x.size == 1:
        predicted = np.full_like(digitized_x, unique_y[0], dtype=float)
    else:
        predicted = np.interp(digitized_x, unique_x, unique_y)

    return SeriesComparison(
        x=digitized_x,
        reference_y=digitized_y,
        predicted_y=predicted,
        y_label=reference_y_column,
    )


def figure_curve_metrics(
    scan_df: pd.DataFrame,
    digitized_df: pd.DataFrame,
    target: dict[str, Any],
) -> tuple[dict[str, float | int], SeriesComparison]:
    comparison = interpolate_series(scan_df, digitized_df, target)
    return summarize_errors(comparison.predicted_y, comparison.reference_y), comparison


def benchmark_point_metrics(
    scan_df: pd.DataFrame,
    digitized_df: pd.DataFrame,
    target: dict[str, Any],
) -> tuple[dict[str, float | int], SeriesComparison]:
    comparison = interpolate_series(scan_df, digitized_df.head(1), target)
    metrics = summarize_errors(comparison.predicted_y, comparison.reference_y)
    metrics["absolute_error"] = metrics["max_absolute_error"]
    metrics["relative_error"] = metrics["max_relative_error"]
    metrics["expected_value"] = float(comparison.reference_y[0])
    metrics["predicted_value"] = float(comparison.predicted_y[0])
    return metrics, comparison


def scan_table_metrics(
    scan_df: pd.DataFrame,
    digitized_df: pd.DataFrame,
    target: dict[str, Any],
) -> tuple[dict[str, float | int], SeriesComparison | None]:
    match_columns = [
        column
        for column in [str(target.get("x_param")), str(target.get("y_param")), *sorted(target.get("fixed", {}))]
        if column in scan_df.columns and column in digitized_df.columns
    ]
    if not match_columns:
        raise ValueError("scan table comparison needs at least one shared parameter column")

    merged = digitized_df.merge(
        scan_df,
        on=match_columns,
        how="left",
        suffixes=("_reference", "_predicted"),
        indicator=True,
    )
    missing_rows = int((merged["_merge"] != "both").sum())

    relative_values: list[float] = []
    absolute_values: list[float] = []
    for observable in target.get("observables", []):
        reference_col = f"{observable}_reference"
        predicted_col = f"{observable}_predicted"
        if reference_col not in merged.columns or predicted_col not in merged.columns:
            continue
        valid = merged[[reference_col, predicted_col]].dropna()
        if valid.empty:
            continue
        reference = valid[reference_col].astype(float).to_numpy()
        predicted = valid[predicted_col].astype(float).to_numpy()
        relative_values.extend(relative_errors(predicted, reference).tolist())
        absolute_values.extend(np.abs(predicted - reference).tolist())

    relative = np.asarray(relative_values, dtype=float)
    absolute = np.asarray(absolute_values, dtype=float)
    metrics: dict[str, float | int] = {
        "max_relative_error": float(np.max(relative)) if relative.size else 0.0,
        "rms_relative_error": float(np.sqrt(np.mean(relative**2))) if relative.size else 0.0,
        "max_absolute_error": float(np.max(absolute)) if absolute.size else 0.0,
        "n_points_compared": int(relative.size),
        "missing_rows": missing_rows,
    }

    plot_data: SeriesComparison | None = None
    if str(target.get("x_param")) in digitized_df.columns and target.get("observables"):
        observable = str(target["observables"][0])
        reference_col = f"{observable}_reference"
        predicted_col = f"{observable}_predicted"
        if reference_col in merged.columns and predicted_col in merged.columns:
            valid = merged[[str(target["x_param"]), reference_col, predicted_col]].dropna()
            if not valid.empty:
                plot_data = SeriesComparison(
                    x=valid[str(target["x_param"])].astype(float).to_numpy(),
                    reference_y=valid[reference_col].astype(float).to_numpy(),
                    predicted_y=valid[predicted_col].astype(float).to_numpy(),
                    y_label=observable,
                )
    return metrics, plot_data


def extract_contour_points(scan_df: pd.DataFrame, target: dict[str, Any]) -> np.ndarray:
    x_col = str(target["x_param"])
    y_col = str(target["y_param"])
    observable = str(target.get("observables", [""])[0])
    if x_col not in scan_df.columns or y_col not in scan_df.columns or observable not in scan_df.columns:
        raise ValueError("scan.csv lacks columns needed for contour extraction")

    grid = scan_df[[x_col, y_col, observable]].dropna()
    pivot = grid.pivot_table(index=y_col, columns=x_col, values=observable, aggfunc="mean")
    if pivot.shape[0] < 2 or pivot.shape[1] < 2:
        return grid[[x_col, y_col]].astype(float).to_numpy()

    x_values = pivot.columns.astype(float).to_numpy()
    y_values = pivot.index.astype(float).to_numpy()
    z_values = pivot.to_numpy(dtype=float)
    level = float(np.nanmedian(z_values))

    fig, ax = plt.subplots()
    try:
        contours = ax.contour(x_values, y_values, z_values, levels=[level])
        segments: list[np.ndarray] = []
        for collection in contours.collections:
            for path in collection.get_paths():
                vertices = path.vertices
                if vertices.size:
                    segments.append(vertices)
        if segments:
            return max(segments, key=len)
    finally:
        plt.close(fig)

    return grid[[x_col, y_col]].astype(float).to_numpy()


def hausdorff_distance(points_a: np.ndarray, points_b: np.ndarray) -> float:
    if points_a.size == 0 or points_b.size == 0:
        return float("inf")
    distances = np.sqrt(((points_a[:, None, :] - points_b[None, :, :]) ** 2).sum(axis=2))
    directed_ab = float(np.max(np.min(distances, axis=1)))
    directed_ba = float(np.max(np.min(distances, axis=0)))
    return max(directed_ab, directed_ba)


def bbox_iou(points_a: np.ndarray, points_b: np.ndarray) -> float:
    if points_a.size == 0 or points_b.size == 0:
        return 0.0
    min_a = np.min(points_a, axis=0)
    max_a = np.max(points_a, axis=0)
    min_b = np.min(points_b, axis=0)
    max_b = np.max(points_b, axis=0)
    inter_min = np.maximum(min_a, min_b)
    inter_max = np.minimum(max_a, max_b)
    inter_dims = np.maximum(inter_max - inter_min, 0.0)
    inter_area = float(inter_dims[0] * inter_dims[1])
    area_a = float(np.prod(np.maximum(max_a - min_a, 0.0)))
    area_b = float(np.prod(np.maximum(max_b - min_b, 0.0)))
    union = area_a + area_b - inter_area
    return inter_area / union if union > 0 else 0.0


def exclusion_region_metrics(
    scan_df: pd.DataFrame,
    digitized_df: pd.DataFrame,
    target: dict[str, Any],
) -> tuple[dict[str, float | int], SeriesComparison]:
    x_col = str(target["x_param"])
    y_col = str(target["y_param"])
    if x_col not in digitized_df.columns or y_col not in digitized_df.columns:
        raise ValueError("digitized exclusion data needs x_param and y_param columns")

    reference_points = digitized_df[[x_col, y_col]].dropna().astype(float).to_numpy()
    predicted_points = extract_contour_points(scan_df, target)
    if reference_points.size == 0 or predicted_points.size == 0:
        raise ValueError("empty boundary points for exclusion comparison")

    predicted_y = np.interp(
        reference_points[:, 0],
        np.unique(predicted_points[:, 0]),
        predicted_points[np.unique(predicted_points[:, 0], return_index=True)[1], 1],
    )
    comparison = SeriesComparison(
        x=reference_points[:, 0],
        reference_y=reference_points[:, 1],
        predicted_y=predicted_y,
        y_label=y_col,
    )
    error_metrics = summarize_errors(comparison.predicted_y, comparison.reference_y)
    error_metrics["max_hausdorff_distance"] = float(hausdorff_distance(reference_points, predicted_points))
    error_metrics["iou_estimate"] = float(bbox_iou(reference_points, predicted_points))
    error_metrics["n_boundary_points"] = int(reference_points.shape[0])
    return error_metrics, comparison
