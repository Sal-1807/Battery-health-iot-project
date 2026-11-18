"""Cell presence / measurement validity.

Floating INA219 inputs (0.72, 0.88, 1.14 V observed on the team's board with no
cell attached) must never be treated as battery voltage or raise voltage alerts,
while real connected-cell readings must still be recognised and alarmed on.
"""

from __future__ import annotations

import dataclasses
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from iot.backend.api import create_app
from iot.backend.config import Settings
from iot.backend.db import _SCHEMA_V1, _SCHEMA_V2, _SCHEMA_V3
from iot.backend.presence import Presence, classify

from .conftest import iso, past, reading

FLOATING_V = [0.72, 0.88, 1.14]
NOISE_A = [-0.0002, 0.0001, -0.0005]          # open-input shunt noise seen on the board


def legacy_fw(v, i=-0.0002, **kw):
    """Reading exactly as the deployed firmware sent it, including its own
    1.0 V 'battery_connected' guess (which the backend must ignore)."""
    return reading(v=v, i=i, t=25.0, sensor_status={"ina219": "ok", "ds18b20": "ok",
                                                    "battery_connected": v >= 1.0}, **kw)


def active(client, battery="HW-1"):
    return {a["type"]: a for a in client.get(f"/api/alerts?battery_id={battery}&status=active").json()["alerts"]}


# ---------------------------------------------------------------- floating input
@pytest.mark.parametrize("v", FLOATING_V)
def test_floating_reading_is_no_cell_and_raises_no_voltage_alert(client, v):
    out = client.post("/api/battery/reading", json=legacy_fw(v)).json()
    assert out["cell_state"] == "no_cell"
    assert "battery_not_detected" in out["flags"]
    a = active(client)
    assert "voltage_low" not in a and "voltage_high" not in a
    assert a["battery_not_detected"]["severity"] == "info"        # absence of a cell is not an alarm
    latest = client.get("/api/battery/HW-1/latest").json()["reading"]
    assert latest["cell_state"] == "no_cell" and latest["battery_detected"] is False


def test_floating_sequence_with_legacy_firmware_claim(client):
    """The original bug: 1.14 V crossed the old 1.0 V threshold and raised a CRITICAL alert."""
    for k, (v, i) in enumerate(zip(FLOATING_V, NOISE_A)):
        client.post("/api/battery/reading", json=legacy_fw(v, i=i, ts=past(30 - k), seq=k, boot_id="f"))
    a = active(client)
    assert "voltage_low" not in a
    assert "battery_not_detected" in a                          # not cleared by the 1.14 V reading
    pts = client.get("/api/battery/HW-1/history").json()["points"]
    assert [p["voltage_v"] for p in pts] == [None, None, None]  # never charted as cell voltage
    assert [p["voltage_raw_v"] for p in pts] == FLOATING_V      # raw values kept for diagnostics
    assert {p["cell_state"] for p in pts} == {"no_cell"}
    ds = client.get("/api/battery/HW-1/history", params={"max_points": 10}).json()
    assert ds["total"] == 3


def test_floating_input_cannot_be_confirmed_by_operator(client):
    client.post("/api/battery/reading", json=legacy_fw(1.14))
    r = client.post("/api/battery/HW-1/presence", json={"confirmed": True})
    assert r.status_code == 409 and "Li-ion range" in r.json()["detail"]


def test_noise_level_current_is_not_presence_evidence(client):
    # 3.9 V would be plausible, but +/-0.5 mA noise is not evidence of a circuit
    for k, i in enumerate(NOISE_A):
        out = client.post("/api/battery/reading", json=reading(v=3.9, i=i, ts=past(20 - k), seq=k, boot_id="n")).json()
        assert out["cell_state"] == "unverified"


# ---------------------------------------------------------------- real cell
def test_resting_cell_is_unverified_until_confirmed(client):
    """A real cell at rest (3.89 V, ~0 A) is indistinguishable from an input that
    happens to float there, so it is shown as Unverified, not as a measurement."""
    out = client.post("/api/battery/reading", json=reading(v=3.89, i=0.0001, ts=past(20), seq=1, boot_id="c")).json()
    assert out["cell_state"] == "unverified" and "cell_unverified" in out["flags"]
    pts = client.get("/api/battery/HW-1/history").json()["points"]
    assert pts[0]["voltage_v"] is None and pts[0]["voltage_raw_v"] == 3.89

    assert client.post("/api/battery/HW-1/presence", json={"confirmed": True, "note": "meter 3.888 V"}).status_code == 200
    out = client.post("/api/battery/reading", json=reading(v=3.888, i=0.0, ts=past(10), seq=2, boot_id="c")).json()
    assert out["cell_state"] == "confirmed"
    b = client.get("/api/battery/HW-1").json()
    assert b["presence_evidence"] == "operator" and b["presence_note"] == "meter 3.888 V"
    assert client.get("/api/battery/HW-1/history").json()["points"][-1]["voltage_v"] == 3.888


