"""Discharge-segment detection and cycle persistence.

Live telemetry arrives as instantaneous samples. The NASA models need one feature
vector per *complete discharge record*, so samples are grouped into segments:

* a segment starts at the first sample with ``current_a >= discharge_on_a`` and a
  detected cell;
* it continues while samples keep discharging and no gap exceeds ``max_gap_s``;
* when the load stops, rest samples within ``tail_s`` are kept (NASA records
  include a post-load relaxation tail, which affects Voltage_max/Current_std);
  if discharge resumes inside the tail window the segment simply continues;
* a segment is only *closed* once the tail window has elapsed, a gap is seen, or
  the device has been silent for ``max_gap_s``. Open segments are not stored.

Closed segments are stored even when incomplete (for transparency), but only
complete ones get a ``cycle_number`` and are eligible for a prediction.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Optional

from .alerts import evaluate_prediction, now_iso
from .config import Settings
from .db import row_to_dict
from .features import compute_segment_features
from .model_service import ModelService

LIVE_SOURCE = "live_aggregated"


@dataclass
class _Segment:
    rows: list[dict] = field(default_factory=list)
    load_stop_ms: Optional[int] = None  # set while in the relaxation tail
    end_reason: str = ""


def _is_discharge(r: dict, s: Settings) -> bool:
    return (r["battery_detected"] != 0 and r["voltage_v"] is not None
            and r["current_a"] is not None and r["current_a"] >= s.discharge_on_a)


def detect_segments(rows: list[dict], s: Settings, now_ms: int) -> list[_Segment]:
    """Return CLOSED segments found in ``rows`` (sorted by ts_ms)."""
    closed: list[_Segment] = []
    seg: Optional[_Segment] = None
    max_gap_ms, tail_ms = s.max_gap_s * 1000, s.tail_s * 1000

    def close(reason: str):
        nonlocal seg
        if seg is not None:
            # trim trailing rest samples beyond the tail window
            if seg.load_stop_ms is not None:
                seg.rows = [r for r in seg.rows if r["ts_ms"] <= seg.load_stop_ms + tail_ms]
            seg.end_reason = reason
            closed.append(seg)
        seg = None

    for r in rows:
        if seg is not None:
            gap = r["ts_ms"] - seg.rows[-1]["ts_ms"]
            if gap > max_gap_ms:
                close("gap" if seg.load_stop_ms is None else "load_stopped")
            elif seg.load_stop_ms is not None and r["ts_ms"] - seg.load_stop_ms > tail_ms:
                close("load_stopped")

        discharging = _is_discharge(r, s)
        if seg is None:
            if discharging:
                seg = _Segment(rows=[r])
            continue
        seg.rows.append(r)
        if discharging:
            seg.load_stop_ms = None  # (re)entered discharge; a short interruption is absorbed
        elif seg.load_stop_ms is None:
            seg.load_stop_ms = r["ts_ms"]

    if seg is not None:
        last = seg.rows[-1]["ts_ms"]
        if seg.load_stop_ms is not None and (now_ms - seg.load_stop_ms > tail_ms or now_ms - last > max_gap_ms):
            close("load_stopped")       # already resting; the tail is just shorter
        elif now_ms - last > max_gap_ms:
            close("device_silent")      # discharge cut off mid-way (power/Wi-Fi loss)
    return closed


def _assess(seg: _Segment, feats, s: Settings, cutoff_v: float) -> list[str]:
    notes = []
    n = len(seg.rows)
    duration = (seg.rows[-1]["ts_ms"] - seg.rows[0]["ts_ms"]) / 1000
    if n < s.min_segment_readings:
        notes.append(f"only {n} readings (< {s.min_segment_readings})")
    if duration < s.min_segment_duration_s:
        notes.append(f"duration {duration:.0f} s (< {s.min_segment_duration_s:g} s)")
    if seg.end_reason == "gap" or seg.end_reason == "device_silent":
        notes.append(f"segment ended by {seg.end_reason.replace('_', ' ')}, not by the load stopping")
    if feats.voltage_min is None or feats.voltage_min > cutoff_v + 0.02:
        vm = "n/a" if feats.voltage_min is None else f"{feats.voltage_min:.3f} V"
        notes.append(f"did not reach end-of-discharge voltage (min {vm}, cutoff {cutoff_v:.2f} V)")
    if feats.voltage_coverage < 0.95 or feats.current_coverage < 0.95:
        notes.append("voltage/current samples missing in > 5 % of readings")
    if feats.temperature_coverage < 0.90:
        notes.append(f"temperature present in only {feats.temperature_coverage:.0%} of readings")
    if feats.temperature_rise is None:
        notes.append("no temperature at segment start; Temperature_rise undefined")
    return notes


def store_prediction(conn: sqlite3.Connection, models: ModelService, battery: dict, cycle: dict,
                     data_source: str) -> dict:
    res = models.predict_cycle(cycle, data_source=data_source, rated_capacity_ah=battery.get("rated_capacity_ah"))
    cur = conn.execute(
        "INSERT INTO predictions(battery_id, cycle_id, cycle_number, created_at, status, model_version, data_source, "
        "soh_pct, eol_probability, eol_predicted, rul_cycles, reasons, warnings, out_of_range, input_features) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (battery["battery_id"], cycle["id"], cycle.get("cycle_number"), now_iso(), res.status, res.model_version,
         data_source, res.soh_pct, res.eol_probability, res.eol_predicted, res.rul_cycles,
         json.dumps(res.reasons), json.dumps(res.warnings), json.dumps(res.out_of_range),
         json.dumps(res.input_features) if res.input_features else None))
    evaluate_prediction(conn, battery["battery_id"], res.status, res.soh_pct)
    return {"id": cur.lastrowid, "status": res.status, "soh_pct": res.soh_pct, "rul_cycles": res.rul_cycles,
            "reasons": res.reasons}


def aggregate_live_cycles(conn: sqlite3.Connection, battery_id: str, s: Settings, models: ModelService,
                          now_ms: Optional[int] = None) -> list[dict]:
    """Detect newly closed segments for a battery, store them, predict on complete ones.

    ``now_ms=None`` means "data time": the newest reading's timestamp. Ingest uses
    that, so buffered/back-filled uploads segment exactly like live ones. Pass
    wall-clock time only to close segments of a device that has gone silent.
    """
    battery = row_to_dict(conn.execute("SELECT * FROM batteries WHERE battery_id=?", (battery_id,)).fetchone())
    if battery is None:
        return []
    last = conn.execute("SELECT MAX(end_ms) AS e, MAX(segment_index) AS i FROM cycles "
                        "WHERE battery_id=? AND source=?", (battery_id, LIVE_SOURCE)).fetchone()
    after_ms = last["e"] if last["e"] is not None else -1
    seg_index = last["i"] or 0

    rows = [dict(r) for r in conn.execute(
        "SELECT id, ts_ms, voltage_v, current_a, temperature_c, battery_detected FROM readings "
        "WHERE battery_id=? AND ts_ms>? ORDER BY ts_ms", (battery_id, after_ms)).fetchall()]
    if not rows:
        return []
    if now_ms is None:
        now_ms = rows[-1]["ts_ms"]

    cutoff = battery.get("cutoff_voltage_v") or s.default_cutoff_v
    created = []
    for seg in detect_segments(rows, s, now_ms):
        feats = compute_segment_features([r["voltage_v"] for r in seg.rows],
                                         [r["current_a"] for r in seg.rows],
                                         [r["temperature_c"] for r in seg.rows])
        notes = _assess(seg, feats, s, cutoff)
        complete = not notes
        seg_index += 1
        cycle_number = None
        if complete and battery.get("cycle_count_offset") is not None:
            n_complete = conn.execute("SELECT COUNT(*) AS n FROM cycles WHERE battery_id=? AND source=? AND complete=1",
                                      (battery_id, LIVE_SOURCE)).fetchone()["n"]
            cycle_number = battery["cycle_count_offset"] + n_complete + 1
        start, end = seg.rows[0]["ts_ms"], seg.rows[-1]["ts_ms"]
        cur = conn.execute(
            "INSERT INTO cycles(battery_id, source, segment_index, cycle_number, start_ts, end_ts, start_ms, end_ms, "
            "n_readings, duration_s, voltage_mean, voltage_min, voltage_max, voltage_range, current_mean, current_std, "
            "temperature_mean, temperature_max, temperature_rise, complete, completeness_notes, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (battery_id, LIVE_SOURCE, seg_index, cycle_number, _iso(start), _iso(end), start, end, len(seg.rows),
             (end - start) / 1000, *feats.as_db().values(), int(complete), json.dumps(notes), now_iso()))
        cycle = row_to_dict(conn.execute("SELECT * FROM cycles WHERE id=?", (cur.lastrowid,)).fetchone(),
                            ("completeness_notes",))
        pred = store_prediction(conn, models, battery, cycle, data_source=battery["source"])
        created.append({"cycle_id": cycle["id"], "segment_index": seg_index, "complete": complete,
                        "notes": notes, "prediction": pred})
    return created


def _iso(ms: int) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def insert_cycle_features(conn: sqlite3.Connection, battery: dict, body: dict[str, Any],
                          models: ModelService) -> tuple[dict, bool, Optional[dict]]:
    """Store a directly-posted cycle feature vector (NASA replay / manual). Idempotent on cycle_number."""
    existing = conn.execute("SELECT * FROM cycles WHERE battery_id=? AND source=? AND segment_index=?",
                            (battery["battery_id"], body["source"], body["cycle_number"])).fetchone()
    if existing:
        return row_to_dict(existing, ("completeness_notes",)), True, None
    ts = now_iso()
    cur = conn.execute(
        "INSERT INTO cycles(battery_id, source, segment_index, cycle_number, start_ts, end_ts, n_readings, "
        "voltage_mean, voltage_min, voltage_max, voltage_range, current_mean, current_std, temperature_mean, "
        "temperature_max, temperature_rise, complete, completeness_notes, ref_soh, ref_rul, created_at) "
        "VALUES (?,?,?,?,NULL,NULL,NULL,?,?,?,?,?,?,?,?,?,1,'[]',?,?,?)",
        (battery["battery_id"], body["source"], body["cycle_number"], body["cycle_number"],
         body["voltage_mean"], body["voltage_min"], body["voltage_max"], body["voltage_range"],
         body["current_mean"], body["current_std"], body["temperature_mean"], body["temperature_max"],
         body["temperature_rise"], body.get("ref_soh"), body.get("ref_rul"), ts))
    cycle = row_to_dict(conn.execute("SELECT * FROM cycles WHERE id=?", (cur.lastrowid,)).fetchone(),
                        ("completeness_notes",))
    pred = store_prediction(conn, models, battery, cycle, data_source=body["source"])
    return cycle, False, pred
