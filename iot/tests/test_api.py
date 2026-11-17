"""API, validation, persistence, alerts and cycle aggregation."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from iot.backend.api import create_app
from iot.backend.config import Settings

from .conftest import iso, needs_models, past, reading


# ------------------------------------------------------------------ basics
def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["database"]["ok"] is True
    assert body["status"] in ("ok", "degraded")


def test_cors_preflight_from_vite_dev_server(client):
    r = client.options("/api/batteries", headers={"Origin": "http://localhost:5173",
                                                   "Access-Control-Request-Method": "GET"})
    assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_store_and_read_latest(client):
    r = client.post("/api/battery/reading", json=reading(v=3.89, i=0.0, t=24.5, seq=1, boot_id="b1"))
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "stored"
    latest = client.get("/api/battery/HW-1/latest").json()
    assert latest["reading"]["voltage_v"] == 3.89
    assert latest["reading"]["temperature_c"] == 24.5
    assert latest["connection"]["state"] == "online"
    lst = client.get("/api/batteries").json()["batteries"]
    assert [b["battery_id"] for b in lst] == ["HW-1"] and lst[0]["source"] == "hardware"


def test_legacy_field_aliases_are_accepted(client):
    body = {"battery_id": "HW-1", "voltage": 3.9, "current": 0.37, "temperature": 25.0, "power": 1.44,
            "timestamp": iso(datetime.now(timezone.utc))}
    r = client.post("/api/battery/reading", json=body)
    assert r.status_code == 200, r.text
    assert client.get("/api/battery/HW-1/latest").json()["reading"]["current_a"] == 0.37


@pytest.mark.parametrize("patch,fragment", [
    ({"current_a": 370.0}, "current_a"),         # mA sent as A -> rejected, never rescaled
    ({"voltage_v": 3890.0}, "voltage_v"),        # mV sent as V
    ({"temperature_c": 200.0}, "temperature_c"),
    ({"battery_id": "bad id!"}, "battery_id"),
    ({"timestamp": "2026-01-01T00:00:00"}, "UTC offset"),
    ({"unexpected": 1}, "unexpected"),
])
def test_validation_rejects_bad_input(client, patch, fragment):
    body = reading()
    body.update(patch)
    r = client.post("/api/battery/reading", json=body)
    assert r.status_code == 422
    assert fragment in r.text


def test_future_and_stale_timestamps_rejected(client):
    r = client.post("/api/battery/reading", json=reading(ts=datetime.now(timezone.utc) + timedelta(hours=1)))
    assert r.status_code == 422 and "future" in r.text
    r = client.post("/api/battery/reading", json=reading(ts=datetime.now(timezone.utc) - timedelta(days=30)))
    assert r.status_code == 422


def test_missing_timestamp_uses_server_time_and_is_flagged(client):
    body = reading()
    del body["timestamp"]
    out = client.post("/api/battery/reading", json=body).json()
    assert out["timestamp_source"] == "server" and "server_timestamp" in out["flags"]


def test_retried_message_is_deduplicated(client):
    body = reading(seq=7, boot_id="boot1")
    a = client.post("/api/battery/reading", json=body).json()
    b = client.post("/api/battery/reading", json=body).json()
    assert a["status"] == "stored" and b["status"] == "duplicate" and b["reading_id"] == a["reading_id"]
    assert client.get("/api/battery/HW-1/history").json()["total"] == 1


def test_batch_ingest_reports_each_item(client):
    good = [reading(ts=past(10 - k), seq=k, boot_id="bb") for k in range(3)]
    bad = reading(ts=datetime.now(timezone.utc) + timedelta(hours=2), seq=99, boot_id="bb")
    out = client.post("/api/battery/readings", json={"readings": good + [good[0], bad]}).json()
    assert (out["stored"], out["duplicates"], out["rejected"]) == (3, 1, 1)


def test_power_computed_or_checked(client):
    client.post("/api/battery/reading", json=reading(v=4.0, i=0.4, ts=past(5)))
    rd = client.get("/api/battery/HW-1/latest").json()["reading"]
    assert rd["power_w"] == pytest.approx(1.6) and rd["power_source"] == "computed"
    out = client.post("/api/battery/reading", json=reading(v=4.0, i=0.4, power_w=5.0, ts=past(4))).json()
    assert "power_inconsistent" in out["flags"]


def test_ds18b20_disconnect_sentinel(client):
    out = client.post("/api/battery/reading", json=reading(t=-127.0)).json()
    assert "temperature_sensor_fault" in out["flags"]
    assert client.get("/api/battery/HW-1/latest").json()["reading"]["temperature_c"] is None
    alerts = client.get("/api/battery/HW-1/alerts?status=active").json()["alerts"]
    assert any(a["type"] == "temperature_sensor_fault" for a in alerts)


def test_ds18b20_power_on_value_not_stored(client):
    out = client.post("/api/battery/reading", json=reading(t=85.0)).json()
    assert "ds18b20_power_on_value" in out["flags"]
    assert client.get("/api/battery/HW-1/latest").json()["reading"]["temperature_c"] is None


def test_floating_ina219_input_is_not_battery_voltage(client):
    out = client.post("/api/battery/reading", json=reading(v=0.04, i=0.0)).json()
    assert "battery_not_detected" in out["flags"]
    pts = client.get("/api/battery/HW-1/history").json()["points"]
    assert pts[0]["voltage_v"] is None and pts[0]["voltage_raw_v"] == 0.04
    types = {a["type"] for a in client.get("/api/alerts?status=active").json()["alerts"]}
    assert "battery_not_detected" in types and "voltage_low" not in types


def test_ina219_fault_drops_electrical_values(client):
    out = client.post("/api/battery/reading", json=reading(sensor_status={"ina219": "not_found", "ds18b20": "ok"})).json()
    assert "ina219_fault" in out["flags"]
    rd = client.get("/api/battery/HW-1/latest").json()["reading"]
    assert rd["voltage_v"] is None and rd["current_a"] is None and rd["temperature_c"] == 25.0


def test_sources_cannot_be_mixed_on_one_battery(client):
    client.post("/api/battery/reading", json=reading())
    r = client.post("/api/battery/reading", json=reading(source="simulation"))
    assert r.status_code == 409


# ------------------------------------------------------------------ alerts
def test_temperature_alert_raise_dedupe_resolve(client):
    for k, temp in enumerate([50.0, 56.0, 57.0]):
        client.post("/api/battery/reading", json=reading(t=temp, ts=past(30 - k)))
    active = [a for a in client.get("/api/alerts?status=active").json()["alerts"] if a["type"] == "temperature_high"]
    assert len(active) == 1 and active[0]["severity"] == "critical" and active[0]["occurrences"] == 3
    client.post("/api/battery/reading", json=reading(t=30.0, ts=past(20)))
    resolved = [a for a in client.get("/api/alerts?status=resolved").json()["alerts"] if a["type"] == "temperature_high"]
    assert len(resolved) == 1
    ack = client.post(f"/api/alerts/{resolved[0]['id']}/acknowledge")
    assert ack.status_code == 200


def test_voltage_alerts_and_thresholds(client):
    t = client.get("/api/settings/thresholds").json()
    assert t["voltage_low_crit_v"] == 3.0
    t["voltage_low_warn_v"] = 3.5
    assert client.put("/api/settings/thresholds", json=t).status_code == 200
    client.post("/api/battery/reading", json=reading(v=3.45))
    a = [x for x in client.get("/api/alerts?status=active").json()["alerts"] if x["type"] == "voltage_low"]
    assert a and a[0]["severity"] == "warning" and a[0]["unit"] == "V"
    t["voltage_low_crit_v"] = 3.6  # crit above warn: invalid
    assert client.put("/api/settings/thresholds", json=t).status_code == 422


def test_device_offline_alert(client):
    t = client.get("/api/settings/thresholds").json()
    t["offline_after_s"] = 5
    client.put("/api/settings/thresholds", json=t)
    app = client.app
    from iot.backend.schemas import ReadingIn
    from iot.backend.telemetry import ingest
    ingest(app.state.db, app.state.settings, app.state.models, ReadingIn(**reading(ts=past(60))),
           now=datetime.now(timezone.utc) - timedelta(seconds=60))
    b = client.get("/api/batteries").json()["batteries"][0]
    assert b["connection"]["state"] in ("stale", "offline")
    off = [a for a in client.get("/api/alerts?status=active").json()["alerts"] if a["type"] == "device_offline"]
    assert len(off) == 1
    for _ in range(3):  # dashboard polling must not inflate the count
        client.get("/api/batteries")
    off = [a for a in client.get("/api/alerts?status=active").json()["alerts"] if a["type"] == "device_offline"]
    assert off[0]["occurrences"] == 1
    client.post("/api/battery/reading", json=reading())
    assert not any(a["type"] == "device_offline" for a in client.get("/api/alerts?status=active").json()["alerts"])


# ------------------------------------------------------------------ history
def test_history_range_downsample_and_csv(client):
    base = datetime.now(timezone.utc) - timedelta(hours=1)
    batch = [reading(ts=base + timedelta(seconds=10 * k), v=3.9 - k * 0.001, i=0.37, seq=k, boot_id="h")
             for k in range(300)]
    for chunk in (batch[:150], batch[150:]):
        assert client.post("/api/battery/readings", json={"readings": chunk}).json()["stored"] == 150
    full = client.get("/api/battery/HW-1/history").json()
    assert full["total"] == 300 and not full["downsampled"]
    rng = client.get("/api/battery/HW-1/history", params={
        "start": iso(base + timedelta(seconds=100)), "end": iso(base + timedelta(seconds=199))}).json()
    assert rng["total"] == 10
    ds = client.get("/api/battery/HW-1/history", params={"max_points": 50}).json()
    assert ds["downsampled"] and len(ds["points"]) <= 51
    lim = client.get("/api/battery/HW-1/history", params={"limit": 20}).json()
    assert len(lim["points"]) == 20 and lim["truncated"]
    csv = client.get("/api/battery/HW-1/history", params={"format": "csv"})
    assert csv.headers["content-type"].startswith("text/csv")
    assert csv.text.splitlines()[0].startswith("ts,device_id,source,voltage_v")
    assert len(csv.text.strip().splitlines()) == 301
    assert client.get("/api/battery/HW-1/history", params={"start": "2026-01-01T00:00:00"}).status_code == 422


def test_unknown_battery_404(client):
    assert client.get("/api/battery/NOPE/latest").status_code == 404
    assert client.get("/api/battery/NOPE/history").status_code == 404


def test_sqlite_persistence_across_restart(settings):
    c1 = TestClient(create_app(settings))
    c1.post("/api/battery/reading", json=reading(seq=1, boot_id="p"))
    c2 = TestClient(create_app(settings))
    assert c2.get("/api/battery/HW-1/history").json()["total"] == 1


def test_database_error_returns_503(client, monkeypatch):
    db = client.app.state.db

    def boom(*a, **k):
        raise sqlite3.OperationalError("disk I/O error")
    monkeypatch.setattr(db, "_connect", boom)
    r = client.get("/api/batteries")
    assert r.status_code == 503 and "database error" in r.json()["detail"]


# ------------------------------------------------------------------ cycles
def _discharge(client, battery, start, n, i_a, v0, v1, t0, t1, seq0=0, step_s=10, rest=8):
    """Post a discharge of n samples followed by `rest` rest samples."""
    rows = []
    for k in range(n):
        f = k / (n - 1)
        rows.append(reading(battery=battery, ts=start + timedelta(seconds=step_s * k), v=v0 + (v1 - v0) * f,
                            i=i_a, t=t0 + (t1 - t0) * f, seq=seq0 + k, boot_id="cyc"))
    for k in range(rest):
        rows.append(reading(battery=battery, ts=start + timedelta(seconds=step_s * (n + k)), v=v1 + 0.3, i=0.0,
                            t=t1, seq=seq0 + n + k, boot_id="cyc"))
    return client.post("/api/battery/readings", json={"readings": rows}).json()


@needs_models
def test_resistor_discharge_aggregates_but_prediction_unavailable(client):
    start = datetime.now(timezone.utc) - timedelta(hours=2)
    out = _discharge(client, "HW-1", start, 60, 0.37, 3.95, 2.98, 25.0, 25.6)
    closed = [c for r in out["results"] for c in r["cycles_closed"]]
    assert len(closed) == 1 and closed[0]["complete"] is True
    pred = closed[0]["prediction"]
    assert pred["status"] == "unavailable" and pred["soh_pct"] is None
    assert any("Cycle_number" in reason for reason in pred["reasons"])   # cycle count unknown
    # ...and the envelope problem is reported up front, not hidden behind the first reason
    assert any("outside NASA training range" in reason for reason in pred["reasons"])

    # Registering the cycle count still does not make it valid: features are out of range.
    client.put("/api/battery/HW-1", json={"cycle_count_offset": 0, "rated_capacity_ah": 3.0})
    cyc = client.get("/api/battery/HW-1/cycles").json()["cycles"][0]
    # NASA sign convention; the 60 s relaxation tail (7 rest samples at 0 A) is included, as in NASA records
    assert cyc["current_mean"] == pytest.approx(-0.37 * 60 / 67, abs=1e-6)
    assert cyc["n_readings"] == 67
    p = client.post("/api/battery/HW-1/predict").json()
    assert p["status"] == "unavailable"


def test_partial_discharge_is_incomplete(client):
    start = datetime.now(timezone.utc) - timedelta(hours=2)
    out = _discharge(client, "HW-1", start, 60, 0.37, 3.95, 3.70, 25.0, 25.4)
    closed = [c for r in out["results"] for c in r["cycles_closed"]]
    assert len(closed) == 1 and closed[0]["complete"] is False
    assert any("end-of-discharge" in n for n in closed[0]["notes"])
    assert closed[0]["prediction"]["status"] == "unavailable"


@needs_models
def test_nasa_like_live_cycle_gets_experimental_prediction(client):
    client.post("/api/battery/reading", json=reading(battery="LAB-2A", ts=past(7200), v=4.19, i=0.0, t=24.0,
                                                     seq=0, boot_id="x"))
    client.put("/api/battery/LAB-2A", json={"cycle_count_offset": 20, "rated_capacity_ah": 2.0,
                                            "cutoff_voltage_v": 2.7})
    start = datetime.now(timezone.utc) - timedelta(minutes=90)
    out = _discharge(client, "LAB-2A", start, 120, 1.9, 4.19, 2.7, 24.5, 39.0, seq0=1, step_s=20)
    closed = [c for r in out["results"] for c in r["cycles_closed"]]
    assert len(closed) == 1 and closed[0]["complete"]
    assert closed[0]["prediction"]["status"] == "experimental", closed[0]["prediction"]
    preds = client.get("/api/battery/LAB-2A/predictions").json()["predictions"]
    assert preds[-1]["cycle_number"] == 21 and preds[-1]["model_version"].startswith("rf-nasa-")


def test_open_segment_not_stored_until_closed(client):
    start = datetime.now(timezone.utc) - timedelta(seconds=200)
    rows = [reading(ts=start + timedelta(seconds=5 * k), v=3.9 - 0.001 * k, i=0.37, seq=k, boot_id="o")
            for k in range(30)]
    client.post("/api/battery/readings", json={"readings": rows})
    assert client.get("/api/battery/HW-1/cycles").json()["cycles"] == []


# ------------------------------------------------------------------ NASA replay
@needs_models
def test_nasa_replay_cycle_in_sample_and_idempotent(client):
    df = pd.read_csv(Settings().features_csv)
    row = df[(df.Battery_ID == "B0005")].iloc[10]
    from iot.simulator.simulate_esp32 import nasa_cycle_payload
    body = nasa_cycle_payload({k: str(v) for k, v in row.items()}, "NASA-B0005")
    a = client.post("/api/battery/cycle", json=body).json()
    assert a["status"] == "stored" and a["prediction"]["status"] == "nasa_in_sample"
    b = client.post("/api/battery/cycle", json=body).json()
    assert b["status"] == "duplicate"
    p = client.get("/api/battery/NASA-B0005/predictions").json()["predictions"]
    assert len(p) == 1 and p[0]["ref_soh"] == pytest.approx(row.SoH)
    bad = dict(body, voltage_min=5.0)
    assert client.post("/api/battery/cycle", json=bad).status_code == 422


def test_nasa_and_model_endpoints(client):
    m = client.get("/api/models").json()
    assert m["features"][-1] == "Cycle_number" and len(m["lobo"]["per_fold"]) == 3
    bats = {b["battery_id"]: b for b in client.get("/api/nasa/batteries").json()["batteries"]}
    assert bats["B0005"]["n_cycles"] == 168 and bats["B0005"]["eol_cycle"] == 125
    cyc = client.get("/api/nasa/B0018/cycles").json()["cycles"]
    assert len(cyc) == 132 and "SoH_lobo_pred" in cyc[0]
    assert client.get("/api/nasa/B9999/cycles").status_code == 404
    st = client.get("/api/system/status").json()
    assert "model" in st and "devices" in st


def test_distinct_seq_same_millisecond_both_stored(client):
    ts = past(5)
    a = client.post("/api/battery/reading", json=reading(ts=ts, seq=1, boot_id="z")).json()
    b = client.post("/api/battery/reading", json=reading(ts=ts, seq=2, boot_id="z")).json()
    assert a["status"] == b["status"] == "stored"
    # without seq, an identical timestamp still deduplicates
    c = client.post("/api/battery/reading", json=reading(ts=past(2))).json()
    d = client.post("/api/battery/reading", json=dict(reading(ts=past(2)), timestamp=c["ts"])).json()
    assert d["status"] == "duplicate"


def test_v1_database_is_migrated(tmp_path, settings):
    import dataclasses, sqlite3 as sq
    from iot.backend.db import _SCHEMA_V1
    path = tmp_path / "old.db"
    con = sq.connect(path)
    con.executescript(_SCHEMA_V1)
    con.execute("INSERT INTO schema_version(version) VALUES (1)")
    con.commit(); con.close()
    c = TestClient(create_app(dataclasses.replace(settings, db_path=path)))
    ts = past(5)
    assert c.post("/api/battery/reading", json=reading(ts=ts, seq=1, boot_id="m")).json()["status"] == "stored"
    assert c.post("/api/battery/reading", json=reading(ts=ts, seq=2, boot_id="m")).json()["status"] == "stored"
    from iot.backend.db import SCHEMA_VERSION
    con = sq.connect(path)
    assert con.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == SCHEMA_VERSION
    assert "transport" in {r[1] for r in con.execute("PRAGMA table_info(readings)")}