def test_unverified_low_voltage_raises_no_alert_but_confirmed_does(client):
    client.post("/api/battery/reading", json=reading(v=2.9, i=0.0, ts=past(20), seq=1, boot_id="u"))
    assert "voltage_low" not in active(client)
    client.post("/api/battery/HW-1/presence", json={"confirmed": True})
    client.post("/api/battery/reading", json=reading(v=2.9, i=0.0, ts=past(10), seq=2, boot_id="u"))
    a = active(client)["voltage_low"]
    assert a["severity"] == "critical" and a["threshold"] == 3.0     # threshold unchanged


def test_load_current_verifies_presence_and_keeps_voltage_alarms(client):
    rows = [reading(v=3.80 - 0.05 * k, i=0.37, ts=past(40 - 2 * k), seq=k, boot_id="d") for k in range(4)]
    states = [client.post("/api/battery/reading", json=r).json()["cell_state"] for r in rows]
    assert states[0] == "unverified"                       # one sample is not "sustained"
    assert states[1:] == ["verified_current"] * 3
    b = client.get("/api/battery/HW-1").json()
    assert b["presence_evidence"] == "current"
    # load removed: presence persists while the voltage stays in range
    out = client.post("/api/battery/reading", json=reading(v=3.20, i=0.0, ts=past(30), seq=10, boot_id="d")).json()
    assert out["cell_state"] == "verified_current"
    assert active(client)["voltage_low"]["severity"] == "warning"     # 3.20 <= 3.2 warn


def test_over_discharged_cell_under_load_still_alarms(client):
    """Current proves a circuit even below the plausibility window: a cell pulled
    to 1.9 V under load must raise the critical alert, not be hidden as 'no cell'."""
    for k in range(3):
        out = client.post("/api/battery/reading", json=reading(v=1.9, i=0.30, ts=past(20 - k), seq=k, boot_id="o")).json()
    assert out["cell_state"] == "verified_current"
    assert active(client)["voltage_low"]["severity"] == "critical"


def test_verified_cell_dropping_out_of_range_raises_signal_lost(client):
    client.post("/api/battery/reading", json=reading(v=3.7, ts=past(30), seq=1, boot_id="l"))
    client.post("/api/battery/HW-1/presence", json={"confirmed": True})
    client.post("/api/battery/reading", json=reading(v=3.7, ts=past(20), seq=2, boot_id="l"))
    out = client.post("/api/battery/reading", json=legacy_fw(0.88, ts=past(10), seq=3, boot_id="l")).json()
    assert out["cell_state"] == "no_cell"
    a = active(client)
    assert a["cell_signal_lost"]["severity"] == "warning" and "voltage_low" not in a
    assert client.get("/api/battery/HW-1").json()["presence_evidence"] is None
    # a plausible reading afterwards is unverified again, not silently "confirmed"
    out = client.post("/api/battery/reading", json=reading(v=3.7, ts=past(5), seq=4, boot_id="l")).json()
    assert out["cell_state"] == "unverified"


def test_presence_expires_after_gap(settings):
    s = dataclasses.replace(settings, presence_gap_s=60)
    p = Presence("operator", "x", 0, None)
    state, p2, events = classify({"ts_ms": 61_000, "voltage_v": 3.9, "current_a": 0.0}, None, p, s)
    assert state == "unverified" and "presence_expired" in events and not p2.active


def test_withdrawing_confirmation(client):
    client.post("/api/battery/reading", json=reading(v=3.9, ts=past(20), seq=1, boot_id="w"))
    client.post("/api/battery/HW-1/presence", json={"confirmed": True})
    client.post("/api/battery/HW-1/presence", json={"confirmed": False, "note": "cell removed"})
    out = client.post("/api/battery/reading", json=reading(v=3.9, ts=past(10), seq=2, boot_id="w")).json()
    assert out["cell_state"] == "unverified"


def test_ina219_fault_is_unknown_not_no_cell(client):
    out = client.post("/api/battery/reading", json=reading(sensor_status={"ina219": "not_found", "ds18b20": "ok"})).json()
    assert out["cell_state"] == "unknown" and "battery_not_detected" not in out["flags"]


def test_temperature_telemetry_unaffected_by_floating_voltage(client):
    client.post("/api/battery/reading", json=legacy_fw(0.88))
    latest = client.get("/api/battery/HW-1/latest").json()["reading"]
    assert latest["temperature_c"] == 25.0 and latest["cell_state"] == "no_cell"


def test_safety_thresholds_unchanged(client):
    t = client.get("/api/settings/thresholds").json()
    assert (t["voltage_low_warn_v"], t["voltage_low_crit_v"], t["voltage_high_warn_v"], t["voltage_high_crit_v"]) == \
        (3.2, 3.0, 4.2, 4.25)


