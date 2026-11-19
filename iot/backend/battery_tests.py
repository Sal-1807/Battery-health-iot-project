"""Battery Test Manager: records and supervises discharge experiments.

IMPORTANT - what this module does NOT do
----------------------------------------
A test is a software record. Creating, starting, pausing, completing or
aborting a test changes ONLY that record. Nothing here (and nothing in the
firmware) can connect, disconnect or switch the battery or the load resistor,
and there is no automatic voltage cutoff or battery protection. The operator
must physically disconnect the load when a test stops. Every API response
repeats this.

States
------
not_started -> ready -> running <-> paused -> completed
any non-final state -> aborted
``ready`` requires a complete checklist and the battery selected as connected
on a device (so readings are attributed to it).

Results
-------
Computed from the battery's own readings inside the test's RUNNING intervals,
with the same rules as discharge sessions: trapezoid integration only between
consecutive load-on readings (>= session_on_a) at most max_gap_s apart; no
current is assumed across gaps or pauses.

Validity for full-capacity / cycle analysis
-------------------------------------------
Only when ALL of these hold: state completed; checklist complete; rested at a
full charge (>= full_charge_v, no load current) at the start; loaded voltage
reached the test's cutoff; no telemetry gaps; never paused; the battery stayed
selected on the same device for the whole test; <= MAX_MISSING_FRAC readings
with a missing measurement; enough data (>= MIN_READINGS). Otherwise the test is
kept and shown, with the failing checks, but never counted as a full cycle.
"""

from __future__ import annotations

import csv
import io
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import HTTPException

from .assignments import battery_assignment
from .config import Settings
from .sessions import _mah, _wh

STATES = ("not_started", "ready", "running", "paused", "completed", "aborted")
FINAL = ("completed", "aborted")
CHECKLIST_ITEMS = (
    "battery_identity_confirmed",
    "load_rating_verified",
    "supervisor_approval",
    "disconnect_procedure_confirmed",
    "voltage_cutoff_confirmed",
)
CUTOFF_RANGE_V = (2.5, 3.3)          # plausible Li-ion end-of-discharge window for a supervised test
MAX_MISSING_FRAC = 0.02
MIN_READINGS = 30
REST_LOOKBACK_S = 120

HARDWARE_NOTICE = ("Software test state only. This did not connect, disconnect or switch the battery or the load, "
                   "and there is no automatic voltage cutoff or battery protection.")
DISCONNECT_WARNING = "Physically disconnect the discharge load now. The software cannot do it."


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _load(conn: sqlite3.Connection, uid: str) -> dict:
    row = conn.execute("SELECT * FROM battery_tests WHERE test_uid=?", (uid,)).fetchone()
    if row is None:
        raise HTTPException(404, f"test '{uid}' not found")
    d = dict(row)
    for k in ("checklist", "history", "final_results"):
        d[k] = json.loads(d[k]) if d.get(k) else ({} if k != "history" else [])
    return d


def _save(conn: sqlite3.Connection, t: dict) -> None:
    conn.execute("UPDATE battery_tests SET state=?, device_id=?, checklist=?, history=?, started_ms=?, ended_ms=?, "
                 "abort_reason=?, final_results=? WHERE id=?",
                 (t["state"], t["device_id"], json.dumps(t["checklist"]), json.dumps(t["history"]), t["started_ms"],
                  t["ended_ms"], t["abort_reason"], json.dumps(t["final_results"]) if t["final_results"] else None,
                  t["id"]))


def _event(t: dict, state: str, ms: int, note: Optional[str] = None) -> None:
    t["history"].append({"state": state, "ms": ms, "ts": _iso(ms), "note": note})
    t["state"] = state


# ------------------------------------------------------------------ checklist
def checklist_problems(c: dict) -> list[str]:
    p = [f"'{k.replace('_', ' ')}' not confirmed" for k in CHECKLIST_ITEMS if not c.get(k)]
    if not (c.get("supervisor_name") or "").strip():
        p.append("supervisor name missing")
    cut = c.get("cutoff_v")
    if cut is None:
        p.append("cutoff voltage not set")
    elif not CUTOFF_RANGE_V[0] <= cut <= CUTOFF_RANGE_V[1]:
        p.append(f"cutoff {cut} V outside {CUTOFF_RANGE_V[0]}-{CUTOFF_RANGE_V[1]} V")
    r, w = c.get("load_resistance_ohm"), c.get("load_power_rating_w")
    if not r or not w:
        p.append("load resistance and power rating not entered")
    else:
        need = 4.2 ** 2 / r                         # worst-case dissipation at a full cell
        if w < need:
            p.append(f"load rated {w} W but dissipates up to {need:.2f} W at 4.2 V")
    return p


