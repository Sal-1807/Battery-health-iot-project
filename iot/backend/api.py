"""FastAPI application factory: telemetry ingest, history, cycles, predictions, alerts.

The ASGI entry point is ``iot.backend.main:app``; tests call ``create_app(settings)``
with a temporary database instead.
"""

from __future__ import annotations

import csv
import io
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from . import __version__
from .alerts import load_thresholds, now_iso, refresh_offline, save_thresholds
from .config import Settings, get_settings
from .cycles import aggregate_live_cycles, insert_cycle_features, store_prediction
from .db import Database, row_to_dict
from .model_service import ModelService
from .nasa import NasaData
from .schemas import BatteryUpsert, CycleIn, ReadingBatchIn, ReadingIn, Thresholds
from .telemetry import battery_summary, ensure_battery, history, ingest, parse_iso

log = logging.getLogger("bhm")
PRED_JSON = ("reasons", "warnings", "out_of_range", "input_features")


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    s = settings or get_settings()
    db = Database(s.db_path)
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
                "server_time": now_iso(), "started_at": started, "database": {"ok": db_ok, "path": str(s.db_path)},
                "models": {"loaded": models.loaded, "version": models.version, "error": models.error}}

    @app.get("/api/system/status", tags=["system"])
    def system_status():
        refresh_offline(db)
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
        refresh_offline(db)
        now = datetime.now(timezone.utc)
        with db.read() as conn:
            rows = conn.execute("SELECT * FROM batteries ORDER BY battery_id").fetchall()
            return {"batteries": [battery_summary(conn, dict(r), now) for r in rows]}

    @app.get("/api/battery/{battery_id}", tags=["batteries"])
    def get_battery(battery_id: str):
        with db.read() as conn:
            return battery_summary(conn, _battery(conn, battery_id), datetime.now(timezone.utc))

    @app.put("/api/battery/{battery_id}", tags=["batteries"])
    def update_battery(battery_id: str, body: BatteryUpsert):
        fields = body.model_dump(exclude_unset=True)
        with db.write() as conn:
            _battery(conn, battery_id)
            if fields:
                conn.execute(f"UPDATE batteries SET {', '.join(f'{k}=?' for k in fields)} WHERE battery_id=?",
                             [*fields.values(), battery_id])
            return dict(conn.execute("SELECT * FROM batteries WHERE battery_id=?", (battery_id,)).fetchone())

    @app.get("/api/battery/{battery_id}/latest", tags=["telemetry"])
    def latest(battery_id: str):
        with db.read() as conn:
            summary = battery_summary(conn, _battery(conn, battery_id), datetime.now(timezone.utc))
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
            cols = ["ts", "device_id", "source", "voltage_v", "current_a", "temperature_c", "power_w",
                    "battery_detected", "timestamp_source", "flags"]
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
        refresh_offline(db)
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

