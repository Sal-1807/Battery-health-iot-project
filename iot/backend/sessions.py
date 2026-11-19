"""Measured discharge sessions: charge (mAh) and energy (Wh) delivered while a
load draws current, integrated live from telemetry.

What a session measures - and what it does not
----------------------------------------------
A session's ``charge_mah`` is the charge that flowed out of the cell DURING
that session, as measured by the INA219. It is not the cell's capacity, not
its state of charge and not its state of health: a partial discharge from an
unknown starting charge says nothing about how much the full cell holds.

Integration rules (no assumed current, ever)
--------------------------------------------
* A reading is "on" when current_a >= session_on_a (positive = discharge).
  Readings below it - including near-zero noise and negative (charging)
  current - never contribute charge.
* A session starts on two consecutive on-readings no more than max_gap_s apart
  (a single noise spike cannot start one) and begins at the first of them.
* Charge/energy are integrated with the trapezoid rule ONLY between two
  consecutive on-readings no more than max_gap_s apart.
* An interval where the load switched on/off between readings is NOT
  integrated; its largest possible contribution (I_on * dt) is added to
  ``uncertainty_mah`` instead, so the true value lies in
  [charge_mah, charge_mah + uncertainty_mah] (ignoring sensor error).
* An interval longer than max_gap_s is a telemetry gap: nothing is integrated,
  its length is recorded in ``gap_s``.
* The session ends after session_stop_hold_s of off-readings
  (``load_stopped``), on a gap longer than session_end_gap_s
  (``telemetry_gap``), or when the device stays silent that long
  (``telemetry_lost``). Its statistics are kept; the next session starts from 0.
* Energy uses each reading's ``power_w`` (INA219 power register, or V*|I|
  computed by the backend). Intervals without power on both ends are not
  integrated (``energy_missing_s``).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional

from .config import Settings
from .db import row_to_dict

SESSION_COLS = (
    "id, battery_id, status, start_ts, start_ms, end_ts, end_ms, last_ms, end_reason, charge_mah, energy_wh, "
    "uncertainty_mah, integrated_s, gap_s, n_gaps, energy_missing_s, n_readings, n_on, start_voltage_v, "
    "rest_voltage_before_v, end_voltage_v, min_voltage_v, peak_current_a, max_temperature_c, last_current_a, "
    "last_power_w, last_on, last_on_ms, created_at"
)


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _on(i: Optional[float], s: Settings) -> bool:
    return i is not None and i >= s.session_on_a


def _mah(i1: float, i2: float, dt_s: float) -> float:
    return (i1 + i2) / 2.0 * dt_s / 3.6          # A*s -> mAh  (1 mAh = 3.6 A*s)


def _wh(p1: float, p2: float, dt_s: float) -> float:
    return (p1 + p2) / 2.0 * dt_s / 3600.0


def active_session(conn: sqlite3.Connection, battery_id: str) -> Optional[dict]:
    return row_to_dict(conn.execute(f"SELECT {SESSION_COLS} FROM discharge_sessions WHERE battery_id=? AND "
                                    f"status='active' ORDER BY id DESC LIMIT 1", (battery_id,)).fetchone())


def _end(conn: sqlite3.Connection, sess: dict, reason: str) -> None:
    end_ms = sess["last_on_ms"] or sess["start_ms"]
    conn.execute("UPDATE discharge_sessions SET status='ended', end_ms=?, end_ts=?, end_reason=? WHERE id=?",
                 (end_ms, _iso(end_ms), reason, sess["id"]))


def _save(conn: sqlite3.Connection, sess: dict) -> None:
    cols = [c for c in sess if c not in ("id",)]
    conn.execute(f"UPDATE discharge_sessions SET {', '.join(f'{c}=?' for c in cols)} WHERE id=?",
                 [sess[c] for c in cols] + [sess["id"]])


def _absorb_on_reading(sess: dict, rec: dict) -> None:
    v, i, t = rec.get("voltage_v"), rec.get("current_a"), rec.get("temperature_c")
    sess["n_on"] += 1
    sess["last_on_ms"] = rec["ts_ms"]
    if i is not None:
        sess["peak_current_a"] = max(sess["peak_current_a"] or 0.0, i)
    if v is not None:
        sess["end_voltage_v"] = v
        sess["min_voltage_v"] = v if sess["min_voltage_v"] is None else min(sess["min_voltage_v"], v)
    if t is not None:
        sess["max_temperature_c"] = t if sess["max_temperature_c"] is None else max(sess["max_temperature_c"], t)


def update_session(conn: sqlite3.Connection, battery_id: str, rec: dict[str, Any], prev: Optional[dict[str, Any]],
                   s: Settings) -> Optional[dict]:
    """Advance the battery's discharge session with one stored reading.

    ``rec``/``prev`` need ts_ms, current_a, power_w, voltage_v, temperature_c.
    Returns an event dict when a session starts or ends, else None.
    """
    t, i = rec["ts_ms"], rec.get("current_a")
    on = _on(i, s)
    sess = active_session(conn, battery_id)
    event = None

    if sess is not None:
        if t <= sess["last_ms"]:
            return None                                   # out-of-order/back-filled: never re-integrate
        dt = (t - sess["last_ms"]) / 1000.0
        if dt > s.session_end_gap_s:
            _end(conn, sess, "telemetry_gap")
            event, sess = {"event": "session_ended", "session_id": sess["id"], "reason": "telemetry_gap"}, None
        else:
            last_on = bool(sess["last_on"])
            if dt > s.max_gap_s:
                sess["gap_s"] += dt
                sess["n_gaps"] += 1
            elif last_on and on:
                sess["charge_mah"] += _mah(sess["last_current_a"], i, dt)
                sess["integrated_s"] += dt
                if sess["last_power_w"] is not None and rec.get("power_w") is not None:
                    sess["energy_wh"] += _wh(sess["last_power_w"], rec["power_w"], dt)
                else:
                    sess["energy_missing_s"] += dt
            elif last_on != on:
                i_on = sess["last_current_a"] if last_on else i
                sess["uncertainty_mah"] += i_on * dt / 3.6
            sess["n_readings"] += 1
            if on:
                _absorb_on_reading(sess, rec)
            sess["last_ms"], sess["last_current_a"], sess["last_power_w"], sess["last_on"] = \
                t, i, rec.get("power_w"), int(on)
            _save(conn, sess)
            if not on and (t - sess["last_on_ms"]) / 1000.0 >= s.session_stop_hold_s:
                _end(conn, sess, "load_stopped")
                return {"event": "session_ended", "session_id": sess["id"], "reason": "load_stopped"}
            return event

    # ---- no active session: start on two consecutive on-readings
    if not (on and prev is not None and _on(prev.get("current_a"), s)):
        return event
    dt = (t - prev["ts_ms"]) / 1000.0
    if not 0 < dt <= s.max_gap_s:
        return event
    rest = conn.execute(
        "SELECT voltage_v FROM readings WHERE battery_id=? AND ts_ms<? AND ts_ms>=? AND voltage_v IS NOT NULL "
        "AND (current_a IS NULL OR ABS(current_a) < ?) ORDER BY ts_ms DESC LIMIT 1",
        (battery_id, prev["ts_ms"], prev["ts_ms"] - int(s.tail_s * 1000), s.session_on_a)).fetchone()
    # The load switched on somewhere between the last off-reading and prev: that
    # charge is not integrated, but bounded (same rule as the stop transition).
    before = conn.execute("SELECT ts_ms, current_a FROM readings WHERE battery_id=? AND ts_ms<? "
                          "ORDER BY ts_ms DESC LIMIT 1", (battery_id, prev["ts_ms"])).fetchone()
    start_unc = 0.0
    if before is not None and not _on(before["current_a"], s) \
            and (prev["ts_ms"] - before["ts_ms"]) / 1000.0 <= s.max_gap_s:
        start_unc = prev["current_a"] * (prev["ts_ms"] - before["ts_ms"]) / 1000.0 / 3.6
    p1, p2 = prev.get("power_w"), rec.get("power_w")
    sess = {
        "battery_id": battery_id, "status": "active", "start_ts": _iso(prev["ts_ms"]), "start_ms": prev["ts_ms"],
        "end_ts": None, "end_ms": None, "last_ms": t, "end_reason": None,
        "charge_mah": _mah(prev["current_a"], i, dt), "energy_wh": _wh(p1, p2, dt) if p1 is not None and p2 is not None else 0.0,
        "uncertainty_mah": start_unc, "integrated_s": dt, "gap_s": 0.0, "n_gaps": 0,
        "energy_missing_s": 0.0 if p1 is not None and p2 is not None else dt,
        "n_readings": 2, "n_on": 0, "start_voltage_v": prev.get("voltage_v"),
        "rest_voltage_before_v": rest["voltage_v"] if rest else None,
        "end_voltage_v": None, "min_voltage_v": None, "peak_current_a": None, "max_temperature_c": None,
        "last_current_a": i, "last_power_w": p2, "last_on": 1, "last_on_ms": t,
        "created_at": _iso(int(datetime.now(timezone.utc).timestamp() * 1000)),
    }
    _absorb_on_reading(sess, prev)
    _absorb_on_reading(sess, rec)
    cols = list(sess)
    cur = conn.execute(f"INSERT INTO discharge_sessions({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                       [sess[c] for c in cols])
    return {"event": "session_started", "session_id": cur.lastrowid}


def close_stale_sessions(conn: sqlite3.Connection, now_ms: int, s: Settings) -> int:
    """End sessions whose device stopped reporting for longer than session_end_gap_s."""
    rows = conn.execute(f"SELECT {SESSION_COLS} FROM discharge_sessions WHERE status='active' AND ? - last_ms > ?",
                        (now_ms, int(s.session_end_gap_s * 1000))).fetchall()
    for r in rows:
        _end(conn, dict(r), "telemetry_lost")
    return len(rows)


def session_out(row: Optional[sqlite3.Row | dict], s: Settings, cutoff_v: Optional[float],
                rated_capacity_ah: Optional[float]) -> Optional[dict]:
    """Public view of a session, with derived figures and an explicit scope statement."""
    if row is None:
        return None
    d = dict(row)
    for k in ("last_current_a", "last_power_w", "last_on"):
        d.pop(k, None)
    end_ms = d["end_ms"] if d["status"] == "ended" else d["last_on_ms"]
    d["duration_s"] = (end_ms - d["start_ms"]) / 1000.0 if end_ms else 0.0
    d["mean_current_a"] = (d["charge_mah"] * 3.6 / d["integrated_s"]) if d["integrated_s"] else None
    cutoff = cutoff_v or s.default_cutoff_v
    started_full = d["rest_voltage_before_v"] is not None and d["rest_voltage_before_v"] >= s.full_charge_v
    reached_cutoff = d["min_voltage_v"] is not None and d["min_voltage_v"] <= cutoff + 0.02
    d["coverage"] = {
        "started_from_full_rest_voltage": started_full,
        "reached_cutoff_voltage": reached_cutoff,
        "no_telemetry_gaps": d["n_gaps"] == 0,
        "full_charge_v": s.full_charge_v,
        "cutoff_v": cutoff,
    }
    full_range = d["status"] == "ended" and started_full and reached_cutoff and d["n_gaps"] == 0
    d["kind"] = "full_range_unvalidated" if full_range else "partial"
    d["rated_capacity_mah"] = rated_capacity_ah * 1000 if rated_capacity_ah else None
    d["interpretation"] = (
        "Charge delivered during this session only. Not the cell's total capacity, state of charge or state of health."
        if not full_range else
        "Covers rest-at-full-charge to cutoff without gaps, but the test protocol (charge method, rest time, load, "
        "temperature) has not been validated, so this is not a validated capacity measurement."
    )
    return d


def battery_stats(conn: sqlite3.Connection, battery: dict, s: Settings) -> dict:
    """Per-battery totals from measured sessions.

    * equivalent_full_cycles (EFC) = total discharged charge / RATED capacity of
      THIS battery. A throughput measure: 10 sessions of 300 mAh on a 3000 mAh
      cell = 1.0 EFC. It is not a count of completed cycles.
    * completed_cycles = sessions that ran from a rested full charge to the
      cutoff voltage without telemetry gaps (kind full_range_unvalidated), i.e.
      a completed experimental charge-discharge cycle. A short or partial
      session never counts.
    * capacity_measurements = the charge of those completed cycles (an
      unvalidated protocol; reported as measured mAh, never as SoH).
    """
    rows = conn.execute(f"SELECT {SESSION_COLS} FROM discharge_sessions WHERE battery_id=? ORDER BY id",
                        (battery["battery_id"],)).fetchall()
    rated = battery.get("rated_capacity_ah")
    out = [session_out(r, s, battery.get("cutoff_voltage_v"), rated) for r in rows]
    total = sum(x["charge_mah"] for x in out)
    full = [x for x in out if x["kind"] == "full_range_unvalidated"]
    return {
        "n_sessions": len(out),
        "n_partial_sessions": sum(1 for x in out if x["kind"] == "partial"),
        "total_discharged_mah": total,
        "total_energy_wh": sum(x["energy_wh"] for x in out),
        "rated_capacity_mah": rated * 1000 if rated else None,
        "equivalent_full_cycles": (total / (rated * 1000)) if rated else None,
        "completed_cycles": len(full),
        "capacity_measurements": [{"session_id": x["id"], "start_ts": x["start_ts"], "charge_mah": x["charge_mah"],
                                   "uncertainty_mah": x["uncertainty_mah"], "energy_wh": x["energy_wh"]} for x in full],
        "last_session_start": out[-1]["start_ts"] if out else None,
        "notes": "EFC = total measured discharge / this battery's rated capacity (throughput, not a cycle count). "
                 "Completed cycles = rested-full to cutoff without gaps (unvalidated protocol).",
    }