def update_checklist(conn: sqlite3.Connection, uid: str, c: dict) -> dict:
    t = _load(conn, uid)
    if t["state"] not in ("not_started", "ready"):
        raise HTTPException(409, f"checklist is locked once a test is {t['state']}")
    t["checklist"] = {**c, "updated_at": _iso(_now_ms())}
    problems = checklist_problems(t["checklist"])
    a = battery_assignment(conn, t["battery_id"])
    if not problems and a is None:
        problems.append("battery is not selected as connected on any device (Batteries page)")
    if not problems and t["state"] == "not_started":
        t["device_id"] = a["device_id"]
        _event(t, "ready", _now_ms(), f"checklist complete; device {a['device_id']}")
    elif problems and t["state"] == "ready":
        _event(t, "not_started", _now_ms(), "checklist no longer complete")
    _save(conn, t)
    return {"test_uid": uid, "state": t["state"], "checklist_problems": problems}


# ------------------------------------------------------------------ lifecycle
def pending_disconnect(conn: sqlite3.Connection, battery_id: str, device_id: Optional[str]) -> Optional[str]:
    """A finished quick test whose physical load disconnection has not been confirmed."""
    row = conn.execute("SELECT test_uid FROM battery_tests WHERE kind='quick' AND state IN ('completed','aborted') "
                       "AND started_ms IS NOT NULL AND disconnect_confirmed_at IS NULL "
                       "AND (battery_id=? OR (device_id IS NOT NULL AND device_id=?)) ORDER BY id DESC LIMIT 1",
                       (battery_id, device_id)).fetchone()
    return row["test_uid"] if row else None


def create_test(conn: sqlite3.Connection, battery_id: str, notes: Optional[str], kind: str = "quick",
                duration_s: Optional[int] = None) -> dict:
    b = conn.execute("SELECT battery_id, managed FROM batteries WHERE battery_id=?", (battery_id,)).fetchone()
    if b is None:
        raise HTTPException(404, f"battery '{battery_id}' not found")
    if not b["managed"]:
        raise HTTPException(400, "tests can only be created for physical battery profiles")
    a = battery_assignment(conn, battery_id)
    pending = pending_disconnect(conn, battery_id, a["device_id"] if a else None)
    if pending:
        raise HTTPException(409, f"confirm that the load was physically disconnected after test {pending} "
                                 "before starting another test")
    if kind == "quick":
        duration_s = duration_s or QUICK_DURATION_S
    now = _now_ms()
    day = _iso(now)[:10].replace("-", "")
    n = conn.execute("SELECT COUNT(*) AS n FROM battery_tests WHERE battery_id=?", (battery_id,)).fetchone()["n"] + 1
    uid = f"T-{day}-{battery_id}-{n:03d}"
    while conn.execute("SELECT 1 FROM battery_tests WHERE test_uid=?", (uid,)).fetchone():
        n += 1
        uid = f"T-{day}-{battery_id}-{n:03d}"
    history = [{"state": "not_started", "ms": now, "ts": _iso(now), "note": "created"}]
    conn.execute("INSERT INTO battery_tests(test_uid, battery_id, state, created_at, notes, checklist, history, kind, "
                 "duration_s) VALUES (?,?,?,?,?,?,?,?,?)",
                 (uid, battery_id, "not_started", _iso(now), notes, "{}", json.dumps(history), kind, duration_s))
    return {"test_uid": uid, "battery_id": battery_id, "state": "not_started", "kind": kind, "duration_s": duration_s,
            "hardware_notice": HARDWARE_NOTICE}


ALLOWED = {
    "start": ("ready",),
    "pause": ("running",),
    "resume": ("paused",),
    "complete": ("running", "paused"),
    "abort": ("not_started", "ready", "running", "paused"),
}
TARGET = {"start": "running", "pause": "paused", "resume": "running", "complete": "completed", "abort": "aborted"}


