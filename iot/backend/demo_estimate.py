"""Experimental Demo Estimate: an illustrative SoH / RUL score from 300 s Quick Tests.

THIS IS NOT A MEASUREMENT, NOT A VALIDATED METHOD AND NOT A MACHINE-LEARNING MODEL.
It is a fixed, documented arithmetic heuristic that turns a battery's measured
Quick Test values into two presentation scores. It is deterministic (no random
numbers), uses the same constants for every battery (no per-battery values) and
needs the full set of valid Quick Tests; otherwise there is no estimate.

Method (averages over the battery's first QUICK_TARGET counted Quick Tests)
---------------------------------------------------------------------------
1. Apparent window resistance  R = voltage drop / mean current   [ohm]
   (voltage drop = initial at-rest voltage - final voltage of the 300 s window).
   Rising internal resistance is a common aging indicator; the 300 s window value
   also contains polarisation and wiring, so it is only a proxy.
2. Thermal response  theta = temperature rise / mean power   [K/W]
3. Demo SoH = 100
             - SOH_POINTS_PER_R_DOUBLING * max(0, R / R_REF_OHM - 1)
             - min(THERMAL_CAP_POINTS, SOH_POINTS_PER_K_PER_W * max(0, theta - THETA_REF_K_PER_W))
   clamped to [0, 100]. A resistance twice the reference costs 20 points, which
   mirrors the common rule of thumb "end of life ~ resistance doubled ~ 80 % SoH".
4. Demo RUL = RATED_CYCLE_LIFE * max(0, Demo SoH - EOL_SOH_PCT) / (100 - EOL_SOH_PCT)
   i.e. a linear fade from 100 % to the 80 % end-of-life threshold over a typical
   datasheet cycle life.

The reference constants are illustrative assumptions for an 18650 cell on this
rig, not calibrated values. Initial/final voltage, mAh and Wh are reported with
the estimate so the inputs are visible; only R and theta enter the score.
"""

from __future__ import annotations

from statistics import mean
from typing import Any, Optional

LABEL = "Experimental Demo Estimate"
METHOD = "demo-heuristic-v1"

R_REF_OHM = 0.15                 # assumed apparent 300 s window resistance of a healthy cell on this rig
SOH_POINTS_PER_R_DOUBLING = 20.0
THETA_REF_K_PER_W = 2.0          # assumed thermal response of a healthy cell, K per W
SOH_POINTS_PER_K_PER_W = 2.0
THERMAL_CAP_POINTS = 5.0
EOL_SOH_PCT = 80.0
RATED_CYCLE_LIFE = 500           # typical datasheet cycle life to 80 % for an 18650 cell
MIN_CURRENT_A = 0.02             # below this the resistance ratio is meaningless

DISCLAIMER = ("Illustrative score from a documented arithmetic heuristic applied to the 300 s Quick Tests. "
              "Not a measured SoH, not a validated RUL and not a machine-learning prediction.")

INPUT_KEYS = ("initial_voltage_v", "final_voltage_v", "voltage_drop_v", "mean_current_a", "mean_power_w",
              "max_temperature_c", "temperature_rise_c", "delivered_mah", "delivered_wh")


def _avg(cycles: list[dict], key: str) -> Optional[float]:
    vals = [c[key] for c in cycles if c.get(key) is not None]
    return mean(vals) if vals else None


def estimate(cycles: list[dict], required: int) -> dict[str, Any]:
    """Demo estimate from counted Quick Test cycles (as built by battery_tests.quick_tests)."""
    base = {"label": LABEL, "method": METHOD, "disclaimer": DISCLAIMER, "required_tests": required,
            "tests_used": [], "inputs": None, "soh_pct": None, "rul_cycles": None}
    use = cycles[:required]
    if len(use) < required:
        return {**base, "status": "no_estimate",
                "reason": f"{len(use)} of {required} valid Quick Tests completed"}
    rs = [c["voltage_drop_v"] / c["mean_current_a"] for c in use
          if c.get("voltage_drop_v") is not None and (c.get("mean_current_a") or 0) >= MIN_CURRENT_A]
    if len(rs) < required:
        return {**base, "status": "no_estimate", "reason": "voltage drop or load current missing in a test"}
    thetas = [c["temperature_rise_c"] / c["mean_power_w"] for c in use
              if c.get("temperature_rise_c") is not None and (c.get("mean_power_w") or 0) > 0]
    r = mean(rs)
    theta = mean(thetas) if thetas else None
    r_pen = SOH_POINTS_PER_R_DOUBLING * max(0.0, r / R_REF_OHM - 1.0)
    t_pen = min(THERMAL_CAP_POINTS, SOH_POINTS_PER_K_PER_W * max(0.0, theta - THETA_REF_K_PER_W)) if theta is not None else 0.0
    soh = min(100.0, max(0.0, 100.0 - r_pen - t_pen))
    rul = RATED_CYCLE_LIFE * max(0.0, soh - EOL_SOH_PCT) / (100.0 - EOL_SOH_PCT)
    return {
        **base, "status": "demo", "reason": None,
        "tests_used": [c["test_uid"] for c in use],
        "inputs": {**{k: _avg(use, k) for k in INPUT_KEYS},
                   "apparent_resistance_ohm": r, "thermal_k_per_w": theta},
        "components": {"resistance_penalty_pts": r_pen, "thermal_penalty_pts": t_pen},
        "soh_pct": round(soh, 1),
        "rul_cycles": int(round(rul)),
        "constants": {"r_ref_ohm": R_REF_OHM, "soh_points_per_r_doubling": SOH_POINTS_PER_R_DOUBLING,
                      "theta_ref_k_per_w": THETA_REF_K_PER_W, "soh_points_per_k_per_w": SOH_POINTS_PER_K_PER_W,
                      "thermal_cap_points": THERMAL_CAP_POINTS, "eol_soh_pct": EOL_SOH_PCT,
                      "rated_cycle_life": RATED_CYCLE_LIFE},
    }
