"""Cycle-level feature computation.

These definitions mirror the offline pipeline exactly:

* ``src/extract_battery_data.py``  - Voltage_mean/min/max, Current_mean, Temperature_mean
  (``np.nanmean``/``nanmin``/``nanmax`` over every sample of the discharge record)
* ``src/build_features.py``        - Current_std (``np.nanstd``, population, ddof=0),
  Temperature_max, Temperature_rise (= max - FIRST sample), Voltage_range (= max - min)

They are plain sample statistics, not time-weighted, so a stream with a roughly
constant sampling interval is required for comparability. The NASA current is
negative during discharge; live telemetry uses positive = discharge, so the sign
is flipped here and ONLY here.

A single instantaneous reading is never turned into a feature vector: the caller
must pass an entire discharge segment (see ``cycles.py``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np

# Order is the order the scaler/models were trained with (models/metadata.json).
FEATURE_ORDER: tuple[str, ...] = (
    "Voltage_mean",
    "Voltage_min",
    "Voltage_max",
    "Voltage_range",
    "Current_mean",
    "Current_std",
    "Temperature_mean",
    "Temperature_max",
    "Temperature_rise",
    "Cycle_number",
)

# snake_case column in the cycles table -> model feature name
DB_TO_FEATURE = {
    "voltage_mean": "Voltage_mean",
    "voltage_min": "Voltage_min",
    "voltage_max": "Voltage_max",
    "voltage_range": "Voltage_range",
    "current_mean": "Current_mean",
    "current_std": "Current_std",
    "temperature_mean": "Temperature_mean",
    "temperature_max": "Temperature_max",
    "temperature_rise": "Temperature_rise",
    "cycle_number": "Cycle_number",
}


@dataclass
class SegmentFeatures:
    voltage_mean: Optional[float]
    voltage_min: Optional[float]
    voltage_max: Optional[float]
    voltage_range: Optional[float]
    current_mean: Optional[float]       # NASA convention, negative = discharge
    current_std: Optional[float]
    temperature_mean: Optional[float]
    temperature_max: Optional[float]
    temperature_rise: Optional[float]
    voltage_coverage: float              # fraction of samples with a voltage value
    current_coverage: float
    temperature_coverage: float

    def as_db(self) -> dict:
        return {k: getattr(self, k) for k in DB_TO_FEATURE if k != "cycle_number"}


def _arr(values: Sequence[Optional[float]]) -> np.ndarray:
    return np.array([np.nan if v is None else float(v) for v in values], dtype=float)


def _stat(fn, a: np.ndarray) -> Optional[float]:
    if a.size == 0 or np.all(np.isnan(a)):
        return None
    return float(fn(a))


def compute_segment_features(
    voltage_v: Sequence[Optional[float]],
    current_a_discharge_positive: Sequence[Optional[float]],
    temperature_c: Sequence[Optional[float]],
) -> SegmentFeatures:
    v = _arr(voltage_v)
    c = -_arr(current_a_discharge_positive)  # -> NASA sign convention
    t = _arr(temperature_c)
    n = max(len(v), 1)

    vmin, vmax = _stat(np.nanmin, v), _stat(np.nanmax, v)
    # Temperature_rise uses the FIRST sample, as build_features.py does
    # (tmp[0]). If the first temperature sample is missing, the rise is
    # undefined rather than silently re-anchored to a later sample.
    t_first = None if t.size == 0 or np.isnan(t[0]) else float(t[0])
    tmax = _stat(np.nanmax, t)

    return SegmentFeatures(
        voltage_mean=_stat(np.nanmean, v),
        voltage_min=vmin,
        voltage_max=vmax,
        voltage_range=None if vmin is None or vmax is None else vmax - vmin,
        current_mean=_stat(np.nanmean, c),
        current_std=_stat(np.nanstd, c),
        temperature_mean=_stat(np.nanmean, t),
        temperature_max=tmax,
        temperature_rise=None if tmax is None or t_first is None else tmax - t_first,
        voltage_coverage=float(np.sum(~np.isnan(v)) / n),
        current_coverage=float(np.sum(~np.isnan(c)) / n),
        temperature_coverage=float(np.sum(~np.isnan(t)) / n),
    )


def feature_vector(row: dict) -> tuple[Optional[list[float]], list[str]]:
    """Build the model input in training order from a cycles-table row.

    Returns (vector, missing_feature_names). Vector is None if anything is missing:
    missing values are never imputed.
    """
    vec, missing = [], []
    by_feature = {feat: row.get(col) for col, feat in DB_TO_FEATURE.items()}
    for name in FEATURE_ORDER:
        val = by_feature.get(name)
        if val is None or (isinstance(val, float) and np.isnan(val)):
            missing.append(name)
        else:
            vec.append(float(val))
    return (None if missing else vec), missing