def transition(conn: sqlite3.Connection, uid: str, action: str, reason: Optional[str], s: Settings,
               now_ms: Optional[int] = None) -> dict:
    t = _load(conn, uid)
    if action not in ALLOWED:
        raise HTTPException(400, f"unknown action '{action}'")
    if t["state"] not in ALLOWED[action]:
        raise HTTPException(409, f"cannot {action} a test that is {t['state'].replace('_', ' ')}")
    now = now_ms or _now_ms()
    if action == "start":
        problems = checklist_problems(t["checklist"])
        a = battery_assignment(conn, t["battery_id"])
        if problems:
            raise HTTPException(409, "checklist incomplete: " + "; ".join(problems))
        if a is None or a["device_id"] != t["device_id"]:
            raise HTTPException(409, f"{t['battery_id']} is no longer selected on device {t['device_id']}; "
                                     "re-check the battery identity")
        pending = pending_disconnect(conn, t["battery_id"], t["device_id"])
        if pending:
            raise HTTPException(409, f"confirm that the load was physically disconnected after test {pending} "
                                     "before starting another test")
        busy = conn.execute("SELECT test_uid FROM battery_tests WHERE state IN ('running','paused') AND "
                            "(battery_id=? OR device_id=?) AND test_uid!=?",
                            (t["battery_id"], t["device_id"], uid)).fetchone()
        if busy:
            raise HTTPException(409, f"test {busy['test_uid']} is already active on this battery or device")
        t["started_ms"] = now
    if action == "abort":
        t["abort_reason"] = reason or "aborted by operator"
    _event(t, TARGET[action], now, reason)
    if action in ("complete", "abort"):
        t["ended_ms"] = now
        if t["started_ms"] is not None:
            t["final_results"] = compute_results(conn, t, s, now)
    _save(conn, t)
    out = {"test_uid": uid, "state": t["state"], "hardware_notice": HARDWARE_NOTICE}
    if action in ("pause", "complete", "abort"):
        out["warning"] = DISCONNECT_WARNING
    return out


# ------------------------------------------------------------------ results
def running_intervals(t: dict, now_ms: int) -> list[tuple[int, int]]:
    out, start = [], None
    for e in t["history"]:
        if e["state"] == "running":
            start = e["ms"]
        elif start is not None and e["state"] in ("paused", "completed", "aborted"):
            out.append((start, e["ms"]))
            start = None
    if start is not None:
        out.append((start, now_ms))
    return out


