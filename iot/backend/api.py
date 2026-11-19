"""FastAPI application factory: telemetry ingest, history, cycles, predictions, alerts.

The ASGI entry point is ``iot.backend.main:app``; tests call ``create_app(settings)``
with a temporary database instead.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from . import __version__
from .alerts import load_thresholds, now_iso, refresh_offline, save_thresholds
from .config import DEFAULT_DB_PATH, Settings, get_settings
from .cycles import aggregate_live_cycles, insert_cycle_features, store_prediction
from .db import Database, row_to_dict
from .model_service import ModelService
from . import aging, battery_tests, ml_validation
from .nasa import NasaData
from .sessions import SESSION_COLS, battery_stats, session_out
from .assignments import active_assignment, battery_assignment, switch_assignment
from .presence import Presence, load_presence, plausible, reclassify_history, save_presence
from .schemas import TestAction, TestChecklist, TestCreate
from .schemas import BatteryUpsert, CycleIn, DeviceAssignmentIn, PresenceIn, ReadingBatchIn, ReadingIn, Thresholds
from .telemetry import battery_summary, ensure_battery, history, ingest, parse_iso

log = logging.getLogger("bhm")
PRED_JSON = ("reasons", "warnings", "out_of_range", "input_features")


SIM_PROFILES = (("SIM-BAT-A-3000", "SIMULATED Battery A - 3000 mAh", 3.0),
                ("SIM-BAT-B-3000", "SIMULATED Battery B - 3000 mAh", 3.0),
                ("SIM-BAT-C-2500", "SIMULATED Battery C - 2500 mAh", 2.5))


def profile_source(s: Settings) -> str:
    """Physical-profile views show only real profiles in production and only SIM-* ones in simulation."""
    return "simulation" if s.mode == "simulation" else "hardware"


def bind_db_mode(db: Database, s: Settings) -> None:
    """A database belongs to one mode for life. Opening a simulation database in
    production mode (or the reverse) would let real telemetry into simulated history
    or vice versa, so it is refused."""
    with db.write() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key='db_mode'").fetchone()
        if row is None:
            if conn.execute("SELECT 1 FROM readings WHERE source='hardware' LIMIT 1").fetchone():
                bound = "production"
            elif conn.execute("SELECT 1 FROM batteries WHERE battery_id LIKE 'SIM-BAT-%' LIMIT 1").fetchone():
                bound = "simulation"
            else:
                bound = s.mode
            conn.execute("INSERT INTO settings(key, value) VALUES ('db_mode', ?)", (json.dumps(bound),))
        else:
            bound = json.loads(row["value"])
    if bound != s.mode:
        raise RuntimeError(f"database {s.db_path} is a {bound} database; refusing to open it in {s.mode} mode "
                           f"(set BHM_MODE={bound}, or point BHM_DB_PATH at a {s.mode} database)")


def seed_simulation_profiles(db: Database) -> None:
    with db.write() as conn:
        for bid, label, cap in SIM_PROFILES:
            conn.execute("INSERT OR IGNORE INTO batteries(battery_id, label, chemistry, rated_capacity_ah, "
                         "nominal_voltage_v, source, notes, created_at, managed) VALUES (?,?,?,?,?,?,?,?,1)",
                         (bid, label, "Li-ion 18650 (simulated)", cap, 3.7, "simulation",
                          "Synthetic profile for the simulation workflow. Not a physical battery.", now_iso()))


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    s = settings or get_settings()
    if s.mode not in ("production", "simulation"):
        raise ValueError(f"BHM_MODE must be 'production' or 'simulation', not {s.mode!r}")
    if s.mode == "simulation" and Path(s.db_path).resolve() == DEFAULT_DB_PATH.resolve():
        raise ValueError("BHM_MODE=simulation needs its own database: set BHM_DB_PATH (e.g. iot/data/simulation.db); "
                         "refusing to use the production database")
    db = Database(s.db_path)
    bind_db_mode(db, s)
    if s.mode == "simulation":
        seed_simulation_profiles(db)
    repair = reclassify_history(db, s)   # back-fill cell_state; reclassify false voltage alerts (idempotent)
    if repair["readings_classified"] or repair["alerts_reclassified"]:
        log.info("history repair: %s", repair)
    models = ModelService(s)
    nasa = NasaData(s)
    started = now_iso()

    app = FastAPI(title="Battery Health Monitoring API", version=__version__,
                  description="ESP32 telemetry ingest, cycle aggregation and NASA-trained SoH/RUL inference. "
                              "Units: V, A (positive = discharge), degC, W. Timestamps: ISO-8601 UTC.")
    app.state.db, app.state.models, app.state.settings = db, models, s
    app.add_middleware(CORSMiddleware, allow_origins=list(s.cors_origins), allow_credentials=False,
                       allow_methods=["GET", "POST", "PUT", "DELETE"], allow_headers=["*"])

    @app.exception_handler(sqlite3.Error)
    async def _db_error(_: Request, exc: sqlite3.Error):
        log.exception("database error")
        return JSONResponse(status_code=503, content={"detail": f"database error: {type(exc).__name__}"})

    def _battery(conn, battery_id: str) -> dict:
        row = conn.execute("SELECT * FROM batteries WHERE battery_id=?", (battery_id,)).fetchone()
        if row is None:
            raise HTTPException(404, f"battery '{battery_id}' not found")
        return dict(row)

    # ------------------------------------------------------------ health
    @app.get("/api/health", tags=["system"])
    def health():
        db_ok = db.ping()
        return {"status": "ok" if db_ok and models.loaded else "degraded", "api_version": __version__,
                "mode": s.mode,
                "server_time": now_iso(), "started_at": started, "database": {"ok": db_ok, "path": str(s.db_path)},
                "models": {"loaded": models.loaded, "version": models.version, "error": models.error}}

    @app.get("/api/system/status", tags=["system"])
    def system_status():
        refresh_offline(db, s)
        now = datetime.now(timezone.utc)
        with db.read() as conn:
            t = load_thresholds(conn)
            devices = []
            for d in conn.execute("SELECT * FROM devices ORDER BY last_seen_ms DESC").fetchall():
                dd = row_to_dict(d, ("last_sensor_status",))
                age = (now - parse_iso(dd["last_seen_at"])).total_seconds() if dd["last_seen_at"] else None
                dd["age_s"] = None if age is None else round(age, 1)
                dd["state"] = ("never_seen" if age is None else "online" if age <= t.offline_after_s
                               else "stale" if age <= 10 * t.offline_after_s else "offline")
                src = conn.execute("SELECT source FROM batteries WHERE battery_id=?", (dd["battery_id"],)).fetchone()
                dd["data_origin"] = src["source"] if src else None
                last = conn.execute("SELECT cell_state FROM readings WHERE device_id=? ORDER BY ts_ms DESC LIMIT 1",
                                    (dd["device_id"],)).fetchone()
                dd["cell_state"] = last["cell_state"] if last else None
                devices.append(dd)
            counts = {r["source"]: r["n"] for r in conn.execute(
                "SELECT source, COUNT(*) AS n FROM readings GROUP BY source").fetchall()}
            last = conn.execute("SELECT MAX(ts) AS ts, MAX(received_at) AS rx FROM readings").fetchone()
        return {"api": health(), "devices": devices, "readings_by_source": counts,
                "last_telemetry_ts": last["ts"], "last_received_at": last["rx"],
                "offline_after_s": t.offline_after_s, "model": models.info()}

    # ------------------------------------------------------------ ingest
    @app.post("/api/battery/reading", tags=["telemetry"])
    def post_reading(r: ReadingIn):
        return ingest(db, s, models, r)

    @app.post("/api/battery/readings", tags=["telemetry"])
    def post_readings(batch: ReadingBatchIn):
        results = []
        for r in sorted(batch.readings, key=lambda x: (x.timestamp is None, x.timestamp or datetime.min.replace(tzinfo=timezone.utc))):
            try:
                results.append(ingest(db, s, models, r))
            except HTTPException as e:
                results.append({"status": "rejected", "detail": e.detail, "seq": r.seq})
        return {"results": results, "stored": sum(x["status"] == "stored" for x in results),
                "duplicates": sum(x["status"] == "duplicate" for x in results),
                "rejected": sum(x["status"] == "rejected" for x in results)}

    @app.post("/api/battery/cycle", tags=["cycles"])
    def post_cycle(c: CycleIn):
        with db.write() as conn:
            b = ensure_battery(conn, c.battery_id, c.source)
            if c.rated_capacity_ah is not None and b.get("rated_capacity_ah") is None:
                conn.execute("UPDATE batteries SET rated_capacity_ah=? WHERE battery_id=?",
                             (c.rated_capacity_ah, c.battery_id))
                b["rated_capacity_ah"] = c.rated_capacity_ah
            cycle, dup, pred = insert_cycle_features(conn, b, c.model_dump(), models)
        return {"status": "duplicate" if dup else "stored", "cycle_id": cycle["id"], "prediction": pred}

    # ------------------------------------------------------------ batteries
    @app.get("/api/batteries", tags=["batteries"])
    def list_batteries():
        refresh_offline(db, s)
        now = datetime.now(timezone.utc)
        with db.read() as conn:
            rows = conn.execute("SELECT * FROM batteries ORDER BY battery_id").fetchall()
            return {"batteries": [battery_summary(conn, dict(r), now, s) for r in rows]}

    @app.get("/api/battery/{battery_id}", tags=["batteries"])
    def get_battery(battery_id: str):
        with db.read() as conn:
            return battery_summary(conn, _battery(conn, battery_id), datetime.now(timezone.utc), s)

    @app.put("/api/battery/{battery_id}", tags=["batteries"])
    def update_battery(battery_id: str, body: BatteryUpsert):
        fields = body.model_dump(exclude_unset=True)
        with db.write() as conn:
            _battery(conn, battery_id)
            if fields:
                conn.execute(f"UPDATE batteries SET {', '.join(f'{k}=?' for k in fields)} WHERE battery_id=?",
                             [*fields.values(), battery_id])
            return dict(conn.execute("SELECT * FROM batteries WHERE battery_id=?", (battery_id,)).fetchone())

    @app.post("/api/battery/{battery_id}/presence", tags=["batteries"])
    def set_presence(battery_id: str, body: PresenceIn):
        """Operator confirmation that a cell is connected (or withdrawal of it).

        The INA219 cannot distinguish a floating input from a resting cell, so an
        in-range voltage at ~0 A stays "unverified" until current flows or someone
        confirms. Confirmation is refused if the latest reading is out of the Li-ion
        range or stale; it lapses automatically on an out-of-range reading or a gap.
        """
        with db.write() as conn:
            b = _battery(conn, battery_id)
            if b["source"] == "nasa_replay":
                raise HTTPException(400, "NASA replay batteries have no live cell")
            if b.get("managed") and body.confirmed and battery_assignment(conn, battery_id) is None:
                raise HTTPException(409, f"'{battery_id}' is not selected as the connected battery on any device. "
                                         f"Select it first; selecting a profile and confirming the cell are separate steps.")
            if not body.confirmed:
                save_presence(conn, battery_id, Presence(note=body.note))
                return {"battery_id": battery_id, "presence": load_presence(conn, battery_id).__dict__}
            last = conn.execute("SELECT ts, ts_ms, received_at, voltage_v FROM readings WHERE battery_id=? "
                                "ORDER BY ts_ms DESC LIMIT 1", (battery_id,)).fetchone()
            if last is None or last["voltage_v"] is None:
                raise HTTPException(409, "no voltage reading yet; connect the device first")
            age = (datetime.now(timezone.utc) - parse_iso(last["received_at"])).total_seconds()
            if age > s.presence_gap_s:
                raise HTTPException(409, f"latest reading is {age:.0f} s old; confirm while the device is reporting")
            if not plausible(last["voltage_v"], s):
                raise HTTPException(409, f"latest reading {last['voltage_v']:.3f} V is outside the Li-ion range "
                                         f"({s.cell_plausible_min_v:g}-{s.cell_plausible_max_v:g} V): that is not "
                                         f"consistent with a connected, usable cell")
            p = Presence("operator", last["ts"], last["ts_ms"], body.note)
            save_presence(conn, battery_id, p)
            return {"battery_id": battery_id, "presence": p.__dict__, "confirmed_at_voltage_v": last["voltage_v"]}

    # ------------------------------------------------------------ devices / multi-battery
    @app.get("/api/devices", tags=["devices"])
    def list_devices():
        with db.read() as conn:
            ids = [r["device_id"] for r in conn.execute(
                "SELECT device_id FROM devices UNION SELECT device_id FROM device_assignments ORDER BY 1").fetchall()]
            out = []
            for d in ids:
                row = conn.execute("SELECT device_id, last_seen_at, firmware_version FROM devices WHERE device_id=?",
                                   (d,)).fetchone()
                out.append({"device_id": d, "last_seen_at": row["last_seen_at"] if row else None,
                            "firmware_version": row["firmware_version"] if row else None,
                            "assignment": active_assignment(conn, d)})
        return {"devices": out}

    @app.put("/api/devices/{device_id}/assignment", tags=["devices"])
    def set_assignment(device_id: str, body: DeviceAssignmentIn):
        """Select the physical battery connected to a device (``battery_id: null`` = none).

        Ends the previous battery's recording session, starts recording to the new
        profile from now, and clears presence evidence on both: selecting a profile
        does not prove the cell is connected.
        """
        with db.write() as conn:
            return switch_assignment(conn, device_id, body.battery_id, body.note, s)

    @app.get("/api/devices/{device_id}/assignments", tags=["devices"])
    def assignment_history(device_id: str, limit: int = Query(200, ge=1, le=5000)):
        with db.read() as conn:
            rows = conn.execute("SELECT * FROM device_assignments WHERE device_id=? ORDER BY start_ms DESC LIMIT ?",
                                (device_id, limit)).fetchall()
        return {"device_id": device_id, "assignments": [dict(r) for r in rows]}

    @app.get("/api/battery/{battery_id}/stats", tags=["sessions"])
    def get_stats(battery_id: str):
        refresh_offline(db, s)
        with db.read() as conn:
            b = _battery(conn, battery_id)
            hist = conn.execute("SELECT * FROM device_assignments WHERE battery_id=? ORDER BY start_ms DESC",
                                (battery_id,)).fetchall()
            return {"battery_id": battery_id, "stats": battery_stats(conn, b, s),
                    "assignments": [dict(r) for r in hist]}

    @app.get("/api/batteries/compare", tags=["batteries"])
    def compare(include_all: bool = False):
        """Side-by-side measured statistics for the physical battery profiles."""
        refresh_offline(db, s)
        with db.read() as conn:
            q = "SELECT * FROM batteries" + ("" if include_all else " WHERE managed=1 AND source=?") + " ORDER BY battery_id"
            rows = [dict(r) for r in conn.execute(q, () if include_all else (profile_source(s),)).fetchall()]
            out = []
            for b in rows:
                last = conn.execute("SELECT ts, cell_state FROM readings WHERE battery_id=? ORDER BY ts_ms DESC LIMIT 1",
                                    (b["battery_id"],)).fetchone()
                n = conn.execute("SELECT COUNT(*) AS n FROM readings WHERE battery_id=?", (b["battery_id"],)).fetchone()["n"]
                out.append({"battery_id": b["battery_id"], "label": b["label"], "managed": bool(b["managed"]),
                            "source": b["source"],
                            "rated_capacity_mah": b["rated_capacity_ah"] * 1000 if b["rated_capacity_ah"] else None,
                            "assignment": battery_assignment(conn, b["battery_id"]),
                            "presence_evidence": b["presence_evidence"],
                            "last_reading_ts": last["ts"] if last else None,
                            "last_cell_state": last["cell_state"] if last else None,
                            "n_readings": n, "stats": battery_stats(conn, b, s),
                            "soh": "unavailable" if b["managed"] else None})
        return {"batteries": out,
                "note": "Measured values per battery. SoH/RUL are not produced for physical profiles."}

    @app.get("/api/battery/{battery_id}/latest", tags=["telemetry"])
    def latest(battery_id: str):
        with db.read() as conn:
            summary = battery_summary(conn, _battery(conn, battery_id), datetime.now(timezone.utc), s)
        if summary["latest_reading"] is None:
            raise HTTPException(404, f"no readings for '{battery_id}'")
        return {"battery_id": battery_id, "connection": summary["connection"], "reading": summary["latest_reading"]}

    @app.get("/api/battery/{battery_id}/history", tags=["telemetry"])
    def get_history(battery_id: str, start: Optional[datetime] = None, end: Optional[datetime] = None,
                    limit: int = Query(2000, ge=1, le=50000), max_points: Optional[int] = Query(None, ge=10, le=5000),
                    format: Literal["json", "csv"] = "json"):
        for v, n in ((start, "start"), (end, "end")):
            if v is not None and v.tzinfo is None:
                raise HTTPException(422, f"{n} must include a UTC offset (e.g. ...Z)")
        with db.read() as conn:
            _battery(conn, battery_id)
            h = history(conn, battery_id, start, end, limit, None if format == "csv" else max_points)
        if format == "csv":
            buf = io.StringIO()
            cols = ["ts", "device_id", "source", "cell_state", "voltage_v", "voltage_raw_v", "current_a",
                    "temperature_c", "power_w", "timestamp_source", "flags"]
            w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            for p in h["points"]:
                w.writerow({**p, "flags": ";".join(p.get("flags") or [])})
            return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv", headers={
                "Content-Disposition": f'attachment; filename="{battery_id}_history.csv"'})
        return h

    @app.get("/api/battery/{battery_id}/cycles", tags=["cycles"])
    def get_cycles(battery_id: str, limit: int = Query(1000, ge=1, le=10000)):
        with db.read() as conn:
            _battery(conn, battery_id)
            rows = conn.execute("SELECT * FROM cycles WHERE battery_id=? ORDER BY segment_index LIMIT ?",
                                (battery_id, limit)).fetchall()
        return {"battery_id": battery_id, "cycles": [row_to_dict(r, ("completeness_notes",)) for r in rows]}

    @app.post("/api/battery/{battery_id}/cycles/aggregate", tags=["cycles"])
    def run_aggregation(battery_id: str):
        with db.write() as conn:
            b = _battery(conn, battery_id)
            if b["source"] == "nasa_replay":
                raise HTTPException(400, "NASA replay batteries receive cycle features directly")
            created = aggregate_live_cycles(conn, battery_id, s, models,
                                            int(datetime.now(timezone.utc).timestamp() * 1000))
        return {"battery_id": battery_id, "cycles_closed": created}

    @app.get("/api/battery/{battery_id}/sessions", tags=["sessions"])
    def get_sessions(battery_id: str, limit: int = Query(100, ge=1, le=5000)):
        """Measured discharge sessions, newest first (charge/energy delivered per session).

        A session's charge is what flowed during that session; it is not the cell's
        capacity, state of charge or state of health.
        """
        refresh_offline(db, s)
        with db.read() as conn:
            b = _battery(conn, battery_id)
            rows = conn.execute(f"SELECT {SESSION_COLS} FROM discharge_sessions WHERE battery_id=? "
                                f"ORDER BY id DESC LIMIT ?", (battery_id, limit)).fetchall()
        return {"battery_id": battery_id,
                "rated_capacity_mah": b["rated_capacity_ah"] * 1000 if b.get("rated_capacity_ah") else None,
                "rated_capacity_note": "Manufacturer label rating, not a measurement.",
                "sessions": [session_out(r, s, b.get("cutoff_voltage_v"), b.get("rated_capacity_ah")) for r in rows]}

    @app.get("/api/battery/{battery_id}/predictions", tags=["predictions"])
    def get_predictions(battery_id: str, limit: int = Query(500, ge=1, le=10000),
                        include_unavailable: bool = True):
        with db.read() as conn:
            _battery(conn, battery_id)
            q = "SELECT p.*, c.ref_soh, c.ref_rul FROM predictions p LEFT JOIN cycles c ON c.id=p.cycle_id " \
                "WHERE p.battery_id=?" + ("" if include_unavailable else " AND p.status!='unavailable'") + \
                " ORDER BY p.id DESC LIMIT ?"
            rows = conn.execute(q, (battery_id, limit)).fetchall()
        return {"battery_id": battery_id, "predictions": [row_to_dict(r, PRED_JSON) for r in reversed(rows)]}

    @app.post("/api/battery/{battery_id}/predict", tags=["predictions"])
    def predict_latest(battery_id: str):
        """Re-run the gated model on the most recent cycle (e.g. after registering cycle_count_offset)."""
        with db.write() as conn:
            b = _battery(conn, battery_id)
            c = conn.execute("SELECT * FROM cycles WHERE battery_id=? ORDER BY segment_index DESC LIMIT 1",
                             (battery_id,)).fetchone()
            if c is None:
                raise HTTPException(404, "no cycles recorded for this battery yet")
            cycle = row_to_dict(c, ("completeness_notes",))
            src = "nasa_replay" if cycle["source"] == "nasa_replay" else b["source"]
            return store_prediction(conn, models, b, cycle, src)

    # ------------------------------------------------------------ alerts
    @app.get("/api/alerts", tags=["alerts"])
    def list_alerts(battery_id: Optional[str] = None, status: Optional[Literal["active", "resolved"]] = None,
                    limit: int = Query(200, ge=1, le=5000)):
        refresh_offline(db, s)
        where, args = [], []
        if battery_id:
            where.append("battery_id=?"); args.append(battery_id)
        if status:
            where.append("status=?"); args.append(status)
        q = "SELECT * FROM alerts" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY last_seen DESC LIMIT ?"
        with db.read() as conn:
            rows = conn.execute(q, [*args, limit]).fetchall()
        return {"alerts": [dict(r) for r in rows]}

    @app.get("/api/battery/{battery_id}/alerts", tags=["alerts"])
    def battery_alerts(battery_id: str, status: Optional[Literal["active", "resolved"]] = None,
                       limit: int = Query(200, ge=1, le=5000)):
        with db.read() as conn:
            _battery(conn, battery_id)
        return list_alerts(battery_id, status, limit)

    @app.post("/api/alerts/{alert_id}/acknowledge", tags=["alerts"])
    def ack_alert(alert_id: int):
        with db.write() as conn:
            cur = conn.execute("UPDATE alerts SET acknowledged=1 WHERE id=?", (alert_id,))
            if cur.rowcount == 0:
                raise HTTPException(404, "alert not found")
        return {"id": alert_id, "acknowledged": True}

    @app.get("/api/settings/thresholds", tags=["alerts"])
    def get_thresholds():
        with db.read() as conn:
            return load_thresholds(conn)

    @app.put("/api/settings/thresholds", tags=["alerts"])
    def put_thresholds(t: Thresholds):
        save_thresholds(db, t)
        return t

    # ------------------------------------------------------------ models / NASA
    @app.get("/api/models", tags=["models"])
    def model_info():
        return {**models.info(), "lobo": nasa.metrics()}

    # ------------------------------------------------------------ battery test manager (records only)
    @app.post("/api/tests", tags=["tests"])
    def create_test(body: TestCreate):
        with db.write() as conn:
            return battery_tests.create_test(conn, body.battery_id, body.notes)

    @app.get("/api/tests", tags=["tests"])
    def list_tests(battery_id: Optional[str] = None, state: Optional[str] = None):
        where, args = [], []
        if battery_id:
            where.append("battery_id=?"); args.append(battery_id)
        if state:
            where.append("state=?"); args.append(state)
        q = "SELECT test_uid FROM battery_tests" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id DESC"
        with db.read() as conn:
            uids = [r["test_uid"] for r in conn.execute(q, args).fetchall()]
            views = [battery_tests.test_view(conn, battery_tests._load(conn, u), s) for u in uids]
        return {"tests": views, "hardware_notice": battery_tests.HARDWARE_NOTICE}

    @app.get("/api/tests/export.csv", tags=["tests"])
    def export_tests(battery_id: Optional[str] = None):
        data = list_tests(battery_id)["tests"]
        name = f"battery_tests_{battery_id or 'all'}.csv"
        return StreamingResponse(iter([battery_tests.tests_csv(data)]), media_type="text/csv",
                                 headers={"Content-Disposition": f'attachment; filename="{name}"'})

    @app.get("/api/tests/{uid}", tags=["tests"])
    def get_test(uid: str):
        with db.read() as conn:
            return battery_tests.test_view(conn, battery_tests._load(conn, uid), s)

    @app.get("/api/tests/{uid}/readings.csv", tags=["tests"])
    def test_readings_csv(uid: str):
        with db.read() as conn:
            t = battery_tests._load(conn, uid)
            if t["started_ms"] is None:
                raise HTTPException(409, "test has not started")
            end = t["ended_ms"] or int(datetime.now(timezone.utc).timestamp() * 1000)
            wins = battery_tests.running_intervals(t, end)
            rows = conn.execute("SELECT ts, ts_ms, device_id, voltage_v, current_a, power_w, temperature_c, cell_state, "
                                "flags FROM readings WHERE battery_id=? AND ts_ms BETWEEN ? AND ? ORDER BY ts_ms",
                                (t["battery_id"], t["started_ms"], end)).fetchall()
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["test_uid", "battery_id", "ts", "test_running", "device_id", "voltage_v", "current_a", "power_w",
                    "temperature_c", "cell_state", "flags"])
        for r in rows:
            running = any(a <= r["ts_ms"] <= b for a, b in wins)
            w.writerow([uid, t["battery_id"], r["ts"], running, r["device_id"], r["voltage_v"], r["current_a"],
                        r["power_w"], r["temperature_c"], r["cell_state"], r["flags"]])
        return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                                 headers={"Content-Disposition": f'attachment; filename="{uid}_readings.csv"'})

    @app.put("/api/tests/{uid}/checklist", tags=["tests"])
    def put_checklist(uid: str, body: TestChecklist):
        with db.write() as conn:
            out = battery_tests.update_checklist(conn, uid, body.model_dump())
        return {**out, "hardware_notice": battery_tests.HARDWARE_NOTICE}

    @app.post("/api/tests/{uid}/{action}", tags=["tests"])
    def test_action(uid: str, action: Literal["start", "pause", "resume", "complete", "abort"],
                    body: Optional[TestAction] = None):
        """Change the SOFTWARE test state. Does not connect, disconnect or control any hardware."""
        with db.write() as conn:
            return battery_tests.transition(conn, uid, action, body.reason if body else None, s)

    # ------------------------------------------------------------ aging analytics (physical batteries)
    @app.get("/api/aging", tags=["aging"])
    def aging_overview():
        """Measured aging analytics for the physical battery profiles, side by side."""
        refresh_offline(db, s)
        with db.read() as conn:
            rows = [dict(r) for r in conn.execute("SELECT * FROM batteries WHERE managed=1 AND source=? "
                                                  "ORDER BY battery_id", (profile_source(s),))]
            out = [aging.battery_aging(conn, b, s) for b in rows]
        for a in out:            # overview keeps payload small; detail endpoint has everything
            a.pop("sessions"), a.pop("trends")
        return {"batteries": out, "rules": aging.comparability_rules()}

    @app.get("/api/aging/{battery_id}", tags=["aging"])
    def aging_detail(battery_id: str):
        refresh_offline(db, s)
        with db.read() as conn:
            b = _battery(conn, battery_id)
            if not b.get("managed"):
                raise HTTPException(400, f"'{battery_id}' is not a physical battery profile")
            return {**aging.battery_aging(conn, b, s), "rules": aging.comparability_rules()}

    # ------------------------------------------------------------ NASA ML validation demo
    @app.get("/api/ml-validation", tags=["ml-validation"])
    def ml_validation_summary():
        """Leakage-free leave-one-cell-out evaluation of the NASA SoH/RUL models (demo only)."""
        return ml_validation.summary(s)

    @app.get("/api/ml-validation/{cell}", tags=["ml-validation"])
    def ml_validation_cell(cell: str):
        d = ml_validation.cell_detail(s, cell)
        if d is None:
            raise HTTPException(404, f"'{cell}' is not one of {', '.join(ml_validation.CELLS)}")
        return d

    @app.get("/api/nasa/batteries", tags=["nasa"])
    def nasa_batteries():
        return {"batteries": nasa.batteries()}

    @app.get("/api/nasa/{battery_id}/cycles", tags=["nasa"])
    def nasa_cycles(battery_id: str):
        rows = nasa.cycles(battery_id)
        if rows is None:
            raise HTTPException(404, f"NASA battery '{battery_id}' not in the processed dataset")
        return {"battery_id": battery_id, "source": "NASA PCoE (processed)", "cycles": rows,
                "prediction_note": "SoH_lobo_pred / RUL_lobo_pred are held-out (leave-one-battery-out) predictions."}

    return app

