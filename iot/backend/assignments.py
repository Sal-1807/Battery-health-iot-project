"""Which physical battery is connected to which device, over time.

One ESP32 + INA219 is moved between several physical cells (BAT-A-3000,
BAT-B-3000, BAT-C-2500). The firmware's BATTERY_ID is a fixed string in
config.h, so it cannot tell which cell is on the clips. The operator records
that in the dashboard as a *device assignment*: "from time T, device D measures
battery X".

Routing rules (never attribute a measurement to a battery automatically):

* A reading is stored under the battery whose assignment for its device covers
  the reading's OWN timestamp (so readings buffered before a switch still land
  on the battery that was connected when they were measured).
* A managed profile (batteries.managed = 1) only ever receives readings through
  an assignment. A reading that is not covered by any assignment is stored
  under ``UNASSIGNED-<device_id>`` - including when the firmware claims a
  managed profile's ID, and for any device that has ever been assigned.
* Devices that have never been assigned keep the original behaviour (the
  firmware's battery_id), so existing data, the simulator and NASA replay are
  unaffected.

An assignment is a *profile selection*. It is not evidence that a cell is
physically connected: presence still comes from load current or an operator
presence confirmation (presence.py), and switching clears presence evidence.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import HTTPException

from .config import Settings
from .presence import Presence, save_presence
from .sessions import active_session

UNASSIGNED_PREFIX = "UNASSIGNED-"


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def assignment_at(conn: sqlite3.Connection, device_id: str, ts_ms: int) -> Optional[dict]:
    row = conn.execute(
        "SELECT * FROM device_assignments WHERE device_id=? AND start_ms<=? AND (end_ms IS NULL OR ?<end_ms) "
        "ORDER BY start_ms DESC LIMIT 1", (device_id, ts_ms, ts_ms)).fetchone()
    return dict(row) if row else None


def active_assignment(conn: sqlite3.Connection, device_id: str) -> Optional[dict]:
    row = conn.execute("SELECT * FROM device_assignments WHERE device_id=? AND end_ms IS NULL "
                       "ORDER BY start_ms DESC LIMIT 1", (device_id,)).fetchone()
    return dict(row) if row else None


def battery_assignment(conn: sqlite3.Connection, battery_id: str) -> Optional[dict]:
    row = conn.execute("SELECT * FROM device_assignments WHERE battery_id=? AND end_ms IS NULL "
                       "ORDER BY start_ms DESC LIMIT 1", (battery_id,)).fetchone()
    return dict(row) if row else None


def resolve_battery(conn: sqlite3.Connection, device_id: str, reported_battery_id: str,
                    ts_ms: int) -> tuple[str, str, Optional[dict]]:
    """Return (battery_id, routed_by, assignment) for a reading."""
    a = assignment_at(conn, device_id, ts_ms)
    if a is not None:
        return a["battery_id"], "assignment", a
    ever = conn.execute("SELECT 1 FROM device_assignments WHERE device_id=? LIMIT 1", (device_id,)).fetchone()
    claimed = conn.execute("SELECT managed FROM batteries WHERE battery_id=?", (reported_battery_id,)).fetchone()
    if ever is not None or (claimed is not None and claimed["managed"]):
        return UNASSIGNED_PREFIX + device_id, "unassigned", None
    return reported_battery_id, "firmware", None


def ensure_unassigned_bucket(conn: sqlite3.Connection, battery_id: str, device_id: str, now: str) -> None:
    conn.execute("INSERT OR IGNORE INTO batteries(battery_id, label, source, notes, created_at, managed) "
                 "VALUES (?,?,?,?,?,0)",
                 (battery_id, f"Unassigned readings ({device_id})", "hardware",
                  "Readings from this device while no battery profile was selected as connected. "
                  "Never attributed to a profile automatically.", now))


def switch_assignment(conn: sqlite3.Connection, device_id: str, battery_id: Optional[str], note: Optional[str],
                      s: Settings, now_ms: Optional[int] = None) -> dict[str, Any]:
    """Select the physically connected battery for a device (None = nothing connected).

    Ends the previous assignment, ends the previous battery's discharge session,
    force-closes its open cycle segment, and clears presence evidence on both
    batteries (a cell swap invalidates it; selecting a profile does not create it).
    """
    from .cycles import aggregate_live_cycles  # local: cycles imports model code

    now_ms = now_ms or int(datetime.now(timezone.utc).timestamp() * 1000)
    now = _iso(now_ms)
    if battery_id is not None:
        b = conn.execute("SELECT * FROM batteries WHERE battery_id=?", (battery_id,)).fetchone()
        if b is None:
            raise HTTPException(404, f"battery '{battery_id}' not found")
        if not b["managed"]:
            raise HTTPException(400, f"'{battery_id}' is not a physical battery profile")
        other = battery_assignment(conn, battery_id)
        if other and other["device_id"] != device_id:
            raise HTTPException(409, f"'{battery_id}' is currently selected on device '{other['device_id']}'; "
                                     f"unassign it there first (one physical cell cannot be on two devices)")

    prev = active_assignment(conn, device_id)
    if prev and prev["battery_id"] == battery_id:
        return {"device_id": device_id, "changed": False, "assignment": prev, "ended": None}

    ended: dict[str, Any] = {}
    if prev:
        conn.execute("UPDATE device_assignments SET end_ms=?, end_ts=? WHERE id=?", (now_ms, now, prev["id"]))
        old = prev["battery_id"]
        sess = active_session(conn, old)
        if sess:
            end_ms = sess["last_on_ms"] or sess["start_ms"]
            conn.execute("UPDATE discharge_sessions SET status='ended', end_ms=?, end_ts=?, "
                         "end_reason='battery_switched' WHERE id=?", (end_ms, _iso(end_ms), sess["id"]))
            ended["session_id"] = sess["id"]
        closed = aggregate_live_cycles(conn, old, s, _NoModels(), now_ms=now_ms, force_close=True)
        ended["cycles_closed"] = len(closed)
        save_presence(conn, old, Presence(note="cleared: battery switched"))
        ended["battery_id"] = old

    new = None
    if battery_id is not None:
        # a stale session left active on the new battery (e.g. by a crash) must not be continued
        stale = active_session(conn, battery_id)
        if stale:
            end_ms = stale["last_on_ms"] or stale["start_ms"]
            conn.execute("UPDATE discharge_sessions SET status='ended', end_ms=?, end_ts=?, "
                         "end_reason='battery_switched' WHERE id=?", (end_ms, _iso(end_ms), stale["id"]))
        save_presence(conn, battery_id, Presence(note="profile selected; cell presence not yet verified"))
        cur = conn.execute("INSERT INTO device_assignments(device_id, battery_id, start_ms, start_ts, note, created_at) "
                           "VALUES (?,?,?,?,?,?)", (device_id, battery_id, now_ms, now, note, now))
        new = dict(conn.execute("SELECT * FROM device_assignments WHERE id=?", (cur.lastrowid,)).fetchone())
    return {"device_id": device_id, "changed": True, "assignment": new, "ended": ended or None}


class _NoModels:
    """Cycle segments closed by a switch are incomplete, so no model is ever run on them."""
    loaded = False
    error = "not run on segments closed by a battery switch"
    version = None

    def predict_cycle(self, cycle, *, data_source, rated_capacity_ah):
        from .model_service import PredictionResult
        return PredictionResult(status="unavailable", reasons=["segment closed by a battery switch"])