def compute_results(conn: sqlite3.Connection, t: dict, s: Settings, now_ms: Optional[int] = None) -> dict:
    now = now_ms or _now_ms()
    if t["started_ms"] is None:
        return {"available": False, "reason": "test has not started"}
    end = t["ended_ms"] or now
    wins = running_intervals(t, now)
    max_gap = s.max_gap_s * 1000

    def running(ms: int) -> Optional[int]:
        for k, (a, b) in enumerate(wins):
            if a <= ms <= b:
                return k
        return None

    rows = [dict(r) for r in conn.execute(
        "SELECT ts_ms, voltage_v, current_a, power_w, temperature_c, device_id, routed_by FROM readings "
        "WHERE battery_id=? AND ts_ms BETWEEN ? AND ? ORDER BY ts_ms", (t["battery_id"], t["started_ms"], end))]
    # initial = last AT-REST reading before the start (so voltage drop is measured from rest, not from a
    # sample taken after the load was already connected); falls back to the first reading in the window
    before = conn.execute(
        "SELECT ts_ms, voltage_v, current_a, temperature_c FROM readings WHERE battery_id=? AND ts_ms<=? AND ts_ms>=? "
        "AND voltage_v IS NOT NULL AND (current_a IS NULL OR ABS(current_a) < ?) ORDER BY ts_ms DESC LIMIT 1",
        (t["battery_id"], t["started_ms"], t["started_ms"] - REST_LOOKBACK_S * 1000, s.session_on_a)).fetchone()

    on = lambda i: i is not None and i >= s.session_on_a  # noqa: E731
    mah = wh = 0.0
    gaps, on_rows, paused_on = [], [], 0
    in_run = [r for r in rows if running(r["ts_ms"]) is not None]
    for r in rows:
        if running(r["ts_ms"]) is None and on(r["current_a"]):
            paused_on += 1
    for a, b in zip(in_run, in_run[1:]):
        if running(a["ts_ms"]) != running(b["ts_ms"]):
            continue                                       # across a pause: never integrated
        dt = (b["ts_ms"] - a["ts_ms"]) / 1000
        if b["ts_ms"] - a["ts_ms"] > max_gap:
            gaps.append({"from": _iso(a["ts_ms"]), "to": _iso(b["ts_ms"]), "duration_s": dt})
            continue
        if on(a["current_a"]) and on(b["current_a"]):
            mah += _mah(a["current_a"], b["current_a"], dt)
            if a["power_w"] is not None and b["power_w"] is not None:
                wh += _wh(a["power_w"], b["power_w"], dt)
    # no data at the start / end of a running window is a gap too
    for (a, b) in wins:
        inside = [r["ts_ms"] for r in in_run if a <= r["ts_ms"] <= b]
        first, last = (inside[0], inside[-1]) if inside else (None, None)
        if first is None:
            if b - a > max_gap:
                gaps.append({"from": _iso(a), "to": _iso(b), "duration_s": (b - a) / 1000, "note": "no readings"})
            continue
        if first - a > max_gap:
            gaps.append({"from": _iso(a), "to": _iso(first), "duration_s": (first - a) / 1000, "note": "after start"})
        if b - last > max_gap:
            gaps.append({"from": _iso(last), "to": _iso(b), "duration_s": (b - last) / 1000,
                         "note": "telemetry missing now" if t["state"] in ("running",) and b == now else "before stop"})
    on_rows = [r for r in in_run if on(r["current_a"])]
    missing = {k: sum(1 for r in in_run if r[k] is None) for k in ("voltage_v", "current_a", "temperature_c")}
    n_missing_any = sum(1 for r in in_run if r["voltage_v"] is None or r["current_a"] is None or r["temperature_c"] is None)
    elapsed = sum(b - a for a, b in wins) / 1000
    on_time = sum((b["ts_ms"] - a["ts_ms"]) / 1000 for a, b in zip(in_run, in_run[1:])
                  if running(a["ts_ms"]) == running(b["ts_ms"]) and b["ts_ms"] - a["ts_ms"] <= max_gap
                  and on(a["current_a"]) and on(b["current_a"]))
    init = before if before is not None else (in_run[0] if in_run else None)
    temps = [r["temperature_c"] for r in in_run if r["temperature_c"] is not None]
    powers = [r["power_w"] for r in on_rows if r["power_w"] is not None]
    loaded_v = [r["voltage_v"] for r in on_rows if r["voltage_v"] is not None]
    assignment_ok = conn.execute(
        "SELECT 1 FROM device_assignments WHERE device_id=? AND battery_id=? AND start_ms<=? "
        "AND (end_ms IS NULL OR end_ms>=?)", (t["device_id"], t["battery_id"], t["started_ms"], end)).fetchone() is not None
    paused = any(e["state"] == "paused" for e in t["history"])
    return {
        "available": True,
        "elapsed_s": elapsed,
        "load_on_s": on_time,
        "n_readings": len(in_run),
        "initial_voltage_v": init["voltage_v"] if init else None,
        "initial_current_a": init["current_a"] if init else None,
        "final_voltage_v": in_run[-1]["voltage_v"] if in_run else None,
        "min_loaded_voltage_v": min(loaded_v) if loaded_v else None,
        "first_loaded_voltage_v": loaded_v[0] if loaded_v else None,
        "last_loaded_voltage_v": loaded_v[-1] if loaded_v else None,
        "mean_current_a": (mah * 3.6 / on_time) if on_time else None,
        "peak_current_a": max((r["current_a"] for r in on_rows), default=None),
        "mean_power_w": (sum(powers) / len(powers)) if powers else None,
        "peak_power_w": max(powers) if powers else None,
        "initial_temperature_c": init["temperature_c"] if init else None,
        "max_temperature_c": max(temps) if temps else None,
        "final_temperature_c": temps[-1] if temps else None,
        "delivered_mah": mah,
        "delivered_wh": wh,
        "gaps": gaps,
        "missing_measurements": {**missing, "readings_with_any_missing": n_missing_any},
        "paused": paused,
        "load_current_while_paused_readings": paused_on,
        "battery_stayed_selected": assignment_ok,
        "last_reading_age_s": (now - rows[-1]["ts_ms"]) / 1000 if rows else None,
    }


