"""Simulator -> API -> SQLite end-to-end (in-process HTTP via TestClient)."""

from __future__ import annotations

import argparse
import sqlite3

from iot.simulator import simulate_esp32 as sim


def _poster(client):
    def post(path, body):
        r = client.post(path, json=body)
        return r.status_code, r.json()
    return post


def test_simulated_telemetry_end_to_end(client, settings):
    args = argparse.Namespace(battery="SIM-1", device="sim-esp32", interval=5.0, speedup=30.0, cycles=1,
                              rest_s=600.0, backfill=True, fault="temp-disconnect", seed=3)
    assert sim.run_telemetry(_poster(client), args) == 0

    con = sqlite3.connect(settings.db_path)
    n, src = con.execute("SELECT COUNT(*), GROUP_CONCAT(DISTINCT source) FROM readings").fetchone()
    assert n > 100 and src == "simulation"

    cycles = client.get("/api/battery/SIM-1/cycles").json()["cycles"]
    assert len(cycles) == 1 and cycles[0]["complete"] == 1
    assert cycles[0]["voltage_min"] <= 3.02
    # resistor-load simulation is outside the NASA envelope -> no fabricated SoH
    preds = client.get("/api/battery/SIM-1/predictions").json()["predictions"]
    assert preds and all(p["status"] == "unavailable" for p in preds)

    types = {a["type"] for a in client.get("/api/battery/SIM-1/alerts").json()["alerts"]}
    assert "temperature_sensor_fault" in types


def test_nasa_replay_end_to_end(client):
    args = argparse.Namespace(battery="B0018", battery_id=None, delay=0.0)
    assert sim.run_nasa_replay(_poster(client), args) == 0
    preds = client.get("/api/battery/NASA-B0018/predictions").json()["predictions"]
    assert len(preds) == 132
    assert {p["status"] for p in preds} == {"nasa_in_sample"}
    assert all(p["ref_soh"] is not None for p in preds)
    b = client.get("/api/battery/NASA-B0018").json()
    assert b["rated_capacity_ah"] == 2.0 and b["connection"]["state"] == "not_applicable"


def test_ocv_curve_monotonic():
    vals = [sim.ocv(s / 100) for s in range(101)]
    assert all(b >= a for a, b in zip(vals, vals[1:]))
