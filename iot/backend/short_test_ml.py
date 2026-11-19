"""Can the saved NASA models, or any defensible short-test model, use 300 s Quick Tests?

This module reads a battery's counted Quick Test Cycles (genuine measured values,
via ``battery_tests.quick_tests``), maps every saved-model input to what a 300 s
partial discharge actually measures, and decides whether an experimental
short-test SoH/RUL model is defensible from the data available. It never
produces an SoH or RUL number itself.

Why the saved models cannot take Quick Tests
--------------------------------------------
The saved models (``models/*.pkl``) expect 10 statistics computed over ONE
COMPLETE NASA discharge: from a rested full charge (~4.2 V) to the cutoff
(~2.7 V), at ~2 A constant current, lasting roughly an hour. A Quick Test is a
5-minute window that starts at whatever state of charge the cell is in, under a
~0.37 A resistive load. ``INPUT_COMPATIBILITY`` below documents each input.

Why no short-test model is built (checked in ``feasibility``)
-------------------------------------------------------------
A short-test model needs (a) training examples of 300 s windows with known SoH,
(b) an operating regime that overlaps the target cells, (c) ground-truth SoH for
the target cells to evaluate on, and for RUL (d) an observed degradation
trajectory. The checks report which of these exist; if any is missing, physical
SoH/RUL stay unavailable.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd

from . import aging, battery_tests
from .config import Settings

ROOT = Path(__file__).resolve().parents[2]
NASA_FEATURES_CSV = ROOT / "data" / "processed" / "battery_health_features.csv"
NASA_RAW_DIR = ROOT / "data" / "raw"
QUICK_WINDOW_S = battery_tests.QUICK_DURATION_S

# kind:
#   missing             - a 300 s Quick Test does not measure it at all
#   different_quantity  - a related value is measured, but it is a window statistic,
#                         not the full-cycle statistic the model was trained on
#   out_of_regime       - the same physical quantity, compared directly with the NASA range
INPUT_COMPATIBILITY: dict[str, dict[str, Any]] = {
    "Voltage_mean": {
        "kind": "different_quantity", "quick_metric": "mean_voltage_v",
        "reason": "NASA: mean over the whole 4.2 V -> ~2.7 V discharge. A 300 s window covers a few percent of "
                  "the curve from an unknown state of charge, so its mean reflects where the window starts, not health.",
    },
    "Voltage_min": {
        "kind": "different_quantity", "quick_metric": "final_voltage_v",
        "reason": "NASA: the cutoff voltage reached at the end of the discharge. A Quick Test ends after 300 s, "
                  "far above the cutoff; its final voltage is not a cycle minimum.",
    },
    "Voltage_max": {
        "kind": "different_quantity", "quick_metric": "initial_voltage_v",
        "reason": "NASA: the voltage at a rested full charge. A Quick Test starts at whatever state of charge the "
                  "cell has; it is not required to start full.",
    },
    "Voltage_range": {
        "kind": "different_quantity", "quick_metric": "voltage_drop_v",
        "reason": "NASA: full-charge to cutoff span (about 1.5 V). The 300 s voltage drop is a different, much "
                  "smaller quantity that depends on load current and starting state of charge.",
    },
    "Current_mean": {
        "kind": "out_of_regime", "quick_metric": "mean_current_a",
        "reason": "Same quantity, different regime: NASA used ~2 A constant current; the Quick Tests use a "
                  "resistive load of a few hundred mA. Compared by magnitude (NASA stores discharge as negative).",
    },
    "Current_std": {
        "kind": "missing", "quick_metric": None,
        "reason": "NASA's spread includes the current collapse at the end of discharge. A resistive load that "
                  "never reaches cutoff has no such feature, and the Quick Test does not store it.",
    },
    "Temperature_mean": {
        "kind": "missing", "quick_metric": None,
        "reason": "NASA: mean cell temperature over an hour of ~2 A self-heating. Not a Quick Test metric, and a "
                  "5-minute low-current window would not be comparable anyway.",
    },
    "Temperature_max": {
        "kind": "different_quantity", "quick_metric": "max_temperature_c",
        "reason": "NASA: the peak after a full 2 A discharge. After 300 s at a few hundred mA the cell is near "
                  "ambient; the window maximum is not the cycle maximum.",
    },
    "Temperature_rise": {
        "kind": "different_quantity", "quick_metric": "temperature_rise_c",
        "reason": "NASA: temperature rise over a full discharge. A 5-minute rise at low current is a different quantity.",
    },
    "Cycle_number": {
        "kind": "missing", "quick_metric": None,
        "reason": "The models need the cell's true lifetime discharge-cycle count. It is unknown for these cells, "
                  "and Quick Tests are not charge-discharge cycles, so they cannot be counted as cycles.",
    },
}


def _nasa_ranges() -> dict[str, dict[str, float]]:
    """Training range of every model input, from the committed NASA feature table."""
    df = pd.read_csv(NASA_FEATURES_CSV)
    out = {}
    for f in INPUT_COMPATIBILITY:
        if f in df.columns:
            col = df[f].abs() if f == "Current_mean" else df[f]
            out[f] = {"min": float(col.min()), "max": float(col.max())}
    return out


def _nasa_time_series_available() -> bool:
    """Per-sample NASA discharge records would be needed to cut 300 s training windows.
    Only whole-cycle aggregates are committed; the raw .mat files are not."""
    return NASA_RAW_DIR.is_dir() and any(NASA_RAW_DIR.rglob("*.mat"))


def _full_capacity_tests(conn: sqlite3.Connection, battery_id: str, s: Settings) -> int:
    """Measured full discharges (rested full charge -> cutoff, no gaps): the only ground truth for SoH."""
    b = conn.execute("SELECT * FROM batteries WHERE battery_id=?", (battery_id,)).fetchone()
    if b is None:
        return 0
    return int(aging.battery_aging(conn, dict(b), s).get("n_full_tests") or 0)


def input_mapping(cycles: list[dict]) -> list[dict]:
    """Each saved-model input against the measured Quick Test values (per test)."""
    ranges = _nasa_ranges()
    rows = []
    for feat, info in INPUT_COMPATIBILITY.items():
        key = info["quick_metric"]
        values = [c.get(key) for c in cycles] if key else []
        rng = ranges.get(feat)
        inside = None
        if info["kind"] == "out_of_regime" and rng and values and all(v is not None for v in values):
            inside = all(rng["min"] <= abs(v) <= rng["max"] for v in values)
        rows.append({
            "model_input": feat, "kind": info["kind"], "quick_metric": key,
            "quick_values": values, "nasa_training_range": rng,
            "inside_nasa_range": inside,          # only meaningful for the same physical quantity
            "usable_by_saved_model": False,
            "reason": info["reason"],
        })
    return rows


def feasibility(conn: sqlite3.Connection, battery_id: str, cycles: list[dict], s: Settings) -> dict:
    """Whether a defensible experimental short-test SoH/RUL model can be built. Facts are checked, not assumed."""
    ranges = _nasa_ranges()
    cur = [abs(c["mean_current_a"]) for c in cycles if c.get("mean_current_a") is not None]
    regime_overlap = bool(cur) and all(ranges["Current_mean"]["min"] <= x <= ranges["Current_mean"]["max"] for x in cur)
    n_full = _full_capacity_tests(conn, battery_id, s)
    span_h = None
    if len(cycles) >= 2:
        ts = sorted(pd.Timestamp(c["completed_at"]) for c in cycles)
        span_h = (ts[-1] - ts[0]).total_seconds() / 3600
    checks = [
        {"requirement": "Training windows: per-sample NASA discharges to cut 300 s windows with known SoH",
         "met": _nasa_time_series_available(),
         "detail": "Only whole-cycle aggregates (data/processed/battery_health_features.csv) are available; "
                   "the raw NASA .mat time series are not in data/raw. They can be downloaded (see README), but that "
                   "alone would not fix the regime and ground-truth problems below."
                   if not _nasa_time_series_available() else "Raw NASA time series found in data/raw."},
        {"requirement": "Operating regime overlaps the NASA training data (load current)",
         "met": regime_overlap,
         "detail": (f"Quick Test mean current {', '.join(f'{x:.3f}' for x in cur)} A vs NASA "
                    f"{ranges['Current_mean']['min']:.2f}-{ranges['Current_mean']['max']:.2f} A.") if cur
                   else "No measured current."},
        {"requirement": "Ground-truth SoH for this battery to evaluate against (a measured full-capacity discharge)",
         "met": n_full > 0,
         "detail": f"{n_full} measured full-capacity discharge(s). Without one, no prediction can be evaluated on this cell."},
        {"requirement": "Observed degradation trajectory for RUL (aging measured over many cycles)",
         "met": False,
         "detail": (f"{len(cycles)} Quick Tests within {span_h:.1f} h. " if span_h is not None else f"{len(cycles)} Quick Test(s). ")
                   + "Short tests over a short period show no aging trend, so remaining life cannot be extrapolated."},
    ]
    possible = all(c["met"] for c in checks)
    return {
        "defensible_short_test_model": possible,
        "implemented": False,
        "checks": checks,
        "conclusion": (
            "All prerequisites are met, but no short-test model has been trained and evaluated yet, "
            "so SoH and RUL remain unavailable." if possible else
            "Not defensible with the available data: SoH and RUL remain unavailable for this battery. "
            "Any number produced from these Quick Tests would be an extrapolation that cannot be evaluated."),
    }


def assess(conn: sqlite3.Connection, battery_id: str, s: Settings) -> dict:
    """Full assessment for one battery, built from its counted Quick Test Cycles."""
    q = battery_tests.quick_tests(conn, battery_id)
    cycles = q["cycles"][: q["target"]]
    measured = [{k: c.get(k) for k in ("number", "test_uid", "completed_at", "duration_s", *battery_tests.QT_METRICS,
                                        "initial_voltage_v", "final_voltage_v", "temperature_rise_c")} for c in cycles]
    return {
        "battery_id": battery_id,
        "quick_tests": {"count": q["count"], "target": q["target"], "window_s": QUICK_WINDOW_S, "measured": measured},
        "saved_model": {
            "inputs": input_mapping(cycles),
            "usable": False,
            "reason": "The saved NASA models need statistics of one complete discharge plus the true cycle count; "
                      "a 300 s partial discharge provides none of the 10 inputs as trained.",
        },
        "short_test_model": feasibility(conn, battery_id, cycles, s),
        "soh": {"status": "unavailable", "value": None},
        "rul": {"status": "unavailable", "value": None},
        "note": "Quick Test values above are genuine measurements. They are not relabelled as full cycles, and no "
                "full-cycle feature is filled in.",
    }


def assess_all(conn: sqlite3.Connection, battery_ids: list[str], s: Settings) -> list[dict]:
    return [assess(conn, b, s) for b in battery_ids]