def validity(t: dict, r: dict, s: Settings) -> dict:
    cut = (t["checklist"] or {}).get("cutoff_v")
    checks = []

    def add(name, ok, detail):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    add("completed", t["state"] == "completed", f"test state is {t['state'].replace('_', ' ')}")
    add("checklist complete", not checklist_problems(t["checklist"] or {}), "; ".join(checklist_problems(t["checklist"] or {})) or "all items confirmed")
    if not r.get("available"):
        add("data", False, r.get("reason", "no data"))
        return {"valid_for_full_capacity": False, "checks": checks, "classification": "no data"}
    iv, ii = r["initial_voltage_v"], r["initial_current_a"]
    add("started from rested full charge",
        iv is not None and iv >= s.full_charge_v and (ii is None or abs(ii) < s.session_on_a),
        f"initial {iv if iv is not None else '?'} V at {ii if ii is not None else '?'} A (need >= {s.full_charge_v} V at rest)")
    mv = r["min_loaded_voltage_v"]
    add("reached cutoff", mv is not None and cut is not None and mv <= cut + 0.02,
        f"lowest loaded voltage {mv if mv is not None else '?'} V vs cutoff {cut} V")
    add("no telemetry gaps", not r["gaps"], f"{len(r['gaps'])} gap(s)" if r["gaps"] else "continuous")
    add("not paused", not r["paused"], "paused during test: capacity not comparable" if r["paused"] else "never paused")
    add("battery stayed selected", r["battery_stayed_selected"],
        "same battery on the same device for the whole test" if r["battery_stayed_selected"]
        else "battery selection changed during the test (interrupted)")
    n = max(r["n_readings"], 1)
    frac = r["missing_measurements"]["readings_with_any_missing"] / n
    add("measurements complete", frac <= MAX_MISSING_FRAC, f"{frac:.1%} readings with a missing value")
    add("enough data", r["n_readings"] >= MIN_READINGS, f"{r['n_readings']} readings (need >= {MIN_READINGS})")
    ok = all(c["ok"] for c in checks)
    if ok:
        cls = "full discharge (valid for capacity / cycle analysis)"
    elif t["state"] == "aborted":
        cls = "aborted"
    elif t["state"] == "completed":
        reached = next(c for c in checks if c["check"] == "reached cutoff")["ok"]
        cls = "invalid full discharge" if reached else "partial discharge (not a full cycle)"
    else:
        cls = "in progress" if t["state"] in ("running", "paused") else "not started"
    return {"valid_for_full_capacity": ok, "checks": checks, "classification": cls}


# ------------------------------------------------------------------ Quick Test Cycles
# Protocol: 3 Quick Test Cycles per battery, each a supervised 300-second
# measurement. A Quick Test Cycle is NOT an electrochemical charge-discharge cycle
# and never changes EFC or the full-cycle count.
QUICK_DURATION_S = 300
QUICK_TARGET = 3
QUICK_LOAD_SLACK_S = 20          # load may be connected up to 20 s after the software start
AUTO_END_NOTE = ("software recording window ended automatically; this did NOT disconnect the battery or stop "
                 "the discharge current")
QUICK_MODEL_STATUS = {
    "validated_model": False,
    "message": "No validated capacity, SoH or RUL model exists for quick tests or for these physical cells. "
               "Quick-test features are provided as inputs for experimental analysis only.",
}


def finalize_due(conn: sqlite3.Connection, s: Settings, now_ms: Optional[int] = None) -> list[str]:
    """End the software recording of quick tests whose window has elapsed (results cover exactly the window)."""
    now = now_ms or _now_ms()
    done = []
    for row in conn.execute("SELECT test_uid, started_ms, duration_s FROM battery_tests WHERE kind='quick' "
                            "AND state IN ('running','paused') AND duration_s IS NOT NULL").fetchall():
        end = row["started_ms"] + row["duration_s"] * 1000
        if now >= end:
            transition(conn, row["test_uid"], "complete", AUTO_END_NOTE, s, now_ms=end)
            done.append(row["test_uid"])
    return done


def confirm_disconnect(conn: sqlite3.Connection, uid: str, s: Settings) -> dict:
    """Record the operator's statement that the load was physically removed.
    Refused while load current is still being measured; the software cannot verify it otherwise."""
    t = _load(conn, uid)
    if t["state"] not in FINAL:
        raise HTTPException(409, "the test has not ended")
    last = conn.execute("SELECT ts_ms, current_a FROM readings WHERE battery_id=? ORDER BY ts_ms DESC LIMIT 1",
                        (t["battery_id"],)).fetchone()
    recent = last is not None and (_now_ms() - last["ts_ms"]) / 1000 <= s.max_gap_s
    if recent and last["current_a"] is not None and abs(last["current_a"]) >= s.session_on_a:
        raise HTTPException(409, f"current of {last['current_a']:.3f} A is still being measured: the load is still "
                                 "connected. Disconnect it physically, then confirm again.")
    now = _iso(_now_ms())
    conn.execute("UPDATE battery_tests SET disconnect_confirmed_at=? WHERE id=?", (now, t["id"]))
    return {"test_uid": uid, "disconnect_confirmed_at": now,
            "note": "Operator confirmation recorded. " + ("No load current is being measured."
                                                          if recent else "No recent telemetry: not verifiable.")}


