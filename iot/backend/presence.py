"""Cell-presence / measurement-validity state.

What the INA219 can and cannot tell us
--------------------------------------
With no cell attached, the INA219's VIN+/VIN- pins float and the bus voltage
reads whatever the input settles to (0.72-1.14 V was observed on the team's
board, and nothing guarantees it stays below any particular value). A
connected cell at rest draws ~0 A, exactly like an open input. So:

* A voltage alone can never PROVE a cell is connected.
* A voltage outside the Li-ion range at ~0 A only proves there is no usable
  cell measurement: a floating input and a deeply discharged cell look the same.
* Sustained current through the shunt (|I| >= current_evidence_a on two
  consecutive readings) proves a closed circuit with a source: an open input
  cannot drive current. It does not prove the source is the cell (a miswired
  supply would look the same).
* An operator can confirm presence explicitly (after checking with a meter).

Presence, once established, persists while readings stay continuous and in
the Li-ion range; it is cleared by an out-of-range reading at rest (cell
removed, wire off, or cell deeply discharged) or a gap > presence_gap_s.

Per-reading ``cell_state``
--------------------------
``unknown``           no voltage available (INA219 fault)
``no_cell``           voltage outside the Li-ion range and no current evidence
``unverified``        voltage in range, but no current evidence or confirmation
``verified_current``  current evidence now, or since an unbroken run of readings
``confirmed``         operator confirmation, unbroken since

Only ``verified_current`` and ``confirmed`` readings are treated as cell
voltage (charts, voltage alerts).
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from .config import Settings
from .db import Database

VALID_STATES = ("verified_current", "confirmed")
VOLTAGE_ALERT_TYPES = ("voltage_low", "voltage_high")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def battery_detected_for(state: str) -> Optional[int]:
    """Legacy readings.battery_detected column: 1 verified, 0 no cell, NULL undecided."""
    return 1 if state in VALID_STATES else 0 if state == "no_cell" else None


@dataclass
class Presence:
    evidence: Optional[str] = None      # "current" | "operator" | None
    since: Optional[str] = None
    last_ms: Optional[int] = None
    note: Optional[str] = None

    @property
    def active(self) -> bool:
        return self.evidence is not None


def load_presence(conn: sqlite3.Connection, battery_id: str) -> Presence:
    row = conn.execute("SELECT presence_evidence, presence_since, presence_last_ms, presence_note "
                       "FROM batteries WHERE battery_id=?", (battery_id,)).fetchone()
    if row is None:
        return Presence()
    return Presence(row["presence_evidence"], row["presence_since"], row["presence_last_ms"], row["presence_note"])


def save_presence(conn: sqlite3.Connection, battery_id: str, p: Presence) -> None:
    conn.execute("UPDATE batteries SET presence_evidence=?, presence_since=?, presence_last_ms=?, presence_note=? "
                 "WHERE battery_id=?", (p.evidence, p.since, p.last_ms, p.note, battery_id))


def plausible(v: float, s: Settings) -> bool:
    return s.cell_plausible_min_v <= v <= s.cell_plausible_max_v


def classify(rec: dict[str, Any], prev: Optional[dict[str, Any]], presence: Presence,
             s: Settings) -> tuple[str, Presence, list[str]]:
    """Return (cell_state, updated presence, events) for one reading.

    ``rec``/``prev`` need ts_ms, voltage_v, current_a. Events: "presence_expired",
    "cell_lost", "verified_by_current".
    """
    p = Presence(presence.evidence, presence.since, presence.last_ms, presence.note)
    events: list[str] = []
    v, i, ts = rec.get("voltage_v"), rec.get("current_a"), rec["ts_ms"]

    if v is None:
        return "unknown", p, events

    if p.active and p.last_ms is not None and ts - p.last_ms > s.presence_gap_s * 1000:
        p = Presence()
        events.append("presence_expired")

    def strong(x: Optional[float]) -> bool:
        return x is not None and abs(x) >= s.current_evidence_a

    current_evidence = (strong(i) and prev is not None and strong(prev.get("current_a"))
                        and ts - prev["ts_ms"] <= s.max_gap_s * 1000)

    if current_evidence:
        if not p.active:                      # an operator confirmation, if any, is kept
            p = Presence("current", rec.get("ts"), None, None)
            events.append("verified_by_current")
        p.last_ms = ts
        # Current flowing is direct evidence regardless of the voltage value: an
        # over-discharged cell under load (< cell_plausible_min_v) is still a cell.
        return "verified_current", p, events

    if p.active:
        if plausible(v, s):
            p.last_ms = ts
            return ("confirmed" if p.evidence == "operator" else "verified_current"), p, events
        events.append("cell_lost")
        return "no_cell", Presence(), events

    return ("unverified" if plausible(v, s) else "no_cell"), p, events


# ------------------------------------------------------------- history repair
def reclassify_history(db: Database, s: Settings) -> dict[str, int]:
    """Back-fill cell_state for readings stored before schema v4 and reclassify
    voltage alerts that were raised without any verified cell reading.

    Nothing is deleted: false alerts are resolved (if still active) and marked
    classification='invalid_measurement' with an explanatory note. Idempotent.
    """
    stats = {"readings_classified": 0, "alerts_reclassified": 0, "alerts_confirmed_valid": 0}
    with db.write() as conn:
        batteries = [r["battery_id"] for r in conn.execute(
            "SELECT DISTINCT battery_id FROM readings WHERE cell_state IS NULL").fetchall()]
        for b in batteries:
            rows = conn.execute("SELECT id, ts, ts_ms, voltage_v, current_a, flags FROM readings "
                                "WHERE battery_id=? ORDER BY ts_ms, id", (b,)).fetchall()
            presence, prev = Presence(), None
            for r in rows:
                rec = dict(r)
                state, presence, _ = classify(rec, prev, presence, s)
                flags = [f for f in json.loads(rec["flags"] or "[]") if f not in ("battery_not_detected", "cell_unverified")]
                if state == "no_cell":
                    flags.append("battery_not_detected")
                elif state == "unverified":
                    flags.append("cell_unverified")
                conn.execute("UPDATE readings SET cell_state=?, battery_detected=?, flags=? WHERE id=?",
                             (state, battery_detected_for(state), json.dumps(flags), rec["id"]))
                stats["readings_classified"] += 1
                prev = rec

        alerts = conn.execute(
            f"SELECT id, battery_id, first_seen, last_seen, status FROM alerts WHERE classification IS NULL "
            f"AND type IN ({','.join('?' * len(VOLTAGE_ALERT_TYPES))})", VOLTAGE_ALERT_TYPES).fetchall()
        for a in alerts:
            verified = conn.execute(
                f"SELECT COUNT(*) AS n FROM readings WHERE battery_id=? AND received_at BETWEEN ? AND ? "
                f"AND cell_state IN ({','.join('?' * len(VALID_STATES))})",
                (a["battery_id"], a["first_seen"], a["last_seen"], *VALID_STATES)).fetchone()["n"]
            if verified:
                conn.execute("UPDATE alerts SET classification='valid' WHERE id=?", (a["id"],))
                stats["alerts_confirmed_valid"] += 1
            else:
                note = ("Reclassified: raised on readings with no verified cell (INA219 input floating or no usable "
                        "cell measurement). Not a battery voltage event. Original record kept.")
                conn.execute(
                    "UPDATE alerts SET classification='invalid_measurement', resolution_note=?, "
                    "status='resolved', resolved_at=COALESCE(resolved_at, ?) WHERE id=?",
                    (note, now_iso(), a["id"]))
                stats["alerts_reclassified"] += 1
    return stats
