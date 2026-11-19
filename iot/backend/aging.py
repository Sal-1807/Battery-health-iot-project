"""Battery aging analytics for the physical battery profiles.

Built only on measured discharge sessions (sessions.py). Nothing here is a
model prediction and nothing is an absolute state of health.

Definitions
-----------
* Delivered capacity / energy / duration: sums over all measured sessions of
  the battery (partial ones included - they are real measurements).
* EFC: total delivered charge / the battery's own RATED capacity
  (throughput normalised for 2500 vs 3000 mAh cells).
* Full test: a session that started from a rested full charge
  (>= full_charge_v), reached the cutoff voltage, ended because the load
  stopped, had no telemetry gaps, and whose on/off transition uncertainty is
  <= MAX_UNCERTAINTY_FRAC of its charge. Anything else is excluded from
  capacity and degradation figures, with the reason recorded.
* Comparable: a full test whose mean load current is within CURRENT_TOL of the
  reference test, mean temperature within TEMP_TOL_C, and same cutoff voltage.
  Capacity depends strongly on load and temperature, so tests under different
  conditions are not compared.
* Reference: the FIRST full test measured on that battery. Capacity retention
  = capacity / reference capacity. The manufacturer's rated capacity is NOT
  used as the reference: a label is not a measurement of initial capacity.
* Retention is relative to the battery's own first measured test. It is not
  absolute SoH (the cell may already have aged before the reference test).
* A trend needs >= MIN_TREND_POINTS comparable tests; otherwise
  "insufficient_data".
"""

from __future__ import annotations

import sqlite3
from typing import Any, Optional

from .config import Settings
from .sessions import SESSION_COLS, session_out

CURRENT_TOL = 0.15
TEMP_TOL_C = 5.0
MAX_UNCERTAINTY_FRAC = 0.02
MIN_TREND_POINTS = 2

RUL_STATUS = {"status": "unavailable",
              "reason": "No RUL model has been validated for these physical cells; the NASA-trained model is "
                        "a separate demonstration (ML validation page)."}


def _session_means(conn: sqlite3.Connection, battery_id: str, sess: dict, s: Settings) -> dict[str, Optional[float]]:
    """Mean temperature and mean loaded voltage from the battery's OWN readings in the session window."""
    end = sess["end_ms"] if sess.get("end_ms") else sess["last_on_ms"]
    row = conn.execute(
        "SELECT AVG(temperature_c) AS t, AVG(voltage_v) AS v FROM readings WHERE battery_id=? AND ts_ms BETWEEN ? AND ? "
        "AND current_a >= ?", (battery_id, sess["start_ms"], end, s.session_on_a)).fetchone()
    return {"mean_temperature_c": row["t"], "mean_loaded_voltage_v": row["v"]}


def _full_test_issues(x: dict) -> list[str]:
    c = x["coverage"]
    issues = []
    if x["status"] != "ended":
        issues.append("session still in progress")
    if not c["started_from_full_rest_voltage"]:
        issues.append(f"did not start from a rested full charge (>= {c['full_charge_v']:g} V)")
    if not c["reached_cutoff_voltage"]:
        issues.append(f"did not reach the cutoff voltage ({c['cutoff_v']:g} V): partial discharge")
    if x["status"] == "ended" and x["end_reason"] != "load_stopped":
        issues.append(f"interrupted ({(x['end_reason'] or '').replace('_', ' ')})")
    if x["n_gaps"]:
        issues.append(f"{x['n_gaps']} telemetry gap(s), {x['gap_s']:.0f} s unmeasured")
    if x["charge_mah"] > 0 and x["uncertainty_mah"] > MAX_UNCERTAINTY_FRAC * x["charge_mah"]:
        issues.append(f"load on/off uncertainty {x['uncertainty_mah']:.1f} mAh > {MAX_UNCERTAINTY_FRAC:.0%} of charge")
    return issues


def _comparability_issues(x: dict, ref: dict) -> list[str]:
    issues = []
    if ref["mean_current_a"] and x["mean_current_a"] is not None:
        d = abs(x["mean_current_a"] - ref["mean_current_a"]) / ref["mean_current_a"]
        if d > CURRENT_TOL:
            issues.append(f"load current {x['mean_current_a']:.3f} A differs from reference "
                          f"{ref['mean_current_a']:.3f} A by {d:.0%} (> {CURRENT_TOL:.0%})")
    if ref["mean_temperature_c"] is not None and x["mean_temperature_c"] is not None \
            and abs(x["mean_temperature_c"] - ref["mean_temperature_c"]) > TEMP_TOL_C:
        issues.append(f"mean temperature {x['mean_temperature_c']:.1f} degC differs from reference "
                      f"{ref['mean_temperature_c']:.1f} degC by > {TEMP_TOL_C:g} degC")
    if abs(x["coverage"]["cutoff_v"] - ref["coverage"]["cutoff_v"]) > 1e-6:
        issues.append("different cutoff voltage from reference")
    return issues