def quick_status(t: dict, r: dict) -> dict:
    """Classify a quick test. Only 'counted' tests are Quick Test Cycles."""
    dur = t.get("duration_s") or QUICK_DURATION_S
    checks = []

    def add(name, ok, detail):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    if t["state"] in ("not_started", "ready"):
        return {"status": "not_started", "label": "Not started", "checks": []}
    if t["state"] in ("running", "paused"):
        return {"status": "in_progress", "label": "Recording", "checks": []}
    add("completed", t["state"] == "completed", f"test {t['state']}")
    if not r.get("available"):
        add("telemetry", False, r.get("reason", "no data"))
        return {"status": "interrupted" if t["state"] == "aborted" else "invalid", "label": "Interrupted", "checks": checks}
    add("not paused", not r["paused"], "paused" if r["paused"] else "never paused")
    add("battery stayed selected", r["battery_stayed_selected"], "same battery throughout"
        if r["battery_stayed_selected"] else "battery selection changed during the test")
    add(f"full {dur} s window recorded", r["elapsed_s"] >= dur - 1, f"{r['elapsed_s']:.0f} s of {dur} s")
    add("load current for the whole window", r["load_on_s"] >= dur - QUICK_LOAD_SLACK_S and r["delivered_mah"] > 0,
        f"load current measured for {r['load_on_s']:.0f} s (need >= {dur - QUICK_LOAD_SLACK_S} s)")
    add("no telemetry gaps", not r["gaps"], f"{len(r['gaps'])} gap(s)" if r["gaps"] else "continuous")
    n = max(r["n_readings"], 1)
    frac = r["missing_measurements"]["readings_with_any_missing"] / n
    add("measurements complete", frac <= MAX_MISSING_FRAC, f"{frac:.1%} readings with a missing value")
    add("enough data", r["n_readings"] >= MIN_READINGS, f"{r['n_readings']} readings (need >= {MIN_READINGS})")
    ok = {c["check"]: c["ok"] for c in checks}
    if t["state"] == "aborted" or not ok["not paused"] or not ok["battery stayed selected"]:
        status, label = "interrupted", "Interrupted"
    elif not ok["no telemetry gaps"] or not ok["measurements complete"] or not ok["enough data"]:
        status, label = "telemetry_gap", "Telemetry gap / missing data"
    elif not ok[f"full {dur} s window recorded"] or not ok["load current for the whole window"]:
        status, label = "incomplete", "Incomplete"
    else:
        status, label = "counted", "Valid Quick Test Cycle"
    return {"status": status, "label": label, "checks": checks}


def _qt_metrics(t: dict, r: dict) -> dict:
    mah, wh = r["delivered_mah"], r["delivered_wh"]
    v0, v1 = r.get("initial_voltage_v"), r.get("final_voltage_v")
    return {
        "test_uid": t["test_uid"], "completed_at": _iso(t["ended_ms"]), "duration_s": r["elapsed_s"],
        "initial_voltage_v": v0, "final_voltage_v": v1,
        "voltage_drop_v": (v0 - v1) if v0 is not None and v1 is not None else None,
        "mean_voltage_v": (wh / (mah / 1000)) if mah else None,
        "mean_current_a": r.get("mean_current_a"), "mean_power_w": r.get("mean_power_w"),
        "max_temperature_c": r.get("max_temperature_c"),
        "temperature_rise_c": (r["max_temperature_c"] - r["initial_temperature_c"])
        if r.get("max_temperature_c") is not None and r.get("initial_temperature_c") is not None else None,
        "delivered_mah": mah, "delivered_wh": wh,
    }


QT_METRICS = ("mean_voltage_v", "mean_current_a", "mean_power_w", "max_temperature_c", "voltage_drop_v",
              "delivered_mah", "delivered_wh")


