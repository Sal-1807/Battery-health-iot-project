"""Threshold alerts.

One *active* alert exists per (battery, type); repeated violations update
``last_seen``/``occurrences`` instead of flooding the table, and the alert is
resolved automatically when a later reading no longer violates the rule.

Alerts are monitoring signals only. The backend cannot disconnect a load or stop
a charge: over/under-voltage and over-temperature protection must be done in
hardware (protected cell / BMS / cutoff circuit).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

from .db import Database
from .presence import VALID_STATES
from .schemas import Thresholds

DEFAULT_THRESHOLDS = Thresholds()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def load_thresholds(conn: sqlite3.Connection) -> Thresholds:
    row = conn.execute("SELECT value FROM settings WHERE key='thresholds'").fetchone()
    if row is None:
        return DEFAULT_THRESHOLDS
    return Thresholds(**json.loads(row["value"]))


def save_thresholds(db: Database, t: Thresholds) -> None:
    with db.write() as conn:
        conn.execute("INSERT INTO settings(key, value) VALUES('thresholds', ?) "
                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (t.model_dump_json(),))


def _raise(conn, battery_id, device_id, type_, severity, message, value=None, threshold=None,
           unit=None, experimental=False, ts=None, count=True, classification=None) -> None:
    ts = ts or now_iso()
    row = conn.execute("SELECT id, severity FROM alerts WHERE battery_id=? AND type=? AND status='active'",
                       (battery_id, type_)).fetchone()
    if row:
        conn.execute("UPDATE alerts SET last_seen=?, occurrences=occurrences+?, value=?, threshold=?, "
                     "severity=?, message=? WHERE id=?",
                     (ts, int(count), value, threshold, severity, message, row["id"]))
    else:
        conn.execute(
            "INSERT INTO alerts(battery_id, device_id, type, severity, message, value, threshold, unit, "
            "status, first_seen, last_seen, experimental, classification) VALUES (?,?,?,?,?,?,?,?, 'active', ?, ?, ?, ?)",
            (battery_id, device_id, type_, severity, message, value, threshold, unit, ts, ts, int(experimental),
             classification))


def _clear(conn, battery_id, type_, ts=None) -> None:
    conn.execute("UPDATE alerts SET status='resolved', resolved_at=? WHERE battery_id=? AND type=? AND status='active'",
                 (ts or now_iso(), battery_id, type_))


def evaluate_reading(conn: sqlite3.Connection, r: dict[str, Any], events: Sequence[str] = ()) -> None:
    """Apply threshold rules to one stored reading (dict of the readings row).

    Voltage rules run ONLY when ``cell_state`` is verified (current evidence or
    operator confirmation). For ``unverified``/``no_cell``/``unknown`` readings
    voltage alerts are neither raised nor cleared: the reading says nothing
    reliable about the cell. Thresholds themselves are unchanged.
    """
    t = load_thresholds(conn)
    b, dev = r["battery_id"], r["device_id"]
    flags = set(r.get("flags") or [])

    # ---- sensor faults
    if r.get("ds18b20_status") in ("disconnected", "not_found", "error") or "temperature_sensor_fault" in flags:
        _raise(conn, b, dev, "temperature_sensor_fault", "warning",
               "DS18B20 temperature sensor not responding (disconnected or wiring fault).")
    else:
        _clear(conn, b, "temperature_sensor_fault")

    if r.get("ina219_status") in ("error", "not_found") or "ina219_fault" in flags:
        _raise(conn, b, dev, "ina219_fault", "warning", "INA219 voltage/current sensor not responding.")
    else:
        _clear(conn, b, "ina219_fault")

    state = r.get("cell_state")
    v = r.get("voltage_v")
    if state == "no_cell":
        _raise(conn, b, dev, "battery_not_detected", "info",
               f"No usable cell measurement: INA219 input reads {v:.3f} V (outside the Li-ion range) with no current. "
               "Either no cell is connected (floating input) or the cell is deeply discharged; "
               "the INA219 cannot tell these apart. Not treated as battery voltage.",
               value=v, unit="V")
    elif state is not None and state != "unknown":
        _clear(conn, b, "battery_not_detected")

    if "cell_lost" in events:
        _raise(conn, b, dev, "cell_signal_lost", "warning",
               f"A verified cell reading dropped to {v:.3f} V (outside the Li-ion range) at rest: the cell was removed, "
               "a wire came loose, or the cell is deeply discharged. Check the setup.",
               value=v, unit="V")
    elif state in VALID_STATES:
        _clear(conn, b, "cell_signal_lost")

    # ---- temperature
    temp = r.get("temperature_c")
    if temp is not None:
        if temp >= t.temp_high_crit_c:
            _raise(conn, b, dev, "temperature_high", "critical",
                   f"Cell temperature {temp:.1f} degC >= {t.temp_high_crit_c:g} degC. Disconnect the load and let the cell cool.",
                   temp, t.temp_high_crit_c, "degC")
        elif temp >= t.temp_high_warn_c:
            _raise(conn, b, dev, "temperature_high", "warning",
                   f"Cell temperature {temp:.1f} degC >= {t.temp_high_warn_c:g} degC.", temp, t.temp_high_warn_c, "degC")
        else:
            _clear(conn, b, "temperature_high")
        if temp <= t.temp_low_warn_c:
            _raise(conn, b, dev, "temperature_low", "warning",
                   f"Cell temperature {temp:.1f} degC <= {t.temp_low_warn_c:g} degC.", temp, t.temp_low_warn_c, "degC")
        else:
            _clear(conn, b, "temperature_low")

    # ---- voltage: only on verified cell readings (see docstring)
    if v is not None and state in VALID_STATES:
        if v <= t.voltage_low_crit_v:
            _raise(conn, b, dev, "voltage_low", "critical",
                   f"Cell voltage {v:.3f} V <= {t.voltage_low_crit_v:g} V. Stop discharging.", v, t.voltage_low_crit_v, "V", classification="valid")
        elif v <= t.voltage_low_warn_v:
            _raise(conn, b, dev, "voltage_low", "warning",
                   f"Cell voltage {v:.3f} V <= {t.voltage_low_warn_v:g} V.", v, t.voltage_low_warn_v, "V", classification="valid")
        else:
            _clear(conn, b, "voltage_low")
        if v >= t.voltage_high_crit_v:
            _raise(conn, b, dev, "voltage_high", "critical",
                   f"Cell voltage {v:.3f} V >= {t.voltage_high_crit_v:g} V (overcharge range).", v, t.voltage_high_crit_v, "V", classification="valid")
        elif v >= t.voltage_high_warn_v:
            _raise(conn, b, dev, "voltage_high", "warning",
                   f"Cell voltage {v:.3f} V >= {t.voltage_high_warn_v:g} V.", v, t.voltage_high_warn_v, "V", classification="valid")
        else:
            _clear(conn, b, "voltage_high")

    # ---- current magnitude
    i = r.get("current_a")
    if i is not None:
        if abs(i) >= t.current_high_warn_a:
            _raise(conn, b, dev, "current_high", "warning",
                   f"|Current| {abs(i):.3f} A >= {t.current_high_warn_a:g} A.", abs(i), t.current_high_warn_a, "A")
        else:
            _clear(conn, b, "current_high")

    # Any reading means the device is talking again.
    _clear(conn, b, "device_offline")


def evaluate_prediction(conn: sqlite3.Connection, battery_id: str, status: str, soh: Optional[float]) -> None:
    if soh is None or status not in ("experimental", "nasa_in_sample"):
        return
    t = load_thresholds(conn)
    tag = "experimental estimate" if status == "experimental" else "NASA replay, in-sample"
    if soh <= t.soh_low_crit_pct:
        _raise(conn, battery_id, None, "soh_low", "critical",
               f"Predicted SoH {soh:.1f} % <= {t.soh_low_crit_pct:g} % ({tag}).", soh, t.soh_low_crit_pct, "%",
               experimental=True)
    elif soh <= t.soh_low_warn_pct:
        _raise(conn, battery_id, None, "soh_low", "warning",
               f"Predicted SoH {soh:.1f} % <= {t.soh_low_warn_pct:g} % ({tag}).", soh, t.soh_low_warn_pct, "%",
               experimental=True)
    else:
        _clear(conn, battery_id, "soh_low")


def refresh_offline(db: Database) -> None:
    """Raise/clear device_offline for hardware/simulation batteries based on last-seen time."""
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    with db.write() as conn:
        t = load_thresholds(conn)
        rows = conn.execute(
            "SELECT b.battery_id, MAX(r.id) AS last_id FROM batteries b JOIN readings r ON r.battery_id=b.battery_id "
            "WHERE b.source IN ('hardware','simulation') GROUP BY b.battery_id").fetchall()
        for row in rows:
            last = conn.execute("SELECT received_at, device_id FROM readings WHERE id=?", (row["last_id"],)).fetchone()
            received_ms = int(datetime.fromisoformat(last["received_at"].replace("Z", "+00:00")).timestamp() * 1000)
            age_s = (now_ms - received_ms) / 1000
            if age_s > t.offline_after_s:
                _raise(conn, row["battery_id"], last["device_id"], "device_offline", "warning",
                       f"No telemetry for {age_s:.0f} s (limit {t.offline_after_s:g} s).", age_s, t.offline_after_s, "s",
                       count=False)
