"""Pure metric helpers for compare_to_reference.py."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from decimal import Decimal
import math
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
from scipy.optimize import linear_sum_assignment


UNIT_SCALE_GROUPS: tuple[dict[str, float], ...] = (
    {"dimensionless": 1.0},
    {"categorical": 1.0},
    {"rad": 1.0},
    {
        "eV": 1.0,
        "keV": 1.0e3,
        "MeV": 1.0e6,
        "GeV": 1.0e9,
        "TeV": 1.0e12,
    },
    {
        "barn": 1.0,
        "mb": 1.0e-3,
        "ub": 1.0e-6,
        "nb": 1.0e-9,
        "pb": 1.0e-12,
        "fb": 1.0e-15,
        "ab": 1.0e-18,
    },
    {
        "m": 1.0,
        "cm": 1.0e-2,
        "mm": 1.0e-3,
        "um": 1.0e-6,
        "nm": 1.0e-9,
        "fm": 1.0e-15,
    },
    {
        "s": 1.0,
        "ms": 1.0e-3,
        "us": 1.0e-6,
        "ns": 1.0e-9,
        "ps": 1.0e-12,
        "fs": 1.0e-15,
    },
)


def expected_linear_unit_conversion(
    source_unit: str,
    canonical_unit: str,
) -> tuple[float, float] | None:
    if source_unit and source_unit == canonical_unit:
        return 1.0, 0.0
    for group in UNIT_SCALE_GROUPS:
        if source_unit in group and canonical_unit in group:
            return group[source_unit] / group[canonical_unit], 0.0
    return None


def exact_decimal_linear_conversion_matches(
    source: Decimal,
    factor: Decimal,
    offset: Decimal,
    canonical: Decimal,
) -> bool:
    """Compare an exact decimal linear conversion without a precision ceiling."""

    if not all(value.is_finite() for value in (source, factor, offset, canonical)):
        return False
    if offset != 0:
        return False
    if source == 0 or factor == 0:
        return canonical == 0
    if canonical == 0:
        return False

    def normalized_components(value: Decimal) -> tuple[int, int]:
        sign, digits, exponent = value.as_tuple()
        coefficient = 0
        for digit in digits:
            coefficient = coefficient * 10 + digit
        if sign:
            coefficient = -coefficient
        while coefficient and coefficient % 10 == 0:
            coefficient //= 10
            exponent += 1
        return coefficient, int(exponent)

    source_coefficient, source_exponent = normalized_components(source)
    factor_coefficient, factor_exponent = normalized_components(factor)
    canonical_coefficient, canonical_exponent = normalized_components(canonical)
    product_coefficient = source_coefficient * factor_coefficient
    product_exponent = source_exponent + factor_exponent
    while product_coefficient and product_coefficient % 10 == 0:
        product_coefficient //= 10
        product_exponent += 1
    return (product_coefficient, product_exponent) == (
        canonical_coefficient,
        canonical_exponent,
    )


def validate_fixed_parameter_normalization(
    target: dict[str, Any],
    normalization: dict[str, Any],
) -> bool:
    """Validate fixed values kept as metadata rather than injected into raw tables."""

    fixed = target.get("fixed", {})
    records = normalization.get("fixed_parameters")
    if not isinstance(fixed, dict) or not isinstance(records, dict):
        raise ValueError("normalization fixed_parameters must be an object")
    if set(records) != set(fixed):
        raise ValueError(
            "normalization fixed_parameters must exactly cover target.fixed"
        )
    changed_unit = False
    for name, canonical_target_value in fixed.items():
        record = records.get(name)
        if not isinstance(record, dict):
            raise ValueError(f"fixed normalization for {name} must be an object")
        source_unit = record.get("source_unit")
        canonical_unit = record.get("canonical_unit")
        if not all(
            isinstance(unit, str) and unit.strip()
            for unit in (source_unit, canonical_unit)
        ):
            raise ValueError(
                f"fixed normalization for {name} requires nonblank unit strings"
            )
        expected = expected_linear_unit_conversion(
            str(source_unit),
            str(canonical_unit),
        )
        if expected is None:
            raise ValueError(
                f"fixed normalization for {name} uses unknown or incompatible units"
            )
        if record.get("operation") != "linear":
            raise ValueError(f"fixed normalization for {name} must be linear")
        factor = record.get("factor")
        offset = record.get("offset")
        if (
            not isinstance(factor, (int, float))
            or isinstance(factor, bool)
            or not isinstance(offset, (int, float))
            or isinstance(offset, bool)
            or not np.isfinite([factor, offset]).all()
            or float(factor) != expected[0]
            or float(offset) != expected[1]
        ):
            raise ValueError(
                f"fixed normalization for {name} is not an allowlisted unit conversion"
            )
        source_value = record.get("source_value")
        canonical_value = record.get("canonical_value")
        numeric_values = all(
            isinstance(value, (int, float)) and not isinstance(value, bool)
            for value in (source_value, canonical_value, canonical_target_value)
        )
        if numeric_values:
            numeric_decimals = [
                Decimal(str(value))
                for value in (source_value, canonical_value, canonical_target_value)
            ]
            if not all(value.is_finite() for value in numeric_decimals):
                raise ValueError(f"fixed normalization for {name} is non-finite")
            if expected == (1.0, 0.0):
                if not (
                    source_value == canonical_value == canonical_target_value
                ):
                    raise ValueError(
                        f"fixed normalization for {name} does not reproduce target.fixed"
                    )
                changed_unit = changed_unit or source_unit != canonical_unit
                continue
            scaled = float(source_value) * float(factor)
            if numeric_decimals[0] != 0 and scaled == 0.0:
                raise ValueError(
                    f"fixed normalization for {name} underflows a nonzero value"
                )
            if not exact_decimal_linear_conversion_matches(
                numeric_decimals[0],
                Decimal(str(factor)),
                Decimal(str(offset)),
                numeric_decimals[1],
            ) or numeric_decimals[1] != numeric_decimals[2]:
                raise ValueError(
                    f"fixed normalization for {name} does not reproduce target.fixed"
                )
        elif not (
            source_value == canonical_value == canonical_target_value
            and expected == (1.0, 0.0)
        ):
            raise ValueError(
                f"categorical fixed normalization for {name} must be exact identity"
            )
        changed_unit = changed_unit or source_unit != canonical_unit
    return changed_unit


@dataclass(frozen=True)
class SeriesComparison:
    x: np.ndarray
    reference_y: np.ndarray
    predicted_y: np.ndarray
    y_label: str
    zero_crossing_count: int = 0

    @property
    def residual(self) -> np.ndarray:
        return self.predicted_y - self.reference_y


@dataclass(frozen=True)
class ScanTableMetricResult:
    metrics: dict[str, float | int]
    comparison: SeriesComparison | None
    completeness: dict[str, Any]


@dataclass(frozen=True)
class BoundaryData:
    points: np.ndarray
    component_labels: np.ndarray
    closed_components: dict[str, bool]
    excluded_probe_match: bool
    face_probe_matches: dict[str, bool] | None = None


@dataclass(frozen=True)
class BoundaryComparison:
    reference_points: np.ndarray
    predicted_points: np.ndarray
    reference_labels: np.ndarray
    predicted_labels: np.ndarray
    reference_closed: dict[str, bool]
    predicted_closed: dict[str, bool]
    scale_values: np.ndarray
    x_label: str
    y_label: str


@dataclass(frozen=True)
class PolylineDistanceBounds:
    lower: float
    upper: float
    reference_lower: float
    reference_upper: float
    predicted_lower: float
    predicted_upper: float
    maximum_gap: float
    sample_count: int


def first_existing(columns: list[str], candidates: list[str]) -> str | None:
    for candidate in candidates:
        if candidate in columns:
            return candidate
    return None


def load_csv(path: str | Any) -> pd.DataFrame:
    csv_path = Path(path)
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration as exc:
            raise ValueError(f"CSV is empty: {csv_path}") from exc
        for row_number, row in enumerate(reader, start=2):
            if not row or (len(row) == 1 and row[0] == ""):
                raise ValueError(f"CSV has a blank data row at line {row_number}: {csv_path}")
            if len(row) != len(header):
                raise ValueError(
                    f"CSV row {row_number} has {len(row)} fields; expected "
                    f"{len(header)}: {csv_path}"
                )
    if not header or any(not column or column != column.strip() for column in header):
        raise ValueError(f"CSV has blank or whitespace-padded column names: {csv_path}")
    duplicates = sorted({column for column in header if header.count(column) > 1})
    if duplicates:
        raise ValueError(f"CSV has duplicate column names {duplicates}: {csv_path}")
    return pd.read_csv(csv_path)


def require_canonical_normalization(
    target: dict[str, Any],
    required_columns: list[str],
) -> None:
    """Refuse quantitative comparison unless normalized inputs are explicit.

    This helper deliberately validates declarations, not conversions. Unit
    conversion belongs to the import/digitization stage; accepting an absent or
    incomplete declaration here would re-introduce runtime unit guessing.
    """

    normalization = target.get("normalization")
    if not isinstance(normalization, dict):
        raise ValueError("missing canonical normalization declaration")
    if normalization.get("status") != "canonical":
        raise ValueError("normalization status must be canonical")
    if normalization.get("method") not in {"identity", "converted"}:
        raise ValueError("normalization method must be identity or converted")

    source_data_file = normalization.get("source_data_file")
    record_file = normalization.get("record_file")
    if not isinstance(source_data_file, str) or not source_data_file.strip():
        raise ValueError("normalization source_data_file is required")
    if not isinstance(record_file, str) or not record_file.strip():
        raise ValueError("normalization record_file is required")
    if source_data_file == target.get("data_file"):
        raise ValueError("raw source_data_file must differ from canonical data_file")
    acquisition = normalization.get("acquisition")
    if not isinstance(acquisition, dict) or not all(
        isinstance(acquisition.get(field), str) and acquisition[field].strip()
        for field in (
            "source_type",
            "paper_id",
            "source_locator",
            "method",
            "acquired_at",
        )
    ):
        raise ValueError("normalization acquisition provenance is incomplete")
    fixed_unit_changed = validate_fixed_parameter_normalization(target, normalization)
    if normalization.get("method") == "identity" and fixed_unit_changed:
        raise ValueError("identity normalization cannot convert fixed-parameter units")

    source_units = normalization.get("source_units")
    canonical_units = normalization.get("canonical_units")
    conversions = normalization.get("conversions")
    if not isinstance(source_units, dict) or not isinstance(canonical_units, dict):
        raise ValueError("normalization source_units/canonical_units must be objects")
    if not isinstance(conversions, dict):
        raise ValueError("normalization conversions must be an object")
    missing_units = sorted(column for column in set(required_columns) if any(
        not isinstance(mapping.get(column), str) or not mapping[column].strip()
        for mapping in (source_units, canonical_units)
    ))
    if missing_units:
        raise ValueError(
            "source/canonical units are missing for columns: " + ",".join(missing_units)
        )
    for column in sorted(set(required_columns)):
        conversion = conversions.get(column)
        if not isinstance(conversion, dict) or conversion.get("operation") != "linear":
            raise ValueError(f"normalization conversion is missing for column {column}")
        try:
            factor = float(conversion["factor"])
            offset = float(conversion["offset"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"normalization conversion is invalid for column {column}") from exc
        if not np.isfinite([factor, offset]).all() or factor == 0:
            raise ValueError(f"normalization conversion is non-finite for column {column}")
        expected_conversion = expected_linear_unit_conversion(
            source_units[column],
            canonical_units[column],
        )
        if expected_conversion is None or not (
            factor == expected_conversion[0]
            and offset == expected_conversion[1]
        ):
            raise ValueError(
                f"normalization conversion for {column} is not an allowlisted unit conversion"
            )
        if normalization.get("method") == "identity" and (
            factor != 1.0
            or offset != 0.0
            or source_units[column] != canonical_units[column]
        ):
            raise ValueError("identity normalization must preserve units and values")
    if normalization.get("method") == "converted" and not fixed_unit_changed and all(
        source_units.get(column) == canonical_units.get(column)
        for column in set(required_columns)
    ):
        raise ValueError("converted normalization must include an actual unit change")


def require_exact_slice(target: dict[str, Any], active_axes: set[str]) -> None:
    scan_parameters = target.get("scan_parameters")
    if not isinstance(scan_parameters, list) or not scan_parameters:
        raise ValueError("scan_parameters must explicitly declare every scan axis")
    names = [str(item) for item in scan_parameters]
    if len(names) != len(set(names)):
        raise ValueError("scan_parameters contains duplicate axes")
    missing_active = sorted(active_axes - set(names))
    if missing_active:
        raise ValueError(
            "scan_parameters is missing active axes: " + ",".join(missing_active)
        )
    hidden = sorted(set(names) - active_axes)
    fixed = target.get("fixed", {})
    unfixed = [name for name in hidden if name not in fixed]
    if unfixed:
        raise ValueError(
            "high-dimensional comparison requires an exact fixed slice for: "
            + ",".join(unfixed)
        )


def _strict_numeric_columns(
    df: pd.DataFrame,
    columns: list[str],
    *,
    label: str,
) -> dict[str, np.ndarray]:
    missing = [column for column in columns if column not in df.columns]
    if missing:
        raise ValueError(f"{label} is missing columns: {','.join(missing)}")
    arrays: dict[str, np.ndarray] = {}
    for column in columns:
        series = df[column]
        if pd.api.types.is_bool_dtype(series.dtype) or series.map(
            lambda value: isinstance(value, (bool, np.bool_))
        ).any():
            raise ValueError(f"{label} column {column} contains boolean data")
        values = pd.to_numeric(series, errors="coerce").to_numpy(
            dtype=float,
            na_value=np.nan,
        )
        if not np.isfinite(values).all():
            raise ValueError(f"{label} column {column} contains non-finite data")
        arrays[column] = values
    return arrays


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

    expected = [candidate for candidate in candidates if candidate]
    raise ValueError(
        f"could not identify a declared y column for target {target.get('id')}; "
        f"expected one of {expected}"
    )


def _same_typed_scalar(actual: Any, expected: Any) -> bool:
    actual_bool = isinstance(actual, (bool, np.bool_))
    expected_bool = isinstance(expected, (bool, np.bool_))
    if actual_bool or expected_bool:
        return actual_bool and expected_bool and bool(actual) is bool(expected)
    numeric_types = (int, float, np.integer, np.floating)
    if isinstance(actual, numeric_types) or isinstance(expected, numeric_types):
        return (
            isinstance(actual, numeric_types)
            and isinstance(expected, numeric_types)
            and actual == expected
        )
    return type(actual) is type(expected) and actual == expected


def _typed_value_mask(series: pd.Series, expected: Any) -> pd.Series:
    return series.map(lambda actual: _same_typed_scalar(actual, expected))


def filter_fixed_rows(df: pd.DataFrame, fixed: dict[str, Any]) -> pd.DataFrame:
    filtered = df.copy()
    for key, value in sorted(fixed.items()):
        if key not in filtered.columns:
            raise ValueError(f"data is missing fixed parameter column {key}")
        if value is None:
            raise ValueError(f"fixed parameter {key} cannot use null as an exact slice")
        if isinstance(value, bool):
            mask = filtered[key].map(
                lambda item: isinstance(item, (bool, np.bool_))
                and bool(item) is value
            )
            filtered = filtered[mask]
        elif isinstance(value, (int, float)):
            numeric = pd.to_numeric(filtered[key], errors="coerce").to_numpy(
                dtype=float,
                na_value=np.nan,
            )
            if not np.isfinite(numeric).all():
                raise ValueError(f"fixed parameter column {key} is not finite numeric data")
            filtered = filtered[_typed_value_mask(filtered[key], value)]
        else:
            mask = filtered[key].map(
                lambda item: isinstance(item, str) and item == value
            )
            filtered = filtered[mask]
    return filtered


def relative_errors(predicted: np.ndarray, reference: np.ndarray) -> np.ndarray:
    predicted = np.asarray(predicted, dtype=float)
    reference = np.asarray(reference, dtype=float)
    errors = np.zeros_like(reference, dtype=float)
    nonzero = reference != 0.0
    with np.errstate(over="ignore", divide="ignore", invalid="ignore"):
        difference = predicted[nonzero] - reference[nonzero]
        direct = np.isfinite(difference)
        relative = np.empty_like(difference, dtype=float)
        relative[direct] = np.abs(difference[direct]) / np.abs(
            reference[nonzero][direct]
        )
        relative[~direct] = np.abs(
            predicted[nonzero][~direct] / reference[nonzero][~direct] - 1.0
        )
    if not np.isfinite(relative).all():
        raise ValueError("relative error exceeds the finite numeric range")
    errors[nonzero] = relative
    return errors


def _stable_rms(values: np.ndarray) -> float:
    scale = float(np.max(np.abs(values)))
    if scale == 0.0:
        return 0.0
    normalized = values / scale
    rms = scale * float(np.sqrt(np.mean(normalized * normalized)))
    if not math.isfinite(rms):
        raise ValueError("RMS error exceeds the finite numeric range")
    return rms


def summarize_errors(predicted: np.ndarray, reference: np.ndarray) -> dict[str, float | int]:
    predicted = np.asarray(predicted, dtype=float)
    reference = np.asarray(reference, dtype=float)
    if predicted.shape != reference.shape:
        raise ValueError("predicted and reference arrays must have the same shape")
    if reference.size == 0:
        raise ValueError("comparison has no valid numerical points")
    if not np.isfinite(reference).all() or not np.isfinite(predicted).all():
        raise ValueError("comparison contains non-finite numerical values")

    with np.errstate(over="ignore", invalid="ignore"):
        absolute = np.abs(predicted - reference)
    if not np.isfinite(absolute).all():
        raise ValueError("absolute error exceeds the finite numeric range")
    relative = relative_errors(predicted, reference)
    zero_reference_count = int(np.count_nonzero(reference == 0.0))
    return {
        "max_relative_error": float(np.max(relative)),
        "rms_relative_error": _stable_rms(relative),
        "max_absolute_error": float(np.max(absolute)),
        "n_points_compared": int(reference.size),
        "n_zero_reference_values": zero_reference_count,
        "n_zero_reference_crossings": 0,
        "relative_error_defined": int(zero_reference_count == 0),
    }


def _stable_piecewise_linear(
    x: np.ndarray,
    nodes: np.ndarray,
    values: np.ndarray,
) -> np.ndarray:
    """Interpolate finite float64 data without subtracting opposite-sign extremes."""

    indices = np.searchsorted(nodes, x, side="right") - 1
    indices = np.clip(indices, 0, len(nodes) - 2)
    left_x = nodes[indices]
    right_x = nodes[indices + 1]
    with np.errstate(over="ignore", invalid="ignore"):
        direct_width = right_x - left_x
    direct = np.isfinite(direct_width) & (direct_width != 0.0)
    fractions = np.empty_like(x, dtype=float)
    fractions[direct] = (x[direct] - left_x[direct]) / direct_width[direct]
    scale = np.maximum.reduce(
        (np.abs(x), np.abs(left_x), np.abs(right_x), np.ones_like(x))
    )
    fractions[~direct] = (
        (x[~direct] / scale[~direct]) - (left_x[~direct] / scale[~direct])
    ) / (
        (right_x[~direct] / scale[~direct]) - (left_x[~direct] / scale[~direct])
    )
    fractions = np.clip(fractions, 0.0, 1.0)
    left_y = values[indices]
    right_y = values[indices + 1]
    interpolated = (1.0 - fractions) * left_y + fractions * right_y
    interpolated[x == nodes[0]] = values[0]
    interpolated[x == nodes[-1]] = values[-1]
    if not np.isfinite(interpolated).all():
        raise ValueError("piecewise-linear interpolation produced non-finite values")
    return interpolated


def interpolate_series(
    scan_df: pd.DataFrame,
    digitized_df: pd.DataFrame,
    target: dict[str, Any],
    *,
    comparison_domain: tuple[float, float] | None = None,
) -> SeriesComparison:
    x_column = str(target["x_param"])
    if x_column not in digitized_df.columns:
        raise ValueError(f"digitized data is missing x column {x_column}")
    if x_column not in scan_df.columns:
        raise ValueError(f"scan.csv is missing x column {x_column}")

    reference_y_column = str(target["y_param"])
    predicted_y_column = reference_y_column
    if reference_y_column not in digitized_df.columns:
        raise ValueError(f"digitized data is missing declared y column {reference_y_column}")
    if predicted_y_column not in scan_df.columns:
        raise ValueError(f"scan.csv is missing declared y column {predicted_y_column}")

    require_canonical_normalization(target, [x_column, reference_y_column])
    declared_scan_parameters = set(str(item) for item in target.get("scan_parameters", []))
    active_axes = {
        name for name in (x_column, reference_y_column) if name in declared_scan_parameters
    }
    require_exact_slice(target, active_axes)

    scan = filter_fixed_rows(scan_df, target.get("fixed", {}))
    reference_fixed = {
        key: value
        for key, value in target.get("fixed", {}).items()
        if key in digitized_df.columns
    }
    digitized_df = filter_fixed_rows(digitized_df, reference_fixed)
    scan = scan[[x_column, predicted_y_column]].sort_values(x_column)
    digitized = digitized_df[[x_column, reference_y_column]].sort_values(x_column)
    if scan.empty or digitized.empty:
        raise ValueError("scan or digitized data has no comparable points")

    scan_values = _strict_numeric_columns(
        scan, [x_column, predicted_y_column], label="scan"
    )
    reference_values = _strict_numeric_columns(
        digitized, [x_column, reference_y_column], label="digitized data"
    )
    scan_x = scan_values[x_column]
    scan_y = scan_values[predicted_y_column]
    digitized_x = reference_values[x_column]
    digitized_y = reference_values[reference_y_column]

    if pd.Series(scan_x).duplicated(keep=False).any():
        raise ValueError("scan contains duplicate x values on the declared slice")
    if pd.Series(digitized_x).duplicated(keep=False).any():
        raise ValueError(
            "digitized curve is not a single-valued y(x); use a parametric target type"
        )

    if comparison_domain is not None:
        x_min, x_max = comparison_domain
        if (digitized_x < x_min).any() or (digitized_x > x_max).any():
            raise ValueError("digitized data contains points outside comparison_domain")
        scan_mask = (scan_x >= x_min) & (scan_x <= x_max)
        reference_mask = (digitized_x >= x_min) & (digitized_x <= x_max)
        scan_x = scan_x[scan_mask]
        scan_y = scan_y[scan_mask]
        digitized_x = digitized_x[reference_mask]
        digitized_y = digitized_y[reference_mask]
        if scan_x.size < 2 or digitized_x.size < 2:
            raise ValueError(
                "full-domain curve comparison requires at least two scan and reference nodes"
            )
        for label, values in (("scan", scan_x), ("digitized data", digitized_x)):
            if float(values.min()) != x_min or float(values.max()) != x_max:
                raise ValueError(
                    f"{label} must exactly cover comparison_domain endpoints"
                )

    if scan_x.size < 2 or digitized_x.size < 2:
        raise ValueError("curve interpolation requires at least two points per curve")
    if digitized_x.min() < scan_x.min() or digitized_x.max() > scan_x.max():
        raise ValueError("scan does not cover the digitized domain; extrapolation is forbidden")
    if scan_x.min() < digitized_x.min() or scan_x.max() > digitized_x.max():
        raise ValueError("digitized data does not cover the scan domain; extrapolation is forbidden")

    # Treat both declared single-valued curves as piecewise-linear functions and
    # compare on the union of their knots.  Comparing only at digitized knots can
    # miss arbitrarily large generated-curve excursions between reference rows.
    zero_crossings: list[float] = []
    zero_crossing_count = 0
    for index in range(digitized_x.size - 1):
        left_y = float(digitized_y[index])
        right_y = float(digitized_y[index + 1])
        if (
            left_y != 0.0
            and right_y != 0.0
            and np.signbit(left_y) != np.signbit(right_y)
        ):
            zero_crossing_count += 1
            magnitude = max(abs(left_y), abs(right_y))
            left_scaled = abs(left_y) / magnitude
            right_scaled = abs(right_y) / magnitude
            fraction = left_scaled / (left_scaled + right_scaled)
            crossing = float(
                (1.0 - fraction) * digitized_x[index]
                + fraction * digitized_x[index + 1]
            )
            if float(digitized_x[index]) < crossing < float(digitized_x[index + 1]):
                zero_crossings.append(crossing)
    comparison_x = np.unique(
        np.concatenate((scan_x, digitized_x, np.asarray(zero_crossings, dtype=float)))
    )
    predicted = _stable_piecewise_linear(comparison_x, scan_x, scan_y)
    reference = _stable_piecewise_linear(comparison_x, digitized_x, digitized_y)
    for crossing in zero_crossings:
        reference[comparison_x == crossing] = 0.0

    return SeriesComparison(
        x=comparison_x,
        reference_y=reference,
        predicted_y=predicted,
        y_label=reference_y_column,
        zero_crossing_count=zero_crossing_count,
    )


def figure_curve_metrics(
    scan_df: pd.DataFrame,
    digitized_df: pd.DataFrame,
    target: dict[str, Any],
) -> tuple[dict[str, float | int], SeriesComparison]:
    if target.get("curve_representation") != "single_valued_y_of_x":
        raise ValueError(
            "figure_curve supports only single-valued y(x); use a separate parametric target type"
        )
    y_column = str(target.get("y_param", ""))
    if y_column not in [str(item) for item in target.get("observables", [])]:
        raise ValueError(
            "figure_curve y_param must be a declared observable with calculation provenance"
        )
    domain = target.get("comparison_domain")
    if not isinstance(domain, dict):
        raise ValueError("figure_curve requires a declared comparison_domain")
    try:
        x_min = float(domain["x_min"])
        x_max = float(domain["x_max"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("comparison_domain requires numeric x_min and x_max") from exc
    if not np.isfinite([x_min, x_max]).all() or x_min >= x_max:
        raise ValueError("comparison_domain must be finite with x_min < x_max")

    comparison = interpolate_series(
        scan_df,
        digitized_df,
        target,
        comparison_domain=(x_min, x_max),
    )
    reference_min = float(np.min(comparison.x))
    reference_max = float(np.max(comparison.x))
    if (
        reference_min != x_min
        or reference_max != x_max
        or (comparison.x < x_min).any()
        or (comparison.x > x_max).any()
    ):
        raise ValueError(
            "digitized data must exactly cover, and remain inside, comparison_domain"
        )
    metrics = summarize_errors(comparison.predicted_y, comparison.reference_y)
    metrics["n_zero_reference_crossings"] = comparison.zero_crossing_count
    metrics["relative_error_defined"] = int(
        metrics["n_zero_reference_values"] == 0
        and comparison.zero_crossing_count == 0
    )
    scan = filter_fixed_rows(scan_df, target.get("fixed", {}))
    scan_x = _strict_numeric_columns(scan, [str(target["x_param"])], label="scan")[
        str(target["x_param"])
    ]
    domain_mask = (scan_x >= x_min) & (scan_x <= x_max)
    scan_domain_x = scan_x[domain_mask]
    reference_fixed = {
        key: value
        for key, value in target.get("fixed", {}).items()
        if key in digitized_df.columns
    }
    reference_frame = filter_fixed_rows(digitized_df, reference_fixed)
    reference_x = _strict_numeric_columns(
        reference_frame,
        [str(target["x_param"])],
        label="digitized data",
    )[str(target["x_param"])]
    reference_domain_x = reference_x[
        (reference_x >= x_min) & (reference_x <= x_max)
    ]
    metrics.update({
        "declared_x_min": x_min,
        "declared_x_max": x_max,
        "reference_x_min": reference_min,
        "reference_x_max": reference_max,
        "scan_x_min": float(np.min(scan_domain_x)),
        "scan_x_max": float(np.max(scan_domain_x)),
        "reference_domain_coverage": 1.0,
        "scan_domain_coverage": 1.0,
        "reference_node_count": int(reference_domain_x.size),
        "scan_node_count": int(scan_domain_x.size),
    })
    return metrics, comparison


def benchmark_point_metrics(
    scan_df: pd.DataFrame,
    digitized_df: pd.DataFrame,
    target: dict[str, Any],
) -> tuple[dict[str, float | int], SeriesComparison]:
    if len(digitized_df) != 1:
        raise ValueError("benchmark_point requires exactly one digitized row")
    result = scan_table_metrics(scan_df, digitized_df, target)
    if not result.completeness["complete"] or result.comparison is None:
        raise ValueError(
            "benchmark_point is incomplete: "
            + ",".join(result.completeness["blocking_reasons"])
        )
    metrics = dict(result.metrics)
    metrics["relative_error"] = metrics["max_relative_error"]
    if "max_absolute_error" in metrics:
        metrics["absolute_error"] = metrics["max_absolute_error"]
    if len(target.get("observables", [])) == 1:
        metrics["expected_value"] = float(result.comparison.reference_y[0])
        metrics["predicted_value"] = float(result.comparison.predicted_y[0])
    return metrics, result.comparison


def keyed_benchmark_metrics(
    scan_df: pd.DataFrame,
    digitized_df: pd.DataFrame,
    target: dict[str, Any],
) -> ScanTableMetricResult:
    """Compare a multi-point benchmark set by an explicit unique key."""

    reference_fixed = {
        key: value
        for key, value in target.get("fixed", {}).items()
        if key in digitized_df.columns
    }
    selected_reference = filter_fixed_rows(digitized_df, reference_fixed)
    if len(selected_reference) < 2:
        return _scan_table_result(
            match_columns=[str(item) for item in target.get("match_columns", [])],
            observables=[str(item) for item in target.get("observables", [])],
            reference_rows=len(selected_reference),
            blocking_reasons=["keyed_benchmark_set_requires_multiple_rows"],
        )
    return scan_table_metrics(scan_df, digitized_df, target)


def _coverage_ratio(numerator: int, denominator: int) -> float:
    return float(numerator / denominator) if denominator else 0.0


def _scan_table_result(
    *,
    match_columns: list[str],
    observables: list[str],
    reference_rows: int,
    matched_reference_rows: int = 0,
    observables_compared: list[str] | None = None,
    compared_values: int = 0,
    blocking_reasons: list[str] | None = None,
    metrics: dict[str, float | int] | None = None,
    comparison: SeriesComparison | None = None,
) -> ScanTableMetricResult:
    compared = observables_compared or []
    reasons = sorted(set(blocking_reasons or []))
    missing_reference_rows = max(reference_rows - matched_reference_rows, 0)
    expected_values = reference_rows * len(observables)
    complete = (
        not reasons
        and reference_rows > 0
        and matched_reference_rows == reference_rows
        and compared == observables
        and compared_values == expected_values
    )
    completeness = {
        "complete": complete,
        "match_columns": match_columns,
        "reference_rows": reference_rows,
        "matched_reference_rows": matched_reference_rows,
        "missing_reference_rows": missing_reference_rows,
        "row_coverage": _coverage_ratio(matched_reference_rows, reference_rows),
        "observables_expected": observables,
        "observables_compared": compared,
        "expected_values": expected_values,
        "compared_values": compared_values,
        "value_coverage": _coverage_ratio(compared_values, expected_values),
        "blocking_reasons": reasons,
    }
    return ScanTableMetricResult(
        metrics=metrics or {},
        comparison=comparison if complete else None,
        completeness=completeness,
    )


def _numeric_array(series: pd.Series) -> np.ndarray:
    if pd.api.types.is_bool_dtype(series.dtype) or series.map(
        lambda value: isinstance(value, (bool, np.bool_))
    ).any():
        raise ValueError(f"numeric comparison column {series.name} contains boolean data")
    return pd.to_numeric(series, errors="coerce").to_numpy(
        dtype=float,
        na_value=np.nan,
    )


def _match_key_has_invalid_values(series: pd.Series) -> bool:
    if series.isna().any():
        return True
    numeric = pd.to_numeric(series, errors="coerce")
    if numeric.notna().all():
        values = numeric.to_numpy(dtype=float, na_value=np.nan)
        return not np.isfinite(values).all()
    return series.astype(str).str.strip().eq("").any()


def _match_key_categories(series: pd.Series) -> set[str]:
    categories: set[str] = set()
    for value in series:
        if isinstance(value, (bool, np.bool_)):
            categories.add("boolean")
        elif isinstance(value, (int, float, np.integer, np.floating)):
            categories.add("number")
        elif isinstance(value, str):
            categories.add("string")
        else:
            categories.add(type(value).__name__)
    return categories


def scan_table_metrics(
    scan_df: pd.DataFrame,
    digitized_df: pd.DataFrame,
    target: dict[str, Any],
) -> ScanTableMetricResult:
    raw_match_columns = target.get("match_columns", [])
    match_columns = (
        [str(column) for column in raw_match_columns]
        if isinstance(raw_match_columns, list)
        else []
    )
    observables = [str(item) for item in target.get("observables", [])]
    scan = scan_df.copy()
    reference = digitized_df.copy()
    blocking_reasons: list[str] = []

    if not match_columns:
        blocking_reasons.append("missing_match_columns")
    if not observables:
        blocking_reasons.append("missing_observables")
    if len(match_columns) != len(set(match_columns)):
        blocking_reasons.append("duplicate_declared_match_columns")
    if len(observables) != len(set(observables)):
        blocking_reasons.append("duplicate_declared_observables")

    axes = {str(target.get("x_param", "")), str(target.get("y_param", ""))}
    if "" in axes:
        blocking_reasons.append("missing_match_axes")
    missing_axes = sorted(axis for axis in axes if axis and axis not in match_columns)
    if missing_axes:
        blocking_reasons.append(
            f"match_columns_missing_axes:{','.join(missing_axes)}"
        )
    overlap = sorted(set(match_columns) & set(observables))
    if overlap:
        blocking_reasons.append(
            f"match_columns_overlap_observables:{','.join(overlap)}"
        )

    try:
        declared_scan_axes = set(str(item) for item in target.get("scan_parameters", []))
        require_exact_slice(target, set(match_columns) & declared_scan_axes)
    except ValueError as exc:
        blocking_reasons.append(f"invalid_exact_slice:{exc}")

    try:
        require_canonical_normalization(target, [*match_columns, *observables])
    except ValueError as exc:
        blocking_reasons.append(f"invalid_normalization:{exc}")
    if target.get("tolerance", {}).get("kind") == "absolute":
        canonical_units = target.get("normalization", {}).get("canonical_units", {})
        observable_units = {
            canonical_units.get(observable) for observable in observables
        }
        if None in observable_units or len(observable_units) != 1:
            blocking_reasons.append(
                "absolute_tolerance_requires_one_shared_observable_unit"
            )

    fixed = target.get("fixed", {})
    missing_fixed_scan = sorted(key for key in fixed if key not in scan.columns)
    if missing_fixed_scan:
        blocking_reasons.append(
            f"missing_fixed_columns_in_scan:{','.join(missing_fixed_scan)}"
        )
    if not missing_fixed_scan:
        try:
            scan = filter_fixed_rows(scan, fixed)
            reference_fixed = {
                key: value for key, value in fixed.items() if key in reference.columns
            }
            reference = filter_fixed_rows(reference, reference_fixed)
        except ValueError as exc:
            blocking_reasons.append(f"invalid_fixed_parameter_data:{exc}")

    reference_rows = len(reference)
    if reference.empty:
        blocking_reasons.append("empty_reference_table")
    if scan.empty:
        blocking_reasons.append("empty_scan_after_fixed_filter")

    missing_scan_match = [column for column in match_columns if column not in scan.columns]
    missing_reference_match = [
        column for column in match_columns if column not in reference.columns
    ]
    if missing_scan_match or missing_reference_match:
        details = []
        if missing_scan_match:
            details.append(f"scan.csv={','.join(missing_scan_match)}")
        if missing_reference_match:
            details.append(f"reference={','.join(missing_reference_match)}")
        blocking_reasons.append(f"missing_match_columns:{';'.join(details)}")

    invalid_scan_match = [
        column
        for column in match_columns
        if column in scan.columns and _match_key_has_invalid_values(scan[column])
    ]
    invalid_reference_match = [
        column
        for column in match_columns
        if column in reference.columns
        and _match_key_has_invalid_values(reference[column])
    ]
    if invalid_scan_match or invalid_reference_match:
        details = []
        if invalid_scan_match:
            details.append(f"scan.csv={','.join(invalid_scan_match)}")
        if invalid_reference_match:
            details.append(f"reference={','.join(invalid_reference_match)}")
        blocking_reasons.append(f"invalid_match_key_values:{';'.join(details)}")

    incompatible_match_types = [
        column
        for column in match_columns
        if column in scan.columns
        and column in reference.columns
        and (
            len(_match_key_categories(scan[column])) != 1
            or len(_match_key_categories(reference[column])) != 1
            or _match_key_categories(scan[column])
            != _match_key_categories(reference[column])
        )
    ]
    if incompatible_match_types:
        blocking_reasons.append(
            "incompatible_match_key_types:" + ",".join(incompatible_match_types)
        )

    missing_scan_observables = [
        observable for observable in observables if observable not in scan.columns
    ]
    missing_reference_observables = [
        observable for observable in observables if observable not in reference.columns
    ]
    if missing_scan_observables or missing_reference_observables:
        details = []
        if missing_scan_observables:
            details.append(f"scan.csv={','.join(missing_scan_observables)}")
        if missing_reference_observables:
            details.append(f"reference={','.join(missing_reference_observables)}")
        blocking_reasons.append(f"missing_observable_columns:{';'.join(details)}")

    if blocking_reasons:
        return _scan_table_result(
            match_columns=match_columns,
            observables=observables,
            reference_rows=reference_rows,
            blocking_reasons=blocking_reasons,
        )

    if scan.duplicated(subset=match_columns, keep=False).any():
        blocking_reasons.append("duplicate_match_keys_in_scan")
    if reference.duplicated(subset=match_columns, keep=False).any():
        blocking_reasons.append("duplicate_match_keys_in_reference")
    if blocking_reasons:
        return _scan_table_result(
            match_columns=match_columns,
            observables=observables,
            reference_rows=reference_rows,
            blocking_reasons=blocking_reasons,
        )

    merged = reference.merge(
        scan,
        on=match_columns,
        how="left",
        suffixes=("_reference", "_predicted"),
        indicator=True,
        validate="one_to_one",
    )
    matched_mask = merged["_merge"].eq("both").to_numpy()
    matched_reference_rows = int(matched_mask.sum())
    if matched_reference_rows != reference_rows:
        blocking_reasons.append(
            f"missing_reference_rows:{reference_rows - matched_reference_rows}"
        )

    reference_arrays: list[np.ndarray] = []
    predicted_arrays: list[np.ndarray] = []
    observables_compared: list[str] = []
    compared_values = 0
    observable_arrays: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for observable in observables:
        reference_values = _numeric_array(merged[f"{observable}_reference"])
        predicted_values = _numeric_array(merged[f"{observable}_predicted"])
        finite_reference = np.isfinite(reference_values)
        finite_predicted = np.isfinite(predicted_values)
        valid = matched_mask & finite_reference & finite_predicted
        valid_count = int(valid.sum())
        compared_values += valid_count

        if not finite_reference.all() or not finite_predicted[matched_mask].all():
            blocking_reasons.append(
                f"non_finite_observable_values:{observable}"
            )
        if valid_count != reference_rows:
            blocking_reasons.append(
                f"incomplete_observable_values:{observable}:{valid_count}/{reference_rows}"
            )
            continue

        observables_compared.append(observable)
        reference_arrays.append(reference_values)
        predicted_arrays.append(predicted_values)
        observable_arrays[observable] = (reference_values, predicted_values)

    x_column = str(target["x_param"])
    x_values = _numeric_array(merged[x_column])
    if not np.isfinite(x_values).all():
        blocking_reasons.append(f"non_finite_match_axis_values:{x_column}")

    if blocking_reasons:
        return _scan_table_result(
            match_columns=match_columns,
            observables=observables,
            reference_rows=reference_rows,
            matched_reference_rows=matched_reference_rows,
            observables_compared=observables_compared,
            compared_values=compared_values,
            blocking_reasons=blocking_reasons,
        )

    reference_values = np.concatenate(reference_arrays)
    predicted_values = np.concatenate(predicted_arrays)
    metrics = summarize_errors(predicted_values, reference_values)
    canonical_units = target.get("normalization", {}).get("canonical_units", {})
    observable_units = {canonical_units.get(observable) for observable in observables}
    for observable, (reference_array, predicted_array) in observable_arrays.items():
        observable_metrics = summarize_errors(predicted_array, reference_array)
        metrics[f"max_absolute_error__{observable}"] = observable_metrics[
            "max_absolute_error"
        ]
    if None in observable_units or len(observable_units) != 1:
        metrics.pop("max_absolute_error", None)
    metrics["missing_rows"] = 0

    first_observable = observables[0]
    first_reference, first_predicted = observable_arrays[first_observable]
    plot_data = SeriesComparison(
        x=x_values,
        reference_y=first_reference,
        predicted_y=first_predicted,
        y_label=first_observable,
    )
    return _scan_table_result(
        match_columns=match_columns,
        observables=observables,
        reference_rows=reference_rows,
        matched_reference_rows=matched_reference_rows,
        observables_compared=observables_compared,
        compared_values=compared_values,
        metrics=metrics,
        comparison=plot_data,
    )


def _filtered_boundary_scan(
    scan_df: pd.DataFrame,
    target: dict[str, Any],
) -> tuple[pd.DataFrame, str, str]:
    x_col = str(target["x_param"])
    y_col = str(target["y_param"])
    require_exact_slice(target, {x_col, y_col})
    scan = filter_fixed_rows(scan_df, target.get("fixed", {}))
    if scan.empty:
        raise ValueError("scan has no rows on the declared exact slice")
    return scan, x_col, y_col


def _rectangular_grid(
    frame: pd.DataFrame,
    *,
    x_col: str,
    y_col: str,
    value_col: str,
) -> tuple[np.ndarray, np.ndarray, pd.DataFrame]:
    _strict_numeric_columns(frame, [x_col, y_col], label="scan boundary grid")
    if frame.duplicated(subset=[x_col, y_col], keep=False).any():
        raise ValueError("scan has duplicate (x,y) rows on the declared slice")
    x_values = np.sort(frame[x_col].astype(float).unique())
    y_values = np.sort(frame[y_col].astype(float).unique())
    if x_values.size < 2 or y_values.size < 2:
        raise ValueError("boundary extraction requires at least a 2x2 grid")
    if len(frame) != int(x_values.size * y_values.size):
        raise ValueError("boundary extraction requires a complete rectangular grid")
    pivot = frame.pivot(index=y_col, columns=x_col, values=value_col).reindex(
        index=y_values,
        columns=x_values,
    )
    if pivot.isna().any().any():
        raise ValueError("boundary grid contains missing cells")
    return x_values, y_values, pivot


def _probe_row(
    scan: pd.DataFrame,
    *,
    x_col: str,
    y_col: str,
    probe: Any,
    label: str,
) -> pd.Series:
    if not isinstance(probe, dict):
        raise ValueError(f"{label} requires finite numeric x and y")
    try:
        probe_x = probe["x"]
        probe_y = probe["y"]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{label} requires finite numeric x and y") from exc
    if (
        not all(
            isinstance(value, (int, float)) and not isinstance(value, bool)
            for value in (probe_x, probe_y)
        )
        or not np.isfinite([probe_x, probe_y]).all()
    ):
        raise ValueError(f"{label} requires finite numeric x and y")
    _strict_numeric_columns(scan, [x_col, y_col], label="scan probe grid")
    matched = scan[
        _typed_value_mask(scan[x_col], probe_x)
        & _typed_value_mask(scan[y_col], probe_y)
    ]
    if len(matched) != 1:
        raise ValueError(
            f"{label} must match exactly one scan row; "
            f"matched {len(matched)}"
        )
    return matched.iloc[0]


def _declared_excluded_probes(boundary: dict[str, Any]) -> dict[str, dict[str, Any]]:
    faces = boundary.get("reference_faces")
    if faces is None:
        probe = boundary.get("reference_excluded_probe")
        if not isinstance(probe, dict):
            raise ValueError("boundary requires reference_excluded_probe or reference_faces")
        return {"legacy": probe}
    if not isinstance(faces, list) or not faces:
        raise ValueError("boundary reference_faces must be a nonempty array")
    probes: dict[str, dict[str, Any]] = {}
    for index, face in enumerate(faces):
        if not isinstance(face, dict):
            raise ValueError(f"reference face {index} must be an object")
        face_id = face.get("id")
        if not isinstance(face_id, str) or not face_id:
            raise ValueError(f"reference face {index} requires a nonempty id")
        if face_id in probes:
            raise ValueError(f"reference_faces contains duplicate id {face_id!r}")
        probe = face.get("excluded_probe")
        if not isinstance(probe, dict):
            raise ValueError(f"reference face {face_id!r} requires excluded_probe")
        probes[face_id] = probe
    return probes


def _require_complete_components(labels: np.ndarray, *, label: str) -> int:
    rendered = pd.Series(labels).astype(str)
    if rendered.empty or rendered.str.strip().eq("").any():
        raise ValueError(f"{label} component labels are empty or incomplete")
    counts = rendered.value_counts()
    undersampled = sorted(str(component) for component, count in counts.items() if count < 2)
    if undersampled:
        raise ValueError(
            f"{label} components require at least two boundary points: {undersampled}"
        )
    return int(len(counts))


def _strict_boolean(value: Any, *, label: str) -> bool:
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, str) and value.lower() in {"true", "false"}:
        return value.lower() == "true"
    raise ValueError(f"{label} must contain only boolean values")


def _geometry_epsilon(*point_sets: np.ndarray) -> float:
    """Return a scale-relative floating-point tolerance without a unit-size floor."""

    finite_values = [
        np.asarray(points, dtype=float)[np.isfinite(points)]
        for points in point_sets
        if np.asarray(points).size
    ]
    if not finite_values:
        return float(np.finfo(float).tiny)
    values = np.concatenate(finite_values)
    maximum = float(np.max(np.abs(values)))
    scale = maximum
    if scale == 0.0:
        return float(np.finfo(float).tiny)
    return max(
        float(np.spacing(scale)),
        float(np.finfo(float).eps * scale * 64.0),
        float(np.finfo(float).tiny),
    )


def _scaled_geometry_coordinates(*points: np.ndarray) -> np.ndarray:
    coordinates = np.asarray(points, dtype=float)
    maximum = float(np.max(np.abs(coordinates))) if coordinates.size else 0.0
    if maximum > 0.0:
        return coordinates / maximum
    return coordinates.copy()


def _points_close(first: np.ndarray, second: np.ndarray) -> bool:
    coordinates = _scaled_geometry_coordinates(first, second)
    tolerance = _geometry_epsilon(coordinates)
    return bool(np.max(np.abs(coordinates[0] - coordinates[1])) <= tolerance)


def _canonicalize_contour_segment(segment: np.ndarray) -> np.ndarray:
    """Remove only adjacent contour duplicates and make closure numerically exact."""

    points = np.asarray(segment, dtype=float)
    if points.ndim != 2 or points.shape[1] != 2 or len(points) < 2:
        return np.empty((0, 2), dtype=float)
    closed = _points_close(points[0], points[-1])
    kept = [points[0]]
    for point in points[1:]:
        if not _points_close(kept[-1], point):
            kept.append(point)
    canonical = np.asarray(kept, dtype=float)
    if closed and len(canonical) >= 3:
        if _points_close(canonical[0], canonical[-1]):
            canonical[-1] = canonical[0]
        else:
            canonical = np.vstack((canonical, canonical[0]))
    return canonical


def _ordered_boundary_rows(
    frame: pd.DataFrame,
    *,
    component_col: str,
    order_col: str,
    closed_col: str,
    label: str,
) -> tuple[pd.DataFrame, dict[str, bool]]:
    missing = [
        column
        for column in (component_col, order_col, closed_col)
        if not column or column not in frame.columns
    ]
    if missing:
        raise ValueError(f"{label} is missing topology columns: {missing}")
    if frame[component_col].isna().any():
        raise ValueError(f"{label} component labels are incomplete")
    ordered = frame.copy()
    ordered[component_col] = ordered[component_col].astype(str)
    order_values = _strict_numeric_columns(
        ordered,
        [order_col],
        label=label,
    )[order_col]
    if not np.isfinite(order_values).all() or not np.equal(
        order_values,
        np.floor(order_values),
    ).all():
        raise ValueError(f"{label} point order must be finite integers")
    ordered[order_col] = order_values.astype(int)
    closed_components: dict[str, bool] = {}
    for component, group in ordered.groupby(component_col, sort=True):
        expected = list(range(len(group)))
        actual = sorted(int(item) for item in group[order_col])
        if actual != expected:
            raise ValueError(
                f"{label} component {component!r} point order must be unique 0..N-1"
            )
        closed_values = {
            _strict_boolean(value, label=f"{label} closed column")
            for value in group[closed_col]
        }
        if len(closed_values) != 1:
            raise ValueError(
                f"{label} component {component!r} has inconsistent closed flags"
            )
        closed = next(iter(closed_values))
        if closed and len(group) < 3:
            raise ValueError(f"{label} closed components require at least three points")
        closed_components[str(component)] = closed
    ordered = ordered.sort_values([component_col, order_col], kind="stable")
    return ordered, closed_components


def extract_contour_points(scan_df: pd.DataFrame, target: dict[str, Any]) -> BoundaryData:
    scan, x_col, y_col = _filtered_boundary_scan(scan_df, target)
    boundary = target.get("boundary")
    if not isinstance(boundary, dict):
        raise ValueError("exclusion target requires an explicit boundary declaration")
    mode = boundary.get("mode")
    component_col = str(boundary.get("component_column", ""))

    if mode == "precomputed_boundary":
        membership_col = str(boundary.get("membership_column", ""))
        membership_value = boundary.get("membership_value")
        region_col = str(boundary.get("region_column", ""))
        order_col = str(boundary.get("order_column", ""))
        closed_col = str(boundary.get("closed_column", ""))
        excluded_value = boundary.get("excluded_value")
        if not membership_col or membership_col not in scan.columns:
            raise ValueError("precomputed boundary requires an explicit membership column")
        if not component_col or component_col not in scan.columns:
            raise ValueError("precomputed boundary requires an explicit component column")
        if not region_col or region_col not in scan.columns:
            raise ValueError("precomputed boundary requires an explicit region column")
        face_probe_matches = {
            face_id: _same_typed_scalar(
                _probe_row(
                    scan,
                    x_col=x_col,
                    y_col=y_col,
                    probe=probe,
                    label=f"reference face {face_id!r} excluded_probe",
                )[region_col],
                excluded_value,
            )
            for face_id, probe in _declared_excluded_probes(boundary).items()
        }
        excluded_probe_match = all(face_probe_matches.values())
        boundary_rows = scan[_typed_value_mask(scan[membership_col], membership_value)]
        if boundary_rows.empty:
            raise ValueError("precomputed boundary membership filter selected no rows")
        boundary_rows, closed_components = _ordered_boundary_rows(
            boundary_rows,
            component_col=component_col,
            order_col=order_col,
            closed_col=closed_col,
            label="precomputed boundary",
        )
        values = _strict_numeric_columns(
            boundary_rows, [x_col, y_col], label="precomputed boundary"
        )
        points = np.column_stack((values[x_col], values[y_col]))
        if pd.DataFrame(points).duplicated(keep=False).any():
            raise ValueError("precomputed boundary contains duplicate points")
        if len(points) < 2:
            raise ValueError("precomputed boundary requires at least two points")
        if boundary_rows[component_col].isna().any():
            raise ValueError("precomputed boundary component labels are incomplete")
        component_labels = boundary_rows[component_col].astype(str).to_numpy()
        _require_complete_components(component_labels, label="precomputed boundary")
        return BoundaryData(
            points,
            component_labels,
            closed_components,
            excluded_probe_match,
            face_probe_matches,
        )

    if mode == "observable_threshold":
        observable = str(boundary.get("observable", ""))
        if observable not in target.get("observables", []):
            raise ValueError("boundary observable must be declared in observables")
        values = _strict_numeric_columns(
            scan, [x_col, y_col, observable], label="scan boundary grid"
        )
        numeric = scan[[x_col, y_col, observable]].copy()
        numeric[observable] = values[observable]
        x_values, y_values, pivot = _rectangular_grid(
            numeric,
            x_col=x_col,
            y_col=y_col,
            value_col=observable,
        )
        try:
            level = float(boundary["value"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("observable threshold requires a numeric value") from exc
        if not np.isfinite(level):
            raise ValueError("observable threshold must be finite")
        canonical_unit = target.get("normalization", {}).get(
            "canonical_units", {}
        ).get(observable)
        if boundary.get("value_unit") != canonical_unit:
            raise ValueError(
                "observable threshold value_unit must match the observable canonical unit"
            )
        z_values = pivot.to_numpy(dtype=float)

        fig, ax = plt.subplots()
        try:
            contours = ax.contour(
                x_values,
                y_values,
                z_values,
                levels=[level],
                corner_mask=False,
            )
            segments = [
                canonical
                for segment in contours.allsegs[0]
                for canonical in [_canonicalize_contour_segment(segment)]
                if canonical.shape[0] >= 2
            ]
        finally:
            plt.close(fig)
        if not segments:
            raise ValueError("declared observable threshold does not cross the scan grid")
        operator = boundary.get("operator")
        if operator not in {"greater_than_or_equal", "less_than_or_equal"}:
            raise ValueError("observable threshold has unsupported operator")
        face_probe_matches: dict[str, bool] = {}
        for face_id, probe in _declared_excluded_probes(boundary).items():
            probe_row = _probe_row(
                scan,
                x_col=x_col,
                y_col=y_col,
                probe=probe,
                label=f"reference face {face_id!r} excluded_probe",
            )
            probe_value = float(
                _strict_numeric_columns(
                    pd.DataFrame([probe_row]),
                    [observable],
                    label=f"reference face {face_id!r} excluded-side probe",
                )[observable][0]
            )
            face_probe_matches[face_id] = (
                probe_value >= level
                if operator == "greater_than_or_equal"
                else probe_value <= level
            )
        excluded_probe_match = all(face_probe_matches.values())
        return BoundaryData(
            np.concatenate(segments, axis=0),
            np.concatenate([
                np.full(segment.shape[0], str(index), dtype=object)
                for index, segment in enumerate(segments)
            ]),
            {
                str(index): _points_close(segment[0], segment[-1])
                for index, segment in enumerate(segments)
            },
            bool(excluded_probe_match),
            face_probe_matches,
        )

    if mode == "constraint_verdict_transition":
        raise ValueError(
            "constraint_verdict_transition is declared but quantitative comparison is "
            "blocked until transition edges are assembled into ordered boundary paths"
        )

    raise ValueError(
        "boundary mode must be observable_threshold, constraint_verdict_transition, "
        "or precomputed_boundary"
    )


MAX_POLYLINE_SAMPLE_POINTS = 100_000
POLYLINE_DISTANCE_ERROR_BOUND = 1.0e-4
POINT_SEGMENT_CHUNK_CELLS = 1_000_000


def _segments_intersect(
    first_start: np.ndarray,
    first_end: np.ndarray,
    second_start: np.ndarray,
    second_end: np.ndarray,
) -> bool:
    def cross(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> float:
        scaled = _scaled_geometry_coordinates(a, b, c)
        first = scaled[1] - scaled[0]
        second = scaled[2] - scaled[0]
        return float(first[0] * second[1] - first[1] * second[0])

    def orientation(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> int:
        value = cross(a, b, c)
        scaled = _scaled_geometry_coordinates(a, b, c)
        scale = float(
            np.linalg.norm(scaled[1] - scaled[0])
            * np.linalg.norm(scaled[2] - scaled[0])
        )
        tolerance = float(np.finfo(float).eps * scale * 64.0)
        if abs(value) <= tolerance:
            return 0
        return 1 if value > 0 else -1

    def on_segment(a: np.ndarray, b: np.ndarray, point: np.ndarray) -> bool:
        scaled = _scaled_geometry_coordinates(a, b, point)
        tolerance = _geometry_epsilon(scaled)
        return (
            orientation(a, b, point) == 0
            and np.all(scaled[2] >= np.minimum(scaled[0], scaled[1]) - tolerance)
            and np.all(scaled[2] <= np.maximum(scaled[0], scaled[1]) + tolerance)
        )

    orientations = (
        orientation(first_start, first_end, second_start),
        orientation(first_start, first_end, second_end),
        orientation(second_start, second_end, first_start),
        orientation(second_start, second_end, first_end),
    )
    if orientations[0] * orientations[1] < 0 and orientations[2] * orientations[3] < 0:
        return True
    return any(
        value == 0 and on_segment(start, end, point)
        for value, start, end, point in (
            (orientations[0], first_start, first_end, second_start),
            (orientations[1], first_start, first_end, second_end),
            (orientations[2], second_start, second_end, first_start),
            (orientations[3], second_start, second_end, first_end),
        )
    )


def _polyline_segments(points: np.ndarray, closed: bool) -> list[tuple[np.ndarray, np.ndarray]]:
    vertices = np.asarray(points, dtype=float)
    if closed and len(vertices) > 1 and _points_close(vertices[0], vertices[-1]):
        vertices = vertices[:-1]
    segments = [
        (vertices[index], vertices[index + 1])
        for index in range(len(vertices) - 1)
    ]
    if closed:
        segments.append((vertices[-1], vertices[0]))
    return segments


def _validate_simple_polyline(points: np.ndarray, closed: bool, label: str) -> None:
    vertices = np.asarray(points, dtype=float)
    if closed and len(vertices) > 1 and _points_close(vertices[0], vertices[-1]):
        vertices = vertices[:-1]
    if closed and len(vertices) < 3:
        raise ValueError(
            f"{label} closed geometry requires at least three distinct vertices"
        )

    segments = _polyline_segments(points, closed)
    if not segments:
        raise ValueError(f"{label} has no boundary segments")
    for index, (first_start, first_end) in enumerate(segments):
        if _points_close(first_start, first_end):
            raise ValueError(f"{label} contains a zero-length segment")
        if index + 1 < len(segments) or closed:
            second_start, second_end = segments[(index + 1) % len(segments)]
            scaled = _scaled_geometry_coordinates(
                first_start,
                first_end,
                second_end,
            )
            incoming = scaled[1] - scaled[0]
            outgoing = scaled[2] - scaled[1]
            cross = float(
                incoming[0] * outgoing[1] - incoming[1] * outgoing[0]
            )
            scale = float(np.linalg.norm(incoming) * np.linalg.norm(outgoing))
            tolerance = float(np.finfo(float).eps * scale * 64.0)
            if (
                abs(cross) <= tolerance
                and float(np.dot(incoming, outgoing)) < -tolerance
            ):
                raise ValueError(f"{label} contains overlapping adjacent segments")
        for other_index in range(index + 1, len(segments)):
            adjacent = other_index == index + 1 or (
                closed and index == 0 and other_index == len(segments) - 1
            )
            if adjacent:
                continue
            second_start, second_end = segments[other_index]
            if _segments_intersect(first_start, first_end, second_start, second_end):
                raise ValueError(f"{label} contains a self-intersection")

    if closed:
        maximum = float(np.max(np.abs(vertices)))
        scaled = vertices / maximum if maximum > 0.0 else vertices.copy()
        translated = scaled - scaled[0]
        following = np.roll(translated, -1, axis=0)
        area_terms = (
            translated[:, 0] * following[:, 1]
            - following[:, 0] * translated[:, 1]
        )
        twice_area = float(np.sum(area_terms))
        area_error_bound = float(
            np.finfo(float).eps
            * max(float(np.sum(np.abs(area_terms))), np.finfo(float).tiny)
            * 128.0
        )
        if not np.isfinite(twice_area) or abs(twice_area) <= area_error_bound:
            raise ValueError(f"{label} closed geometry has zero enclosed area")


def _point_to_polyline_distances(
    points: np.ndarray,
    target_segments: list[tuple[np.ndarray, np.ndarray]],
) -> np.ndarray:
    """Exact point-to-segment distances with bounded intermediate memory."""

    starts = np.asarray([start for start, _ in target_segments], dtype=float)
    deltas = np.asarray([end - start for start, end in target_segments], dtype=float)
    denominators = np.sum(deltas * deltas, axis=1)
    if (denominators <= 0).any():
        raise ValueError("boundary contains a zero-length segment")
    chunk_size = max(1, min(4096, POINT_SEGMENT_CHUNK_CELLS // len(starts)))
    distances: list[np.ndarray] = []
    for offset in range(0, len(points), chunk_size):
        chunk = np.asarray(points[offset : offset + chunk_size], dtype=float)
        relative = chunk[:, None, :] - starts[None, :, :]
        fractions = np.sum(relative * deltas[None, :, :], axis=2) / denominators[None, :]
        fractions = np.clip(fractions, 0.0, 1.0)
        projected = starts[None, :, :] + fractions[:, :, None] * deltas[None, :, :]
        squared = np.sum((chunk[:, None, :] - projected) ** 2, axis=2)
        distances.append(np.sqrt(np.min(squared, axis=1)))
    return np.concatenate(distances)


def _redundant_collinear_vertex(
    previous: np.ndarray,
    current: np.ndarray,
    following: np.ndarray,
) -> bool:
    scaled = _scaled_geometry_coordinates(previous, current, following)
    incoming = scaled[1] - scaled[0]
    outgoing = scaled[2] - scaled[1]
    cross = float(incoming[0] * outgoing[1] - incoming[1] * outgoing[0])
    scale = float(np.linalg.norm(incoming) * np.linalg.norm(outgoing))
    cross_tolerance = float(np.finfo(float).eps * scale * 64.0)
    return abs(cross) <= cross_tolerance and float(np.dot(incoming, outgoing)) >= 0.0


def _simplified_polyline_vertices(points: np.ndarray, closed: bool) -> np.ndarray:
    vertices = np.asarray(points, dtype=float)
    if closed and len(vertices) > 1 and _points_close(vertices[0], vertices[-1]):
        vertices = vertices[:-1]
    changed = True
    while changed:
        changed = False
        minimum = 3 if closed else 2
        if len(vertices) <= minimum:
            break
        removable: list[int] = []
        indices = range(len(vertices)) if closed else range(1, len(vertices) - 1)
        for index in indices:
            if _redundant_collinear_vertex(
                vertices[index - 1],
                vertices[index],
                vertices[(index + 1) % len(vertices)],
            ):
                removable.append(index)
        if removable and len(vertices) - len(removable) >= minimum:
            vertices = np.delete(vertices, removable, axis=0)
            changed = True
    return vertices


def _polylines_same_geometry(
    first_points: np.ndarray,
    first_closed: bool,
    second_points: np.ndarray,
    second_closed: bool,
) -> bool:
    if first_closed != second_closed:
        return False
    first = _simplified_polyline_vertices(first_points, first_closed)
    second = _simplified_polyline_vertices(second_points, second_closed)
    if first.shape != second.shape:
        return False

    def equal(candidate: np.ndarray) -> bool:
        return bool(np.array_equal(first, candidate))

    if not first_closed:
        return equal(second) or equal(second[::-1])
    for candidate in (second, second[::-1]):
        for offset in range(len(candidate)):
            if equal(np.roll(candidate, offset, axis=0)):
                return True
    return False


def _uniform_polyline_samples(
    points: np.ndarray,
    closed: bool,
) -> tuple[np.ndarray, float]:
    """Sample by total arc length, independent of how vertices subdivide a line."""

    segments = _polyline_segments(points, closed)
    coordinate_scale = float(np.max(np.abs(points)))
    if not np.isfinite(coordinate_scale) or coordinate_scale <= 0.0:
        raise ValueError("boundary has non-finite or zero total length")
    scaled_points = np.asarray(points, dtype=float) / coordinate_scale
    scaled_segments = _polyline_segments(scaled_points, closed)
    scaled_lengths = np.asarray(
        [float(np.linalg.norm(end - start)) for start, end in scaled_segments],
        dtype=float,
    )
    scaled_total = float(np.sum(scaled_lengths))
    if not np.isfinite(scaled_total) or scaled_total <= 0.0:
        raise ValueError("boundary has non-finite or zero total length")
    maximum_gap = 2.0 * POLYLINE_DISTANCE_ERROR_BOUND
    maximum_intervals = (
        MAX_POLYLINE_SAMPLE_POINTS
        if closed
        else MAX_POLYLINE_SAMPLE_POINTS - 1
    )
    if coordinate_scale > maximum_gap * maximum_intervals / scaled_total:
        raise ValueError(
            "the fixed normalized polyline error bound requires more than "
            f"{MAX_POLYLINE_SAMPLE_POINTS} bounded polyline samples"
        )
    lengths = scaled_lengths * coordinate_scale
    total_length = float(np.sum(lengths))
    if not np.isfinite(total_length) or total_length <= 0.0:
        raise ValueError("boundary has non-finite or zero total length")
    intervals = max(1, int(np.ceil(total_length / maximum_gap)))
    sample_count = intervals if closed else intervals + 1
    if sample_count > MAX_POLYLINE_SAMPLE_POINTS:
        raise ValueError(
            "the fixed normalized polyline error bound requires more than "
            f"{MAX_POLYLINE_SAMPLE_POINTS} bounded polyline samples"
        )
    positions = np.linspace(
        0.0,
        total_length,
        sample_count,
        endpoint=not closed,
    )
    cumulative = np.concatenate(([0.0], np.cumsum(lengths)))
    indices = np.searchsorted(cumulative[1:], positions, side="right")
    indices = np.minimum(indices, len(segments) - 1)
    local = (positions - cumulative[indices]) / lengths[indices]
    starts = np.asarray([segments[index][0] for index in indices], dtype=float)
    deltas = np.asarray(
        [segments[index][1] - segments[index][0] for index in indices],
        dtype=float,
    )
    samples = starts + local[:, None] * deltas
    return samples, total_length / intervals


def _directed_polyline_distance_bounds(
    source_points: np.ndarray,
    source_closed: bool,
    target_points: np.ndarray,
    target_closed: bool,
) -> tuple[float, float, float, int]:
    """Bound the directed continuous-polyline distance using its Lipschitz property."""

    samples, maximum_gap = _uniform_polyline_samples(
        source_points,
        source_closed,
    )
    target_segments = _polyline_segments(target_points, target_closed)
    distances = _point_to_polyline_distances(samples, target_segments)
    lower_bound = float(np.max(distances))
    upper_bound = lower_bound + maximum_gap / 2.0
    return lower_bound, upper_bound, maximum_gap, len(samples)


def _polyline_distance_bounds(
    reference_points: np.ndarray,
    reference_closed: bool,
    predicted_points: np.ndarray,
    predicted_closed: bool,
) -> PolylineDistanceBounds:
    same_geometry = _polylines_same_geometry(
        reference_points,
        reference_closed,
        predicted_points,
        predicted_closed,
    )
    if same_geometry:
        reference_lower = reference_upper = 0.0
        predicted_lower = predicted_upper = 0.0
        reference_gap = predicted_gap = 0.0
        reference_samples = len(reference_points)
        predicted_samples = len(predicted_points)
    else:
        (
            reference_lower,
            reference_upper,
            reference_gap,
            reference_samples,
        ) = _directed_polyline_distance_bounds(
            reference_points,
            reference_closed,
            predicted_points,
            predicted_closed,
        )
        (
            predicted_lower,
            predicted_upper,
            predicted_gap,
            predicted_samples,
        ) = _directed_polyline_distance_bounds(
            predicted_points,
            predicted_closed,
            reference_points,
            reference_closed,
        )
    return PolylineDistanceBounds(
        lower=max(reference_lower, predicted_lower),
        upper=max(reference_upper, predicted_upper),
        reference_lower=reference_lower,
        reference_upper=reference_upper,
        predicted_lower=predicted_lower,
        predicted_upper=predicted_upper,
        maximum_gap=max(reference_gap, predicted_gap),
        sample_count=int(reference_samples + predicted_samples),
    )


def _directed_component_union_distance_bounds(
    source_components: dict[str, tuple[np.ndarray, bool]],
    target_components: dict[str, tuple[np.ndarray, bool]],
) -> tuple[float, float, float, int]:
    target_segments = [
        segment
        for points, closed in target_components.values()
        for segment in _polyline_segments(points, closed)
    ]
    if not target_segments:
        raise ValueError("component union has no target segments")
    lower = 0.0
    upper = 0.0
    maximum_gap = 0.0
    sample_count = 0
    for points, closed in source_components.values():
        samples, gap = _uniform_polyline_samples(points, closed)
        distances = _point_to_polyline_distances(samples, target_segments)
        component_lower = float(np.max(distances))
        component_upper = component_lower + gap / 2.0
        lower = max(lower, component_lower)
        upper = max(upper, component_upper)
        maximum_gap = max(maximum_gap, gap)
        sample_count += len(samples)
    return lower, upper, maximum_gap, sample_count


def _normalized_coordinate_scales(
    target: dict[str, Any],
    *,
    x_col: str,
    y_col: str,
) -> np.ndarray:
    scales = target.get("coordinate_scales")
    if not isinstance(scales, dict) or set(scales) != {x_col, y_col}:
        raise ValueError("coordinate_scales must define exactly the x and y coordinates")
    try:
        scale_values = np.asarray([float(scales[x_col]), float(scales[y_col])])
    except (TypeError, ValueError) as exc:
        raise ValueError("coordinate_scales must define numeric x and y scales") from exc
    if not np.isfinite(scale_values).all() or (scale_values <= 0).any():
        raise ValueError("coordinate_scales must be finite and strictly positive")
    return scale_values


def _ordered_parametric_points(
    frame: pd.DataFrame,
    *,
    parameter_col: str,
    x_col: str,
    y_col: str,
    parameter_min: float,
    parameter_max: float,
    closed: bool,
    label: str,
) -> tuple[np.ndarray, np.ndarray]:
    numeric = _strict_numeric_columns(
        frame,
        list(dict.fromkeys((parameter_col, x_col, y_col))),
        label=label,
    )
    parameter = numeric[parameter_col]
    if len(parameter) < (3 if closed else 2):
        minimum = 3 if closed else 2
        raise ValueError(f"{label} requires at least {minimum} ordered curve nodes")
    if (parameter < parameter_min).any() or (parameter > parameter_max).any():
        raise ValueError(f"{label} contains nodes outside parameter_domain")
    if float(np.min(parameter)) != parameter_min or float(np.max(parameter)) != parameter_max:
        raise ValueError(f"{label} must exactly cover parameter_domain endpoints")
    if len(np.unique(parameter)) != len(parameter):
        raise ValueError(f"{label} curve_parameter values must be unique")
    order = np.argsort(parameter, kind="stable")
    ordered_parameter = parameter[order]
    if not np.all(np.diff(ordered_parameter) > 0):
        raise ValueError(f"{label} curve_parameter must be strictly increasing")
    points = np.column_stack((numeric[x_col][order], numeric[y_col][order]))
    return ordered_parameter, points


def parametric_curve_metrics(
    scan_df: pd.DataFrame,
    digitized_df: pd.DataFrame,
    target: dict[str, Any],
) -> tuple[dict[str, float | int], BoundaryComparison]:
    if target.get("curve_representation") != "ordered_parametric_xy":
        raise ValueError(
            "parametric_curve requires curve_representation=ordered_parametric_xy"
        )
    parameter_col = str(target.get("curve_parameter", ""))
    x_col = str(target.get("x_param", ""))
    y_col = str(target.get("y_param", ""))
    if not parameter_col or not x_col or not y_col or x_col == y_col:
        raise ValueError("parametric_curve requires distinct x_param and y_param")
    observables = {str(item) for item in target.get("observables", [])}
    scan_parameters = {str(item) for item in target.get("scan_parameters", [])}
    if parameter_col not in scan_parameters:
        raise ValueError("parametric_curve curve_parameter must be a declared scan axis")
    invalid_coordinate_sources = sorted(
        coordinate
        for coordinate in (x_col, y_col)
        if coordinate != parameter_col and coordinate not in observables
    )
    if invalid_coordinate_sources:
        raise ValueError(
            "parametric_curve coordinates must be curve_parameter or declared observables: "
            + ",".join(invalid_coordinate_sources)
        )
    varying_nonparameter_coordinates = sorted(
        coordinate
        for coordinate in (x_col, y_col)
        if coordinate in scan_parameters and coordinate != parameter_col
    )
    if varying_nonparameter_coordinates:
        raise ValueError(
            "parametric_curve cannot silently project additional varying coordinates: "
            + ",".join(varying_nonparameter_coordinates)
        )
    require_exact_slice(target, {parameter_col})
    require_canonical_normalization(
        target,
        list(dict.fromkeys((parameter_col, x_col, y_col))),
    )
    domain = target.get("parameter_domain")
    if not isinstance(domain, dict):
        raise ValueError("parametric_curve requires parameter_domain")
    try:
        parameter_min = float(domain["parameter_min"])
        parameter_max = float(domain["parameter_max"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            "parameter_domain requires numeric parameter_min and parameter_max"
        ) from exc
    if (
        not np.isfinite([parameter_min, parameter_max]).all()
        or parameter_min >= parameter_max
    ):
        raise ValueError(
            "parameter_domain must be finite with parameter_min < parameter_max"
        )
    closed = target.get("curve_closed")
    if not isinstance(closed, bool):
        raise ValueError("parametric_curve curve_closed must be boolean")

    scan = filter_fixed_rows(scan_df, target.get("fixed", {}))
    if scan.empty:
        raise ValueError("scan has no rows on the declared exact slice")
    reference_fixed = {
        key: value
        for key, value in target.get("fixed", {}).items()
        if key in digitized_df.columns
    }
    reference = filter_fixed_rows(digitized_df, reference_fixed)
    if reference.empty:
        raise ValueError("digitized data has no rows on the declared exact slice")
    scan_parameter, scan_points = _ordered_parametric_points(
        scan,
        parameter_col=parameter_col,
        x_col=x_col,
        y_col=y_col,
        parameter_min=parameter_min,
        parameter_max=parameter_max,
        closed=closed,
        label="scan parametric curve",
    )
    reference_parameter, reference_points = _ordered_parametric_points(
        reference,
        parameter_col=parameter_col,
        x_col=x_col,
        y_col=y_col,
        parameter_min=parameter_min,
        parameter_max=parameter_max,
        closed=closed,
        label="digitized parametric curve",
    )
    scale_values = _normalized_coordinate_scales(target, x_col=x_col, y_col=y_col)
    with np.errstate(over="ignore", divide="ignore", invalid="ignore"):
        normalized_reference = reference_points / scale_values
        normalized_scan = scan_points / scale_values
    if not np.isfinite(normalized_reference).all() or not np.isfinite(
        normalized_scan
    ).all():
        raise ValueError("normalized parametric curve coordinates must be finite")
    _validate_simple_polyline(normalized_reference, closed, "digitized parametric curve")
    _validate_simple_polyline(normalized_scan, closed, "scan parametric curve")
    bounds = _polyline_distance_bounds(
        normalized_reference,
        closed,
        normalized_scan,
        closed,
    )
    tolerance = target.get("tolerance", {}).get("value")
    if (
        not isinstance(tolerance, (int, float))
        or isinstance(tolerance, bool)
        or not np.isfinite(float(tolerance))
        or float(tolerance) < 0
    ):
        raise ValueError(
            "parametric_curve requires a finite non-negative normalized tolerance"
        )
    tolerance_value = float(tolerance)
    within = int(bounds.upper <= tolerance_value)
    exceeds = int(bounds.lower > tolerance_value)
    decision = int(within == 1 or exceeds == 1)
    metrics: dict[str, float | int] = {
        "max_normalized_hausdorff_distance": bounds.lower,
        "max_normalized_hausdorff_distance_lower_bound": bounds.lower,
        "max_normalized_hausdorff_distance_upper_bound": bounds.upper,
        "max_normalized_hausdorff_distance_uncertainty": bounds.upper - bounds.lower,
        "reference_to_predicted_max_normalized_distance": bounds.reference_lower,
        "reference_to_predicted_max_normalized_distance_lower_bound": bounds.reference_lower,
        "predicted_to_reference_max_normalized_distance": bounds.predicted_lower,
        "predicted_to_reference_max_normalized_distance_lower_bound": bounds.predicted_lower,
        "normalized_bbox_iou": float(
            bbox_iou(normalized_reference, normalized_scan)
        ),
        "n_points_compared": bounds.sample_count,
        "reference_node_count": int(len(reference_points)),
        "scan_node_count": int(len(scan_points)),
        "declared_parameter_min": parameter_min,
        "declared_parameter_max": parameter_max,
        "reference_parameter_min": float(reference_parameter[0]),
        "reference_parameter_max": float(reference_parameter[-1]),
        "scan_parameter_min": float(scan_parameter[0]),
        "scan_parameter_max": float(scan_parameter[-1]),
        "reference_domain_coverage": 1.0,
        "scan_domain_coverage": 1.0,
        "closed_topology_match": 1,
        "distance_within_tolerance_proven": within,
        "distance_exceeds_tolerance_proven": exceeds,
        "distance_decision_defined": decision,
        "polyline_sampling_max_gap": bounds.maximum_gap,
        "polyline_sampling_error_bound": bounds.upper - bounds.lower,
        "polyline_sample_count": bounds.sample_count,
    }
    labels = np.full(len(reference_points), "curve", dtype=object)
    predicted_labels = np.full(len(scan_points), "curve", dtype=object)
    return metrics, BoundaryComparison(
        reference_points=reference_points,
        predicted_points=scan_points,
        reference_labels=labels,
        predicted_labels=predicted_labels,
        reference_closed={"curve": closed},
        predicted_closed={"curve": closed},
        scale_values=scale_values,
        x_label=x_col,
        y_label=y_col,
    )


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


def _component_point_map(
    points: np.ndarray,
    labels: np.ndarray,
    closed_components: dict[str, bool],
    *,
    label: str,
) -> dict[str, tuple[np.ndarray, bool]]:
    rendered = labels.astype(str)
    components: dict[str, tuple[np.ndarray, bool]] = {}
    single_component = len(set(rendered)) == 1
    for component_id in sorted(set(rendered)):
        if component_id not in closed_components:
            raise ValueError(f"{label} component {component_id!r} lacks closed metadata")
        component_points = np.asarray(points[rendered == component_id], dtype=float)
        _validate_simple_polyline(
            component_points,
            closed_components[component_id],
            label if single_component else f"{label} component {str(component_id)!r}",
        )
        components[component_id] = (
            component_points,
            closed_components[component_id],
        )
    return components


def _reference_face_contract(
    boundary: dict[str, Any],
    reference_components: dict[str, tuple[np.ndarray, bool]],
) -> dict[str, dict[str, Any]] | None:
    raw_faces = boundary.get("reference_faces")
    if raw_faces is None:
        return None
    if not isinstance(raw_faces, list) or not raw_faces:
        raise ValueError("reference_faces must be a nonempty array")
    faces: dict[str, dict[str, Any]] = {}
    for index, raw_face in enumerate(raw_faces):
        if not isinstance(raw_face, dict):
            raise ValueError(f"reference face {index} must be an object")
        face_id = raw_face.get("id")
        if not isinstance(face_id, str) or not face_id:
            raise ValueError(f"reference face {index} requires a nonempty id")
        if face_id in faces:
            raise ValueError(f"reference_faces contains duplicate id {face_id!r}")
        if raw_face.get("closed") is not True:
            raise ValueError(f"reference face {face_id!r} must be explicitly closed")
        if raw_face.get("excluded_side") not in {"interior", "exterior"}:
            raise ValueError(
                f"reference face {face_id!r} requires interior/exterior excluded_side"
            )
        faces[face_id] = raw_face
    if set(faces) != set(reference_components):
        raise ValueError(
            "reference_faces must exactly cover digitized boundary component IDs"
        )
    for face_id, face in faces.items():
        if reference_components[face_id][1] is not True:
            raise ValueError(
                f"digitized reference face {face_id!r} contradicts declared closed topology"
            )
        parent_id = face.get("parent_id")
        if parent_id is not None and (
            not isinstance(parent_id, str)
            or parent_id not in faces
            or parent_id == face_id
        ):
            raise ValueError(
                f"reference face {face_id!r} has an invalid parent_id {parent_id!r}"
            )
    for face_id in faces:
        visited: set[str] = set()
        current: str | None = face_id
        while current is not None:
            if current in visited:
                raise ValueError("reference_faces parent graph contains a cycle")
            visited.add(current)
            parent = faces[current].get("parent_id")
            current = str(parent) if parent is not None else None
    for face_id, face in faces.items():
        parent_id = face.get("parent_id")
        if parent_id is not None and (
            face["excluded_side"] == faces[str(parent_id)]["excluded_side"]
        ):
            raise ValueError(
                f"reference face {face_id!r} excluded_side must alternate with its parent"
            )
    _declared_excluded_probes(boundary)
    return faces


def _segments_from_components(
    components: dict[str, tuple[np.ndarray, bool]],
) -> dict[str, list[tuple[np.ndarray, np.ndarray]]]:
    return {
        component_id: _polyline_segments(points, closed)
        for component_id, (points, closed) in components.items()
    }


def _point_in_closed_component(
    point: np.ndarray,
    component_points: np.ndarray,
    *,
    label: str,
) -> bool:
    vertices = np.asarray(component_points, dtype=float)
    combined = np.vstack((vertices, np.asarray(point, dtype=float)))
    scaled = _scaled_geometry_coordinates(*combined)
    vertices = scaled[:-1]
    scaled_point = scaled[-1]
    segments = _polyline_segments(vertices, True)
    boundary_distance = float(
        _point_to_polyline_distances(
            np.asarray([scaled_point], dtype=float),
            segments,
        )[0]
    )
    if boundary_distance <= _geometry_epsilon(scaled_point, vertices):
        raise ValueError(f"{label} lies on a boundary and has undefined side semantics")
    if len(vertices) > 1 and _points_close(vertices[0], vertices[-1]):
        vertices = vertices[:-1]
    x_value, y_value = float(scaled_point[0]), float(scaled_point[1])
    inside = False
    previous = vertices[-1]
    for current in vertices:
        x_first, y_first = float(previous[0]), float(previous[1])
        x_second, y_second = float(current[0]), float(current[1])
        if (y_first > y_value) != (y_second > y_value):
            crossing = x_first + (y_value - y_first) * (x_second - x_first) / (
                y_second - y_first
            )
            if crossing > x_value:
                inside = not inside
        previous = current
    return inside


def _component_containment_sets(
    components: dict[str, tuple[np.ndarray, bool]],
    *,
    label: str,
) -> dict[str, set[str]]:
    if any(not closed for _, closed in components.values()):
        raise ValueError(f"{label} face containment requires closed components")
    segments = _segments_from_components(components)
    component_ids = sorted(components)
    for index, first_id in enumerate(component_ids):
        for second_id in component_ids[index + 1 :]:
            if any(
                _segments_intersect(first_start, first_end, second_start, second_end)
                for first_start, first_end in segments[first_id]
                for second_start, second_end in segments[second_id]
            ):
                raise ValueError(
                    f"{label} components {first_id!r} and {second_id!r} intersect or touch"
                )
    containers: dict[str, set[str]] = {component_id: set() for component_id in component_ids}
    for child_id in component_ids:
        child_points = components[child_id][0]
        for parent_id in component_ids:
            if parent_id == child_id:
                continue
            parent_points = components[parent_id][0]
            states = {
                _point_in_closed_component(
                    point,
                    parent_points,
                    label=f"{label} component containment probe",
                )
                for point in child_points
            }
            if len(states) != 1:
                raise ValueError(
                    f"{label} component {child_id!r} crosses {parent_id!r}"
                )
            if next(iter(states)):
                containers[child_id].add(parent_id)
    return containers


def _declared_ancestor_sets(
    faces: dict[str, dict[str, Any]],
) -> dict[str, set[str]]:
    ancestors: dict[str, set[str]] = {}
    for face_id in faces:
        current = faces[face_id].get("parent_id")
        values: set[str] = set()
        while current is not None:
            rendered = str(current)
            values.add(rendered)
            current = faces[rendered].get("parent_id")
        ancestors[face_id] = values
    return ancestors


def _component_pair_bounds(
    reference_components: dict[str, tuple[np.ndarray, bool]],
    predicted_components: dict[str, tuple[np.ndarray, bool]],
) -> dict[tuple[str, str], PolylineDistanceBounds]:
    return {
        (reference_id, predicted_id): _polyline_distance_bounds(
            reference_points,
            reference_closed,
            predicted_points,
            predicted_closed,
        )
        for reference_id, (reference_points, reference_closed) in reference_components.items()
        for predicted_id, (predicted_points, predicted_closed) in predicted_components.items()
    }


def _stable_component_assignment(
    reference_components: dict[str, tuple[np.ndarray, bool]],
    predicted_components: dict[str, tuple[np.ndarray, bool]],
    pair_bounds: dict[tuple[str, str], PolylineDistanceBounds],
) -> tuple[dict[str, str], bool]:
    reference_ids = sorted(reference_components)
    predicted_ids = sorted(predicted_components)
    costs = np.asarray(
        [
            [pair_bounds[(reference_id, predicted_id)].lower for predicted_id in predicted_ids]
            for reference_id in reference_ids
        ],
        dtype=float,
    )
    row_indices, column_indices = linear_sum_assignment(costs)
    assignment = {
        reference_ids[int(row)]: predicted_ids[int(column)]
        for row, column in zip(row_indices, column_indices, strict=True)
    }
    if len(reference_ids) != len(predicted_ids):
        return assignment, False
    for reference_id, predicted_id in assignment.items():
        chosen_upper = pair_bounds[(reference_id, predicted_id)].upper
        row_alternatives = [
            pair_bounds[(reference_id, candidate)].lower
            for candidate in predicted_ids
            if candidate != predicted_id
        ]
        column_alternatives = [
            pair_bounds[(candidate, predicted_id)].lower
            for candidate in reference_ids
            if candidate != reference_id
        ]
        if any(chosen_upper >= alternative for alternative in row_alternatives):
            return assignment, False
        if any(chosen_upper >= alternative for alternative in column_alternatives):
            return assignment, False
    return assignment, True


def exclusion_region_metrics(
    scan_df: pd.DataFrame,
    digitized_df: pd.DataFrame,
    target: dict[str, Any],
) -> tuple[dict[str, float | int], BoundaryComparison]:
    x_col = str(target["x_param"])
    y_col = str(target["y_param"])
    if x_col not in digitized_df.columns or y_col not in digitized_df.columns:
        raise ValueError("digitized exclusion data needs x_param and y_param columns")

    require_canonical_normalization(target, [x_col, y_col])
    reference_fixed = {
        key: value
        for key, value in target.get("fixed", {}).items()
        if key in digitized_df.columns
    }
    digitized_df = filter_fixed_rows(digitized_df, reference_fixed)
    boundary = target.get("boundary", {})
    component_col = str(boundary.get("component_column", ""))
    digitized_df, reference_closed = _ordered_boundary_rows(
        digitized_df,
        component_col=component_col,
        order_col=str(boundary.get("reference_order_column", "")),
        closed_col=str(boundary.get("reference_closed_column", "")),
        label="digitized boundary",
    )
    reference_values = _strict_numeric_columns(
        digitized_df, [x_col, y_col], label="digitized exclusion boundary"
    )
    reference_points = np.column_stack(
        (reference_values[x_col], reference_values[y_col])
    )
    if pd.DataFrame(reference_points).duplicated(keep=False).any():
        raise ValueError("digitized exclusion boundary contains duplicate points")
    reference_components = digitized_df[component_col].astype(str).to_numpy()
    reference_component_count = _require_complete_components(
        reference_components,
        label="digitized boundary",
    )
    predicted = extract_contour_points(scan_df, target)
    predicted_points = predicted.points
    if reference_points.size == 0 or predicted_points.size == 0:
        raise ValueError("empty boundary points for exclusion comparison")
    predicted_component_count = _require_complete_components(
        predicted.component_labels,
        label="predicted boundary",
    )
    scale_values = _normalized_coordinate_scales(target, x_col=x_col, y_col=y_col)

    with np.errstate(over="ignore", divide="ignore", invalid="ignore"):
        normalized_reference_vertices = reference_points / scale_values
        normalized_predicted_vertices = predicted_points / scale_values
    if not np.isfinite(normalized_reference_vertices).all() or not np.isfinite(
        normalized_predicted_vertices
    ).all():
        raise ValueError("normalized exclusion boundary coordinates must be finite")
    reference_component_map = _component_point_map(
        normalized_reference_vertices,
        reference_components,
        reference_closed,
        label="digitized boundary",
    )
    predicted_component_map = _component_point_map(
        normalized_predicted_vertices,
        predicted.component_labels,
        predicted.closed_components,
        label="predicted boundary",
    )
    faces = _reference_face_contract(boundary, reference_component_map)
    if faces is None and (
        reference_component_count != 1 or predicted_component_count != 1
    ):
        raise ValueError(
            "multi-component or holed exclusion comparison requires complete "
            "reference_faces side semantics"
        )

    tolerance = target.get("tolerance", {}).get("value")
    if (
        not isinstance(tolerance, (int, float))
        or isinstance(tolerance, bool)
        or not np.isfinite(float(tolerance))
        or float(tolerance) < 0
    ):
        raise ValueError("exclusion component matching requires a finite non-negative tolerance")
    normalized_tolerance = float(tolerance)
    pair_bounds = _component_pair_bounds(
        reference_component_map,
        predicted_component_map,
    )
    assignment, assignment_defined = _stable_component_assignment(
        reference_component_map,
        predicted_component_map,
        pair_bounds,
    )
    if (
        reference_component_count == predicted_component_count
        and not assignment_defined
    ):
        raise ValueError(
            "boundary component assignment is ambiguous within the fixed geometry error bound"
        )

    (
        reference_lower,
        reference_upper,
        reference_gap,
        reference_samples,
    ) = _directed_component_union_distance_bounds(
        reference_component_map,
        predicted_component_map,
    )
    (
        predicted_lower,
        predicted_upper,
        predicted_gap,
        predicted_samples,
    ) = _directed_component_union_distance_bounds(
        predicted_component_map,
        reference_component_map,
    )
    assigned_bounds = [
        pair_bounds[(reference_id, predicted_id)]
        for reference_id, predicted_id in assignment.items()
    ]
    component_lower = max(
        [reference_lower, predicted_lower, *[item.lower for item in assigned_bounds]]
    )
    component_upper = max(
        [reference_upper, predicted_upper, *[item.upper for item in assigned_bounds]]
    )
    if (
        assignment_defined
        and assigned_bounds
        and all(item.upper == 0.0 for item in assigned_bounds)
    ):
        reference_lower = reference_upper = 0.0
        predicted_lower = predicted_upper = 0.0
        reference_gap = predicted_gap = 0.0
        component_lower = component_upper = 0.0
    overall_lower = max(reference_lower, predicted_lower, component_lower)
    overall_upper = max(reference_upper, predicted_upper, component_upper)
    uncertainty = overall_upper - overall_lower
    distance_within_tolerance_proven = int(overall_upper <= normalized_tolerance)
    distance_exceeds_tolerance_proven = int(overall_lower > normalized_tolerance)
    distance_decision_defined = int(
        distance_within_tolerance_proven == 1
        or distance_exceeds_tolerance_proven == 1
    )
    matched_component_count = sum(
        int(
            pair_bounds[(reference_id, predicted_id)].upper <= normalized_tolerance
        )
        for reference_id, predicted_id in assignment.items()
    )
    component_coverage = matched_component_count / reference_component_count
    closed_topology_match = int(
        assignment_defined
        and all(
            reference_component_map[reference_id][1]
            == predicted_component_map[predicted_id][1]
            for reference_id, predicted_id in assignment.items()
        )
    )

    face_parent_topology_match = 1
    verified_face_probe_count = int(predicted.excluded_probe_match)
    face_probe_coverage = float(verified_face_probe_count)
    excluded_probe_match = int(predicted.excluded_probe_match)
    reference_face_count = 1
    if faces is not None:
        reference_face_count = len(faces)
        expected_ancestors = _declared_ancestor_sets(faces)
        reference_containers = _component_containment_sets(
            reference_component_map,
            label="digitized boundary",
        )
        if reference_containers != expected_ancestors:
            raise ValueError(
                "digitized boundary nesting does not match reference_faces parent graph"
            )
        face_parent_topology_match = 0
        if assignment_defined and all(
            predicted_component_map[predicted_id][1]
            for predicted_id in assignment.values()
        ):
            predicted_containers = _component_containment_sets(
                predicted_component_map,
                label="predicted boundary",
            )
            inverse_assignment = {
                predicted_id: reference_id
                for reference_id, predicted_id in assignment.items()
            }
            mapped_predicted_containers = {
                reference_id: {
                    inverse_assignment[container]
                    for container in predicted_containers[predicted_id]
                    if container in inverse_assignment
                }
                for reference_id, predicted_id in assignment.items()
            }
            face_parent_topology_match = int(
                mapped_predicted_containers == expected_ancestors
            )

        face_probe_matches = predicted.face_probe_matches or {}
        verified_face_probe_count = 0
        for face_id, face in faces.items():
            predicted_id = assignment.get(face_id)
            if predicted_id is None:
                continue
            probe = face.get("excluded_probe")
            if not isinstance(probe, dict):
                raise ValueError(f"reference face {face_id!r} lacks excluded_probe")
            try:
                normalized_probe = np.asarray(
                    [float(probe["x"]), float(probe["y"])],
                    dtype=float,
                ) / scale_values
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(
                    f"reference face {face_id!r} excluded_probe is invalid"
                ) from exc
            if not np.isfinite(normalized_probe).all():
                raise ValueError(
                    f"reference face {face_id!r} normalized excluded_probe is non-finite"
                )
            expected_inside = face.get("excluded_side") == "interior"
            reference_inside = _point_in_closed_component(
                normalized_probe,
                reference_component_map[face_id][0],
                label=f"reference face {face_id!r} excluded_probe",
            )
            if reference_inside != expected_inside:
                raise ValueError(
                    f"reference face {face_id!r} excluded_probe contradicts excluded_side"
                )
            predicted_inside = _point_in_closed_component(
                normalized_probe,
                predicted_component_map[predicted_id][0],
                label=f"predicted face matched to {face_id!r} excluded_probe",
            )
            if (
                predicted_inside == expected_inside
                and face_probe_matches.get(face_id) is True
            ):
                verified_face_probe_count += 1
        face_probe_coverage = verified_face_probe_count / reference_face_count
        excluded_probe_match = int(
            verified_face_probe_count == reference_face_count
            and assignment_defined
        )

    metrics: dict[str, float | int] = {
        "max_normalized_hausdorff_distance": overall_lower,
        "max_normalized_hausdorff_distance_lower_bound": overall_lower,
        "max_normalized_hausdorff_distance_upper_bound": overall_upper,
        "max_normalized_hausdorff_distance_uncertainty": uncertainty,
        "max_component_normalized_hausdorff_distance": component_lower,
        "reference_to_predicted_max_normalized_distance": reference_lower,
        "reference_to_predicted_max_normalized_distance_lower_bound": reference_lower,
        "predicted_to_reference_max_normalized_distance": predicted_lower,
        "predicted_to_reference_max_normalized_distance_lower_bound": predicted_lower,
        "normalized_bbox_iou": float(
            bbox_iou(normalized_reference_vertices, normalized_predicted_vertices)
        ),
        "n_points_compared": int(reference_samples + predicted_samples),
        "n_reference_boundary_points": int(reference_points.shape[0]),
        "n_predicted_boundary_points": int(predicted_points.shape[0]),
        "reference_component_count": reference_component_count,
        "predicted_component_count": predicted_component_count,
        "matched_component_count": matched_component_count,
        "component_count_match": int(reference_component_count == predicted_component_count),
        "closed_topology_match": closed_topology_match,
        "reference_face_count": reference_face_count,
        "verified_face_probe_count": verified_face_probe_count,
        "face_assignment_defined": int(assignment_defined),
        "face_parent_topology_match": face_parent_topology_match,
        "face_probe_coverage_ratio": face_probe_coverage,
        "excluded_probe_match": excluded_probe_match,
        "component_coverage_ratio": component_coverage,
        "distance_within_tolerance_proven": distance_within_tolerance_proven,
        "distance_exceeds_tolerance_proven": distance_exceeds_tolerance_proven,
        "distance_decision_defined": distance_decision_defined,
        "polyline_sampling_max_gap": max(
            reference_gap,
            predicted_gap,
            *[item.maximum_gap for item in assigned_bounds],
        ),
        "polyline_sampling_error_bound": uncertainty,
        "polyline_sample_count": int(reference_samples + predicted_samples),
    }
    return metrics, BoundaryComparison(
        reference_points=reference_points,
        predicted_points=predicted_points,
        reference_labels=reference_components,
        predicted_labels=predicted.component_labels,
        reference_closed=reference_closed,
        predicted_closed=predicted.closed_components,
        scale_values=scale_values,
        x_label=x_col,
        y_label=y_col,
    )