def quick_tests(conn: sqlite3.Connection, battery_id: str) -> dict:
    """Quick tests of ONE battery: counted cycles (numbered), other attempts with their
    status, protocol progress and averages over the first 3 counted cycles."""
    rows = conn.execute("SELECT test_uid FROM battery_tests WHERE battery_id=? AND kind='quick' ORDER BY id",
                        (battery_id,)).fetchall()
    cycles, attempts = [], []
    for row in rows:
        t = _load(conn, row["test_uid"])
        r = t["final_results"] if t["state"] in FINAL and t["final_results"] else {}
        q = quick_status(t, r)
        entry = {"test_uid": t["test_uid"], "state": t["state"], "status": q["status"], "label": q["label"]}
        if q["status"] == "counted":
            cycles.append({"number": len(cycles) + 1, **_qt_metrics(t, r)})
            entry["number"] = len(cycles)
        attempts.append(entry)
    protocol = cycles[:QUICK_TARGET]
    summary = {}
    for k in QT_METRICS:
        vals = [c[k] for c in protocol if c[k] is not None]
        summary[k] = {"mean": sum(vals) / len(vals), "min": min(vals), "max": max(vals)} if vals else None
    return {
        "battery_id": battery_id, "count": len(cycles), "target": QUICK_TARGET, "duration_s": QUICK_DURATION_S,
        "next_test_number": min(len(cycles) + 1, QUICK_TARGET) if len(cycles) < QUICK_TARGET else None,
        "complete": len(cycles) >= QUICK_TARGET,
        "definition": "A Quick Test Cycle is one supervised 300-second measurement completed with valid telemetry. "
                      "It is not a full charge-discharge cycle and does not change EFC.",
        "cycles": cycles, "attempts": attempts, "summary": summary, "last": cycles[-1] if cycles else None,
        "not_counted": {k: sum(1 for a in attempts if a["status"] == k)
                        for k in ("interrupted", "incomplete", "telemetry_gap", "invalid")},
        "model_status": QUICK_MODEL_STATUS,
    }


def compare_quick(per_battery: list[dict]) -> dict:
    """Side-by-side averages over each battery's 3 protocol tests, with differences from the group mean."""
    rows = []
    group = {}
    for k in QT_METRICS:
        means = [q["summary"][k]["mean"] for q in per_battery if q["summary"].get(k)]
        group[k] = sum(means) / len(means) if means else None
    for q in per_battery:
        row = {"battery_id": q["battery_id"], "count": q["count"], "target": q["target"]}
        for k in QT_METRICS:
            m = q["summary"].get(k)
            row[k] = m["mean"] if m else None
            row[f"{k}_range"] = (m["max"] - m["min"]) if m else None
            row[f"{k}_diff_pct"] = (100 * (m["mean"] - group[k]) / group[k]) if m and group[k] else None
        rows.append(row)
    return {"rows": rows, "group_mean": group,
            "note": "Averages over each battery's first 3 valid Quick Test Cycles. Differences are relative to the "
                    "mean of the batteries shown. Quick-test mAh is set by load current x 300 s; it is not a capacity."}


QT_FEATURES = ["battery_id", "rated_capacity_mah", "number", "test_uid", "completed_at", "duration_s",
               "initial_voltage_v", "final_voltage_v", "voltage_drop_v", "mean_voltage_v", "mean_current_a",
               "mean_power_w", "max_temperature_c", "temperature_rise_c", "delivered_mah", "delivered_wh"]


def test_view(conn: sqlite3.Connection, t: dict, s: Settings) -> dict:
    final = t["state"] in FINAL and t["final_results"]
    r = t["final_results"] if final else compute_results(conn, t, s)
    a = battery_assignment(conn, t["battery_id"])
    last = conn.execute("SELECT ts, ts_ms, current_a, voltage_v, cell_state FROM readings WHERE battery_id=? "
                        "ORDER BY ts_ms DESC LIMIT 1", (t["battery_id"],)).fetchone()
    age = (_now_ms() - last["ts_ms"]) / 1000 if last else None
    if last is None or age > s.max_gap_s:
        hw = "no recent telemetry: physical state unknown"
    elif last["current_a"] is not None and last["current_a"] >= s.session_on_a:
        hw = f"load current {last['current_a']:.3f} A measured: a load appears to be connected"
    elif last["current_a"] is not None and last["current_a"] <= -s.session_on_a:
        hw = (f"current {last['current_a']:.3f} A measured (negative): something is connected - check whether "
              "INA219 VIN+/VIN- are reversed; negative current is not counted as discharge")
    else:
        hw = "no load current measured"
    return {
        "test_uid": t["test_uid"], "battery_id": t["battery_id"], "device_id": t["device_id"], "state": t["state"],
        "created_at": t["created_at"], "started_at": _iso(t["started_ms"]) if t["started_ms"] else None,
        "ended_at": _iso(t["ended_ms"]) if t["ended_ms"] else None, "notes": t["notes"],
        "abort_reason": t["abort_reason"], "checklist": t["checklist"],
        "checklist_problems": checklist_problems(t["checklist"] or {}), "history": t["history"],
        "kind": t.get("kind") or "supervised", "duration_s": t.get("duration_s"),
        "results": r, "results_frozen": bool(final), "validity": validity(t, r, s),
        "quick_test": _quick_view(conn, t, r),
        "hardware_state": {"description": hw, "last_reading_ts": last["ts"] if last else None,
                           "battery_selected_on": a["device_id"] if a else None},
        "hardware_notice": HARDWARE_NOTICE, "disconnect_warning": DISCONNECT_WARNING,
        "soh": "unavailable", "rul": "unavailable",
    }


