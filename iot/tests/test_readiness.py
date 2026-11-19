"""Regression tests for defects found in the pre-hardware readiness audit."""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from iot.backend.api import create_app
from iot.backend.assignments import switch_assignment
from iot.backend.config import DEFAULT_DB_PATH, Settings
from iot.backend.schemas import ReadingIn
from iot.backend.telemetry import ingest

from .conftest import iso, reading

DEV = "esp32-devkit-01"


# ---------------------------------------------------------------- database / mode isolation
def test_simulation_mode_refuses_the_production_database():
    with pytest.raises(ValueError, match="needs its own database"):
        create_app(dataclasses.replace(Settings(), db_path=DEFAULT_DB_PATH, mode="simulation"))


def test_simulation_database_cannot_be_opened_in_production_mode(tmp_path):
    path = tmp_path / "sim.db"
    TestClient(create_app(dataclasses.replace(Settings(), db_path=path, mode="simulation")))
    with pytest.raises(RuntimeError, match="is a simulation database"):
        create_app(dataclasses.replace(Settings(), db_path=path, mode="production"))


def test_production_database_cannot_be_opened_in_simulation_mode(tmp_path):
    path = tmp_path / "prod.db"
    c = TestClient(create_app(dataclasses.replace(Settings(), db_path=path)))
    c.post("/api/battery/reading", json=reading(device_id=DEV))
    with pytest.raises(RuntimeError, match="is a production database"):
        create_app(dataclasses.replace(Settings(), db_path=path, mode="simulation"))


def test_existing_unmarked_database_with_real_data_is_bound_to_production(tmp_path):
    """Databases created before the mode marker existed: real readings => production."""
    path = tmp_path / "legacy.db"
    c = TestClient(create_app(dataclasses.replace(Settings(), db_path=path)))
    c.post("/api/battery/reading", json=reading(device_id=DEV))
    with c.app.state.db.write() as conn:
        conn.execute("DELETE FROM settings WHERE key='db_mode'")          # as an older release left it
    with pytest.raises(RuntimeError):
        create_app(dataclasses.replace(Settings(), db_path=path, mode="simulation"))
    TestClient(create_app(dataclasses.replace(Settings(), db_path=path)))  # production still opens


# ---------------------------------------------------------------- offline alerts after switching
def _rx(app, s, t, current=0.0):
    ingest(app.state.db, s, app.state.models,
           ReadingIn(battery_id="CELL-18650-01", device_id=DEV, timestamp=t, voltage_v=3.9, current_a=current,
                     temperature_c=25.0), now=t)


def _assign(app, s, battery, t):
    with app.state.db.write() as conn:
        switch_assignment(conn, DEV, battery, None, s, now_ms=int(t.timestamp() * 1000))


def test_switched_away_batteries_are_not_reported_offline(client):
    app, s = client.app, client.app.state.settings
    now = datetime.now(timezone.utc)
    _rx(app, s, now - timedelta(minutes=20))                         # legacy flow -> CELL-18650-01
    _assign(app, s, "BAT-A-3000", now - timedelta(minutes=15))
    _rx(app, s, now - timedelta(minutes=14))
    _assign(app, s, "BAT-B-3000", now - timedelta(minutes=5))
    _rx(app, s, now - timedelta(seconds=3))                          # device online, measuring B
    off = [a["battery_id"] for a in client.get("/api/alerts?status=active").json()["alerts"]
           if a["type"] == "device_offline"]
    assert off == []
    bats = {b["battery_id"]: b for b in client.get("/api/batteries").json()["batteries"]}
    assert bats["BAT-A-3000"]["connection"]["state"] == "not_selected"
    assert bats["BAT-B-3000"]["connection"]["state"] == "online"


def test_selected_battery_still_goes_offline_when_device_stops(client):
    app, s = client.app, client.app.state.settings
    now = datetime.now(timezone.utc)
    _assign(app, s, "BAT-A-3000", now - timedelta(minutes=15))
    _rx(app, s, now - timedelta(minutes=10))                          # then silence
    off = [a["battery_id"] for a in client.get("/api/alerts?status=active").json()["alerts"]
           if a["type"] == "device_offline"]
    assert off == ["BAT-A-3000"]


# ---------------------------------------------------------------- reversed current in the Test Manager
def test_negative_current_is_not_reported_as_no_current(client):
    app, s = client.app, client.app.state.settings
    _assign(app, s, "BAT-B-3000", datetime.now(timezone.utc) - timedelta(minutes=1))
    uid = client.post("/api/tests", json={"battery_id": "BAT-B-3000"}).json()["test_uid"]
    client.put(f"/api/tests/{uid}/checklist", json={
        "battery_identity_confirmed": True, "load_rating_verified": True, "supervisor_approval": True,
        "disconnect_procedure_confirmed": True, "voltage_cutoff_confirmed": True, "supervisor_name": "x",
        "cutoff_v": 3.0, "load_resistance_ohm": 10, "load_power_rating_w": 5})
    client.post(f"/api/tests/{uid}/start")
    client.post("/api/battery/reading", json=reading(battery="X", device_id=DEV, ts=datetime.now(timezone.utc),
                                                     v=3.8, i=-0.37))
    hw = client.get(f"/api/tests/{uid}").json()["hardware_state"]["description"]
    assert "-0.370 A" in hw and "reversed" in hw and "no load current" not in hw
