"""Telemetry ingest and read-side queries."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import HTTPException

from .alerts import evaluate_reading, load_thresholds, now_iso
from .config import Settings
from .cycles import LIVE_SOURCE, aggregate_live_cycles
from .db import Database, row_to_dict
from .model_service import ModelService
from .assignments import battery_assignment, ensure_unassigned_bucket, resolve_battery
from .sessions import SESSION_COLS, battery_stats, session_out, update_session
from .presence import VALID_STATES, battery_detected_for, classify, load_presence, save_presence
from .schemas import ReadingIn

DS18B20_DISCONNECTED_C = -127.0
DS18B20_POWER_ON_C = 85.0


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def ensure_battery(conn: sqlite3.Connection, battery_id: str, source: str) -> dict:
    row = conn.execute("SELECT * FROM batteries WHERE battery_id=?", (battery_id,)).fetchone()
    if row is None:
        conn.execute("INSERT INTO batteries(battery_id, source, created_at) VALUES (?,?,?)",
                     (battery_id, source, now_iso()))
        row = conn.execute("SELECT * FROM batteries WHERE battery_id=?", (battery_id,)).fetchone()
    elif row["source"] != source:
        raise HTTPException(409, f"battery '{battery_id}' is registered with source '{row['source']}', "
                                 f"refusing to mix in '{source}' data. Use a different battery_id.")
    return dict(row)


def normalise_reading(r: ReadingIn, s: Settings, now: datetime) -> dict[str, Any]:
    flags: list[str] = []
    st = r.sensor_status

    if r.timestamp is not None:
        ts = r.timestamp.astimezone(timezone.utc)
        if (ts - now).total_seconds() > s.max_future_skew_s:
            raise HTTPException(422, f"timestamp {_iso(ts)} is more than {s.max_future_skew_s:g} s in the future "
                                     f"(server time {_iso(now)}). Check NTP sync on the device.")
        if now - ts > timedelta(days=s.max_backfill_days):
            raise HTTPException(422, f"timestamp {_iso(ts)} is older than {s.max_backfill_days:g} days")
        # A USB bridge stamps readings with the PC clock, not the ESP32's NTP clock.
        ts_source = "bridge" if r.transport == "usb_serial" else "device"
    else:
        ts, ts_source = now, "server"
        flags.append("server_timestamp")

    voltage, current, temp = r.voltage_v, r.current_a, r.temperature_c
    ina_status = st.ina219 if st else None
    ds_status = st.ds18b20 if st else None

    if ina_status in ("error", "not_found"):
        voltage = current = None
        flags.append("ina219_fault")

    if temp is not None and temp <= DS18B20_DISCONNECTED_C + 0.5:
        temp, ds_status = None, "disconnected"
        flags.append("temperature_sensor_fault")
    elif temp is not None and temp == DS18B20_POWER_ON_C:
        temp = None
        flags.append("ds18b20_power_on_value")
    elif temp is not None and temp < -55:
        raise HTTPException(422, f"temperature_c {temp} is outside the DS18B20 range (-55..125 degC)")
    if ds_status in ("disconnected", "not_found", "error"):
        temp = None
        if "temperature_sensor_fault" not in flags:
            flags.append("temperature_sensor_fault")

    # Cell presence is NOT decided here: it needs the previous reading and the
    # battery's presence record (see presence.classify, called in ingest()).
    # sensor_status.battery_connected from firmware is deliberately ignored.

    power, power_source = r.power_w, None
    if power is not None:
        power_source = "device"
        if voltage is not None and current is not None:
            expected = voltage * abs(current)
            if abs(power - expected) > max(0.05, 0.15 * expected):
                flags.append("power_inconsistent")
    elif voltage is not None and current is not None:
        power, power_source = voltage * abs(current), "computed"

    return {
        "battery_id": r.battery_id,
        "device_id": r.device_id or r.battery_id,
        "source": r.source,
        "ts": _iso(ts),
        "ts_ms": _ms(ts),
        "timestamp_source": ts_source,
        "received_at": _iso(now),
        "voltage_v": voltage,
        "current_a": current,
        "temperature_c": temp,
        "power_w": power,
        "power_source": power_source,
        "seq": r.seq,
        "boot_id": r.boot_id,
        "ina219_status": ina_status,
        "ds18b20_status": ds_status,
        "battery_detected": None,
        "cell_state": None,
        "flags": flags,
        "transport": r.transport,
    }


def ingest(db: Database, s: Settings, models: ModelService, r: ReadingIn,
           now: Optional[datetime] = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    rec = normalise_reading(r, s, now)
    with db.write() as conn:
        # Which battery this reading belongs to is decided by the device assignment
        # covering its timestamp, never by guessing (assignments.py).
        battery_id, routed_by, assignment = resolve_battery(conn, rec["device_id"], rec["battery_id"], rec["ts_ms"])
        rec["reported_battery_id"], rec["battery_id"], rec["routed_by"] = rec["battery_id"], battery_id, routed_by
        if routed_by == "unassigned":
            ensure_unassigned_bucket(conn, battery_id, rec["device_id"], rec["received_at"])
        ensure_battery(conn, rec["battery_id"], rec["source"])
        # Only readings from the current connection period count as "previous": a
        # reading from before a battery swap must never be paired with this one.
        since = assignment["start_ms"] if assignment else -1
        prev = conn.execute("SELECT ts_ms, current_a, voltage_v, power_w, temperature_c FROM readings "
                            "WHERE battery_id=? AND ts_ms>=? AND ts_ms<? ORDER BY ts_ms DESC LIMIT 1",
                            (rec["battery_id"], since, rec["ts_ms"])).fetchone()
        presence = load_presence(conn, rec["battery_id"])
        state, new_presence, events = classify(rec, dict(prev) if prev else None, presence, s)
        rec["cell_state"] = state
        rec["battery_detected"] = battery_detected_for(state)
        if state == "no_cell":
            rec["flags"].append("battery_not_detected")
        elif state == "unverified":
            rec["flags"].append("cell_unverified")
        cols = list(rec)
        values = [json.dumps(rec[c]) if c == "flags" else rec[c] for c in cols]
        cur = conn.execute(
            f"INSERT INTO readings({','.join(cols)}) VALUES ({','.join('?' * len(cols))}) ON CONFLICT DO NOTHING",
            values)
        if cur.rowcount == 0:
            dup = conn.execute(
                "SELECT id, ts FROM readings WHERE (device_id=? AND boot_id IS ? AND seq IS ? AND seq IS NOT NULL) "
                "OR (seq IS NULL AND battery_id=? AND device_id=? AND source=? AND ts_ms=?) LIMIT 1",
                (rec["device_id"], rec["boot_id"], rec["seq"], rec["battery_id"], rec["device_id"], rec["source"],
                 rec["ts_ms"])).fetchone()
            return {"status": "duplicate", "reading_id": dup["id"] if dup else None, "ts": rec["ts"],
                    "timestamp_source": rec["timestamp_source"], "flags": rec["flags"], "cycles_closed": []}
        reading_id = cur.lastrowid
        save_presence(conn, rec["battery_id"], new_presence)

        conn.execute(
            "INSERT INTO devices(device_id, battery_id, firmware_version, boot_id, last_rssi_dbm, last_seen_at, "
            "last_seen_ms, last_sensor_status) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(device_id) DO UPDATE SET "
            "battery_id=excluded.battery_id, firmware_version=COALESCE(excluded.firmware_version, firmware_version), "
            "boot_id=COALESCE(excluded.boot_id, boot_id), last_rssi_dbm=COALESCE(excluded.last_rssi_dbm, last_rssi_dbm), "
            "last_seen_at=excluded.last_seen_at, last_seen_ms=excluded.last_seen_ms, "
            "last_sensor_status=excluded.last_sensor_status",
            (rec["device_id"], rec["battery_id"], r.firmware_version, r.boot_id, r.rssi_dbm, rec["received_at"],
             _ms(now), r.sensor_status.model_dump_json() if r.sensor_status else None))

        evaluate_reading(conn, rec, events)
        session_event = update_session(conn, rec["battery_id"], rec, dict(prev) if prev else None, s)

        # Only scan for closed discharge segments on a transition (load stopped,
        # reading gap), not on every sample of a long discharge.
        discharging = rec["current_a"] is not None and rec["current_a"] >= s.discharge_on_a and rec["battery_detected"] != 0
        gap = prev is not None and rec["ts_ms"] - prev["ts_ms"] > s.max_gap_s * 1000
        closed = []
        if not discharging or gap:
            closed = aggregate_live_cycles(conn, rec["battery_id"], s, models)

    return {"status": "stored", "reading_id": reading_id, "ts": rec["ts"],
            "timestamp_source": rec["timestamp_source"], "cell_state": rec["cell_state"],
            "battery_id": rec["battery_id"], "routed_by": rec["routed_by"],
            "flags": rec["flags"], "cycles_closed": closed, "session_event": session_event}


# ------------------------------------------------------------------ queries
READING_FIELDS = ("id, battery_id, reported_battery_id, routed_by, device_id, source, transport, ts, timestamp_source, received_at, voltage_v, current_a, "
                  "temperature_c, power_w, power_source, seq, ina219_status, ds18b20_status, battery_detected, "
                  "cell_state, flags")


def reading_out(row: sqlite3.Row) -> dict:
    d = row_to_dict(row, ("flags",))
    d["battery_detected"] = None if d["battery_detected"] is None else bool(d["battery_detected"])
    return d


def connection_state(last_received_at: Optional[str], offline_after_s: float, now: datetime) -> dict:
    if last_received_at is None:
        return {"state": "never_seen", "age_s": None}
    age = (now - parse_iso(last_received_at)).total_seconds()
    state = "online" if age <= offline_after_s else ("stale" if age <= 10 * offline_after_s else "offline")
    return {"state": state, "age_s": round(age, 1)}


def battery_summary(conn: sqlite3.Connection, b: dict, now: datetime, s: Optional[Settings] = None) -> dict:
    s = s or Settings()
    t = load_thresholds(conn)
    last = conn.execute(f"SELECT {READING_FIELDS} FROM readings WHERE battery_id=? ORDER BY ts_ms DESC LIMIT 1",
                        (b["battery_id"],)).fetchone()
    last_rx = conn.execute("SELECT MAX(received_at) AS r FROM readings WHERE battery_id=?",
                           (b["battery_id"],)).fetchone()["r"]
    pred = conn.execute("SELECT * FROM predictions WHERE battery_id=? ORDER BY id DESC LIMIT 1",
                        (b["battery_id"],)).fetchone()
    valid_pred = conn.execute("SELECT * FROM predictions WHERE battery_id=? AND status!='unavailable' "
                              "ORDER BY id DESC LIMIT 1", (b["battery_id"],)).fetchone()
    alerts = conn.execute("SELECT severity, COUNT(*) AS n FROM alerts WHERE battery_id=? AND status='active' "
                          "GROUP BY severity", (b["battery_id"],)).fetchall()
    n_read = conn.execute("SELECT COUNT(*) AS n FROM readings WHERE battery_id=?", (b["battery_id"],)).fetchone()["n"]
    n_cyc = conn.execute("SELECT COUNT(*) AS n, SUM(complete) AS c FROM cycles WHERE battery_id=?",
                         (b["battery_id"],)).fetchone()
    pj = ("reasons", "warnings", "out_of_range", "input_features")
    sess = conn.execute(f"SELECT {SESSION_COLS} FROM discharge_sessions WHERE battery_id=? ORDER BY id DESC LIMIT 1",
                        (b["battery_id"],)).fetchone()
    session = session_out(sess, s, b.get("cutoff_voltage_v"), b.get("rated_capacity_ah")) if sess else None
    return {
        **b,
        "connection": connection_state(last_rx, t.offline_after_s, now) if b["source"] != "nasa_replay"
        else {"state": "not_applicable", "age_s": None},
        "latest_reading": reading_out(last) if last else None,
        "latest_prediction": row_to_dict(pred, pj),
        "latest_valid_prediction": row_to_dict(valid_pred, pj),
        "active_alerts": {r["severity"]: r["n"] for r in alerts},
        "n_readings": n_read,
        "n_cycles": n_cyc["n"],
        "n_complete_cycles": n_cyc["c"] or 0,
        "active_session": session if session and session["status"] == "active" else None,
        "last_session": session,
        "assignment": battery_assignment(conn, b["battery_id"]),
        "stats": battery_stats(conn, b, s),
    }


def history(conn: sqlite3.Connection, battery_id: str, start: Optional[datetime], end: Optional[datetime],
            limit: int, max_points: Optional[int]) -> dict:
    where, args = ["battery_id=?"], [battery_id]
    if start:
        where.append("ts_ms>=?"); args.append(_ms(start))
    if end:
        where.append("ts_ms<=?"); args.append(_ms(end))
    w = " AND ".join(where)
    stats = conn.execute(f"SELECT COUNT(*) AS n, MIN(ts_ms) AS a, MAX(ts_ms) AS b FROM readings WHERE {w}",
                         args).fetchone()
    total = stats["n"]
    if max_points and total > max_points and stats["b"] > stats["a"]:
        bucket = max(1, -(-(stats["b"] - stats["a"] + 1) // max_points))
        rows = conn.execute(
            f"SELECT MIN(ts_ms) AS ts_ms, COUNT(*) AS n, "
            f"AVG(CASE WHEN cell_state IN ('verified_current','confirmed') THEN voltage_v END) AS voltage_v, "
            f"AVG(current_a) AS current_a, AVG(temperature_c) AS temperature_c, AVG(power_w) AS power_w, "
            f"MIN(CASE WHEN cell_state IN ('verified_current','confirmed') THEN voltage_v END) AS voltage_min_v, "
            f"MAX(temperature_c) AS temperature_max_c "
            f"FROM readings WHERE {w} GROUP BY (ts_ms - ?) / ? ORDER BY ts_ms",
            [*args, stats["a"], bucket]).fetchall()
        points = [{**dict(r), "ts": _iso(datetime.fromtimestamp(r["ts_ms"] / 1000, tz=timezone.utc))} for r in rows]
        return {"battery_id": battery_id, "total": total, "downsampled": True, "bucket_s": bucket / 1000,
                "points": points}
    rows = conn.execute(f"SELECT {READING_FIELDS}, ts_ms FROM readings WHERE {w} ORDER BY ts_ms DESC LIMIT ?",
                        [*args, limit]).fetchall()
    points = []
    for r in reversed(rows):
        d = reading_out(r)
        # voltage_v carries only verified cell voltage; the raw INA219 value is kept alongside.
        d["voltage_raw_v"] = d["voltage_v"]
        if d["cell_state"] not in VALID_STATES:
            d["voltage_v"] = None
        points.append(d)
    return {"battery_id": battery_id, "total": total, "downsampled": False, "truncated": total > len(points),
            "points": points}