def battery_aging(conn: sqlite3.Connection, battery: dict, s: Settings) -> dict[str, Any]:
    bid = battery["battery_id"]
    rated = battery.get("rated_capacity_ah")
    rated_mah = rated * 1000 if rated else None
    rows = conn.execute(f"SELECT {SESSION_COLS} FROM discharge_sessions WHERE battery_id=? ORDER BY start_ms, id",
                        (bid,)).fetchall()
    sessions = []
    for r in rows:
        x = session_out(r, s, battery.get("cutoff_voltage_v"), rated)
        x.update(_session_means(conn, bid, dict(r), s))
        x["mean_loaded_voltage_v_energy"] = (x["energy_wh"] / (x["charge_mah"] / 1000)) if x["charge_mah"] > 0 and \
            x["energy_missing_s"] == 0 else None
        x["c_rate"] = (x["mean_current_a"] / rated) if rated and x["mean_current_a"] else None
        x["full_test_issues"] = _full_test_issues(x)
        sessions.append(x)

    full = [x for x in sessions if not x["full_test_issues"]]
    ref = full[0] if full else None
    comparable = []
    for x in sessions:
        if x["full_test_issues"]:
            x["comparable"], x["comparability_issues"] = False, []
            continue
        issues = _comparability_issues(x, ref) if x is not ref else []
        x["comparable"], x["comparability_issues"] = not issues, issues
        if not issues:
            comparable.append(x)

    capacity_series = [{"index": i + 1, "session_id": x["id"], "start_ts": x["start_ts"],
                        "capacity_mah": x["charge_mah"], "uncertainty_mah": x["uncertainty_mah"],
                        "energy_wh": x["energy_wh"], "mean_current_a": x["mean_current_a"],
                        "mean_temperature_c": x["mean_temperature_c"],
                        "retention_pct": 100.0 * x["charge_mah"] / ref["charge_mah"] if ref else None}
                       for i, x in enumerate(comparable)]
    if not full:
        status = "insufficient_data"
        reason = "No full, uninterrupted, gap-free discharge test from a rested full charge has been recorded."
    elif len(comparable) < MIN_TREND_POINTS:
        status = "insufficient_data"
        reason = (f"Only {len(comparable)} comparable full test(s); at least {MIN_TREND_POINTS} are needed for "
                  "capacity retention.")
    else:
        status = "ok"
        reason = None

    total_mah = sum(x["charge_mah"] for x in sessions)
    trends = [{"session_id": x["id"], "start_ts": x["start_ts"], "start_ms": x["start_ms"] if "start_ms" in x else None,
               "mean_temperature_c": x["mean_temperature_c"], "max_temperature_c": x["max_temperature_c"],
               "mean_loaded_voltage_v": x["mean_loaded_voltage_v"], "min_voltage_v": x["min_voltage_v"],
               "kind": "comparable_full" if x.get("comparable") else ("full" if not x["full_test_issues"] else "partial")}
              for x in sessions]
    return {
        "battery_id": bid,
        "label": battery.get("label"),
        "source": battery.get("source"),
        "rated_capacity_mah": rated_mah,
        "rated_note": "Manufacturer label rating. Not used as the capacity reference.",
        "totals": {
            "n_sessions": len(sessions),
            "delivered_mah": total_mah,
            "delivered_wh": sum(x["energy_wh"] for x in sessions),
            "discharge_duration_s": sum(x["duration_s"] for x in sessions),
            "equivalent_full_cycles": (total_mah / rated_mah) if rated_mah else None,
        },
        "n_full_tests": len(full),
        "n_comparable_tests": len(comparable),
        "reference": None if ref is None else {
            "session_id": ref["id"], "start_ts": ref["start_ts"], "capacity_mah": ref["charge_mah"],
            "mean_current_a": ref["mean_current_a"], "c_rate": ref["c_rate"],
            "mean_temperature_c": ref["mean_temperature_c"], "cutoff_v": ref["coverage"]["cutoff_v"]},
        "retention": {
            "status": status, "reason": reason,
            "latest_pct": capacity_series[-1]["retention_pct"] if status == "ok" else None,
            "definition": "Measured capacity relative to this battery's first comparable full test. "
                          "Not absolute SoH and not relative to the label rating.",
        },
        "capacity_series": capacity_series,
        "trends": trends,
        "sessions": [{
            "session_id": x["id"], "start_ts": x["start_ts"], "end_ts": x["end_ts"], "status": x["status"],
            "end_reason": x["end_reason"], "charge_mah": x["charge_mah"], "energy_wh": x["energy_wh"],
            "duration_s": x["duration_s"], "mean_current_a": x["mean_current_a"], "c_rate": x["c_rate"],
            "mean_temperature_c": x["mean_temperature_c"], "mean_loaded_voltage_v": x["mean_loaded_voltage_v"],
            "full_test": not x["full_test_issues"], "full_test_issues": x["full_test_issues"],
            "comparable": x.get("comparable", False), "comparability_issues": x.get("comparability_issues", []),
            "is_reference": ref is not None and x["id"] == ref["id"],
        } for x in sessions],
        "rul": RUL_STATUS,
        "soh": {"status": "unavailable", "reason": "Absolute SoH is not measured: there is no validated initial "
                                                   "capacity for these cells."},
    }


def comparability_rules() -> dict[str, Any]:
    return {"current_tolerance_frac": CURRENT_TOL, "temperature_tolerance_c": TEMP_TOL_C,
            "max_uncertainty_frac": MAX_UNCERTAINTY_FRAC, "min_trend_points": MIN_TREND_POINTS,
            "full_test": "rested full charge -> cutoff, ended by load stop, no telemetry gaps"}