def _quick_view(conn: sqlite3.Connection, t: dict, r: dict) -> Optional[dict]:
    if t.get("kind") != "quick":
        return None
    q = quick_status(t, r)
    number = None
    if q["status"] == "counted":
        number = next((c["number"] for c in quick_tests(conn, t["battery_id"])["cycles"]
                       if c["test_uid"] == t["test_uid"]), None)
    dur = t.get("duration_s") or QUICK_DURATION_S
    elapsed = None
    if t["started_ms"]:
        end = t["ended_ms"] or _now_ms()
        elapsed = min((end - t["started_ms"]) / 1000, dur)
    return {**q, "number": number, "target": QUICK_TARGET, "duration_s": dur,
            "elapsed_s": elapsed, "remaining_s": None if elapsed is None else max(dur - elapsed, 0.0),
            "auto_ended": any(e.get("note") == AUTO_END_NOTE for e in t["history"]),
            "disconnect_confirmed_at": t.get("disconnect_confirmed_at"),
            "needs_disconnect_confirmation": t["state"] in FINAL and t["started_ms"] is not None
            and not t.get("disconnect_confirmed_at")}


CSV_COLS = ["test_uid", "battery_id", "device_id", "state", "quick_test_cycle", "quick_test_status", "classification", "valid_for_full_capacity", "created_at",
            "started_at", "ended_at", "elapsed_s", "initial_voltage_v", "final_voltage_v", "min_loaded_voltage_v",
            "mean_current_a", "peak_current_a", "mean_power_w", "initial_temperature_c", "max_temperature_c",
            "delivered_mah", "delivered_wh", "n_gaps", "readings_with_missing", "paused", "battery_stayed_selected",
            "cutoff_v", "load_resistance_ohm", "supervisor_name", "abort_reason", "failed_checks"]


def tests_csv(views: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_COLS)
    w.writeheader()
    for v in views:
        r = v["results"] if v["results"].get("available") else {}
        c = v["checklist"] or {}
        w.writerow({
            "test_uid": v["test_uid"], "battery_id": v["battery_id"], "device_id": v["device_id"], "state": v["state"],
            "quick_test_cycle": (v["quick_test"] or {}).get("number"),
            "quick_test_status": (v["quick_test"] or {}).get("status"),
            "classification": v["validity"]["classification"],
            "valid_for_full_capacity": v["validity"]["valid_for_full_capacity"], "created_at": v["created_at"],
            "started_at": v["started_at"], "ended_at": v["ended_at"],
            **{k: r.get(k) for k in ("elapsed_s", "initial_voltage_v", "final_voltage_v", "min_loaded_voltage_v",
                                     "mean_current_a", "peak_current_a", "mean_power_w", "initial_temperature_c",
                                     "max_temperature_c", "delivered_mah", "delivered_wh", "paused",
                                     "battery_stayed_selected")},
            "n_gaps": len(r.get("gaps", [])) if r else None,
            "readings_with_missing": (r.get("missing_measurements") or {}).get("readings_with_any_missing") if r else None,
            "cutoff_v": c.get("cutoff_v"), "load_resistance_ohm": c.get("load_resistance_ohm"),
            "supervisor_name": c.get("supervisor_name"), "abort_reason": v["abort_reason"],
            "failed_checks": "; ".join(f"{x['check']}: {x['detail']}" for x in v["validity"]["checks"] if not x["ok"]),
        })
    return buf.getvalue()