# ---------------------------------------------------------------- history repair
def _legacy_db(path, readings, alerts):
    """Build a schema-v3 database exactly as the previous release wrote it."""
    con = sqlite3.connect(path)
    for sql in (_SCHEMA_V1, _SCHEMA_V2, _SCHEMA_V3):
        con.executescript(sql)
    for v in (1, 2, 3):
        con.execute("INSERT INTO schema_version(version) VALUES (?)", (v,))
    con.execute("INSERT INTO batteries(battery_id, source, created_at) VALUES ('HW-1','hardware','2026-11-17T00:00:00Z')")
    for k, (rx, v, i, detected) in enumerate(readings):
        ts_ms = int(datetime.fromisoformat(rx.replace("Z", "+00:00")).timestamp() * 1000)
        con.execute("INSERT INTO readings(battery_id, device_id, source, ts, ts_ms, timestamp_source, received_at, "
                    "voltage_v, current_a, temperature_c, seq, boot_id, battery_detected, flags) "
                    "VALUES ('HW-1','esp32','hardware',?,?,'bridge',?,?,?,25.0,?,'old',?,'[]')",
                    (rx, ts_ms, rx, v, i, k, detected))
    for typ, sev, status, first, last in alerts:
        con.execute("INSERT INTO alerts(battery_id, device_id, type, severity, message, value, threshold, unit, status, "
                    "first_seen, last_seen, occurrences) VALUES ('HW-1','esp32',?,?,'Cell voltage 1.140 V <= 3 V.',"
                    "1.14,3.0,'V',?,?,?,1)", (typ, sev, status, first, last))
    con.commit()
    con.close()


def test_false_alarms_from_old_release_are_reclassified_not_deleted(tmp_path, settings):
    t0 = datetime.now(timezone.utc) - timedelta(hours=1)
    rx = [iso(t0 + timedelta(seconds=2 * k)) for k in range(6)]
    # what the old code stored: 1.14 V marked "detected" and a CRITICAL low-voltage alert
    readings = [(rx[0], 0.72, -0.0002, 0), (rx[1], 0.88, 0.0001, 0), (rx[2], 1.14, -0.0005, 1),
                (rx[3], 1.14, -0.0002, 1), (rx[4], 0.88, -0.0001, 0), (rx[5], 1.02, 0.0, 1)]
    alerts = [("voltage_low", "critical", "active", rx[2], rx[5]),
              ("voltage_low", "critical", "resolved", rx[2], rx[3])]
    path = tmp_path / "legacy.db"
    _legacy_db(path, readings, alerts)

    c = TestClient(create_app(dataclasses.replace(settings, db_path=path)))
    al = [a for a in c.get("/api/alerts?battery_id=HW-1").json()["alerts"] if a["type"] == "voltage_low"]
    assert len(al) == 2                                            # nothing deleted
    for a in al:
        assert a["classification"] == "invalid_measurement" and a["status"] == "resolved"
        assert "Original record kept" in a["resolution_note"]
        assert a["severity"] == "critical" and a["message"].startswith("Cell voltage 1.140")  # original content intact
    pts = c.get("/api/battery/HW-1/history").json()["points"]
    assert len(pts) == 6 and {p["cell_state"] for p in pts} == {"no_cell"}
    assert all(p["voltage_v"] is None for p in pts)

    # idempotent: a restart changes nothing
    c2 = TestClient(create_app(dataclasses.replace(settings, db_path=path)))
    al2 = [a for a in c2.get("/api/alerts?battery_id=HW-1").json()["alerts"] if a["type"] == "voltage_low"]
    assert [(a["id"], a["status"], a["resolved_at"]) for a in al2] == [(a["id"], a["status"], a["resolved_at"]) for a in al]


def test_genuine_historical_alarm_is_kept_valid(tmp_path, settings):
    t0 = datetime.now(timezone.utc) - timedelta(hours=1)
    rx = [iso(t0 + timedelta(seconds=2 * k)) for k in range(4)]
    # a real discharge: sustained 0.37 A at 3.1-2.95 V
    readings = [(rx[k], 3.10 - 0.05 * k, 0.37, 1) for k in range(4)]
    path = tmp_path / "legacy2.db"
    _legacy_db(path, readings, [("voltage_low", "critical", "active", rx[2], rx[3])])
    c = TestClient(create_app(dataclasses.replace(settings, db_path=path)))
    a = [a for a in c.get("/api/alerts?battery_id=HW-1").json()["alerts"] if a["type"] == "voltage_low"][0]
    assert a["classification"] == "valid" and a["status"] == "active"


def test_usb_bridge_floating_line_end_to_end(client):
    from iot.bridge.serial_bridge import Bridge
    line = ('@BHM ' + json.dumps({"battery_id": "HW-1", "device_id": "esp32", "source": "hardware",
                                  "voltage_v": 1.14, "current_a": -0.0005, "power_w": 0.0, "temperature_c": 26.5,
                                  "seq": 1, "boot_id": "u1", "sensor_status": {"ina219": "ok", "ds18b20": "ok",
                                                                               "battery_connected": None}}))

    def post(path, body):
        r = client.post(path, json=body)
        return r.status_code, r.json()
    b = Bridge(post, log=lambda _: None)
    b.handle_line(line)
    assert b.sent == 1
    assert client.get("/api/battery/HW-1/latest").json()["reading"]["cell_state"] == "no_cell"
    assert "voltage_low" not in active(client)
