"""Multiple physical batteries on one ESP32: profiles, switching, isolation,
capacity handling, session boundaries and the v5 -> v6 migration."""

from __future__ import annotations

import dataclasses
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from iot.backend.api import create_app
from iot.backend.db import _SCHEMA_V1, _SCHEMA_V2, _SCHEMA_V3, _SCHEMA_V4, _SCHEMA_V5

from .conftest import iso, reading

DEV = "esp32-devkit-01"
FW_ID = "CELL-18650-01"          # what the deployed firmware's config.h sends


def fw(ts, v=3.9, i=0.0, t=25.0, seq=None, boot="b", **kw):
    """A reading exactly as the ESP32 sends it: fixed firmware battery_id."""
    return reading(battery=FW_ID, device_id=DEV, ts=ts, v=v, i=i, t=t, seq=seq, boot_id=boot, **kw)


def assign(client, battery_id, note=None, at=None):
    """Select the connected battery. Through the API it takes effect at server 'now';
    tests that replay readings from the past make it effective at ``at`` instead
    (same code path, explicit time)."""
    if at is None:
        r = client.put(f"/api/devices/{DEV}/assignment", json={"battery_id": battery_id, "note": note})
        assert r.status_code == 200, r.text
        return r.json()
    from iot.backend.assignments import switch_assignment
    app = client.app
    with app.state.db.write() as conn:
        return switch_assignment(conn, DEV, battery_id, note, app.state.settings,
                                 now_ms=int(at.timestamp() * 1000))


def stored_count(client, battery_id):
    r = client.get(f"/api/battery/{battery_id}/history")
    return r.json()["total"] if r.status_code == 200 else 0


def now_minus(s):
    return datetime.now(timezone.utc) - timedelta(seconds=s)


# ---------------------------------------------------------------- profiles
def test_three_profiles_exist_with_their_own_rated_capacity(client):
    cmp = {b["battery_id"]: b for b in client.get("/api/batteries/compare").json()["batteries"]}
    assert set(cmp) == {"BAT-A-3000", "BAT-B-3000", "BAT-C-2500"}
    assert (cmp["BAT-A-3000"]["rated_capacity_mah"], cmp["BAT-B-3000"]["rated_capacity_mah"],
            cmp["BAT-C-2500"]["rated_capacity_mah"]) == (3000, 3000, 2500)
    assert all(b["managed"] and b["soh"] == "unavailable" for b in cmp.values())


# ---------------------------------------------------------------- routing / isolation
def test_readings_follow_the_selected_battery(client):
    assign(client, "BAT-A-3000", at=now_minus(60))
    for k in range(3):
        out = client.post("/api/battery/reading", json=fw(now_minus(30 - k), seq=k)).json()
        assert out["battery_id"] == "BAT-A-3000" and out["routed_by"] == "assignment"
    assign(client, "BAT-C-2500", at=now_minus(10))
    for k in range(2):
        out = client.post("/api/battery/reading", json=fw(now_minus(5 - k), seq=10 + k)).json()
        assert out["battery_id"] == "BAT-C-2500"
    assert (stored_count(client, "BAT-A-3000"), stored_count(client, "BAT-C-2500"),
            stored_count(client, "BAT-B-3000")) == (3, 2, 0)
    latest = client.get("/api/battery/BAT-C-2500/latest").json()["reading"]
    assert latest["reported_battery_id"] == FW_ID          # firmware's claim kept for audit
    assert stored_count(client, FW_ID) == 0                 # nothing leaked to the firmware ID


def test_no_selection_never_assigns_to_a_profile(client):
    # firmware configured with a profile ID, but nobody selected it as connected
    out = client.post("/api/battery/reading",
                      json=reading(battery="BAT-A-3000", device_id=DEV, ts=now_minus(5))).json()
    assert out["battery_id"] == f"UNASSIGNED-{DEV}" and out["routed_by"] == "unassigned"
    assert stored_count(client, "BAT-A-3000") == 0


def test_after_unassigning_readings_go_to_unassigned_bucket(client):
    assign(client, "BAT-B-3000", at=now_minus(60))
    client.post("/api/battery/reading", json=fw(now_minus(20), seq=1))
    assign(client, None, at=now_minus(5))
    out = client.post("/api/battery/reading", json=fw(now_minus(1), seq=2)).json()
    assert out["battery_id"] == f"UNASSIGNED-{DEV}"
    assert stored_count(client, "BAT-B-3000") == 1


def test_buffered_reading_measured_before_switch_stays_with_old_battery(client):
    assign(client, "BAT-A-3000", at=now_minus(60))
    measured_on_a = now_minus(30)
    assign(client, "BAT-B-3000", at=now_minus(10))                       # switch happens after that measurement
    out = client.post("/api/battery/reading", json=fw(measured_on_a, seq=1)).json()   # delivered late
    assert out["battery_id"] == "BAT-A-3000"


def test_unmanaged_device_keeps_original_behaviour(client):
    # simulator / legacy device that was never assigned: firmware battery_id is used, as before
    out = client.post("/api/battery/reading",
                      json=reading(battery="SIM-1", device_id="sim", ts=now_minus(3), source="simulation")).json()
    assert out["battery_id"] == "SIM-1" and out["routed_by"] == "firmware"


def test_one_cell_cannot_be_selected_on_two_devices(client):
    assign(client, "BAT-A-3000")
    r = client.put("/api/devices/other-esp32/assignment", json={"battery_id": "BAT-A-3000"})
    assert r.status_code == 409
    assert client.put("/api/devices/x/assignment", json={"battery_id": "NOPE"}).status_code == 404
    client.post("/api/battery/reading", json=reading(battery="HW-9", device_id="hw9", ts=now_minus(3)))
    assert client.put("/api/devices/x/assignment", json={"battery_id": "HW-9"}).status_code == 400


def test_alerts_are_stored_under_the_connected_battery(client):
    assign(client, "BAT-A-3000", at=now_minus(60))
    client.post("/api/battery/reading", json=fw(now_minus(20), t=57.0, seq=1))
    assign(client, "BAT-B-3000", at=now_minus(10))
    client.post("/api/battery/reading", json=fw(now_minus(5), t=25.0, seq=2))
    a = [x for x in client.get("/api/alerts?status=active").json()["alerts"] if x["type"] == "temperature_high"]
    assert [x["battery_id"] for x in a] == ["BAT-A-3000"]
    assert not [x for x in client.get("/api/alerts?battery_id=BAT-B-3000").json()["alerts"]
                if x["type"] == "temperature_high"]


# ---------------------------------------------------------------- presence vs selection
def test_selection_is_not_presence_and_switch_clears_presence(client):
    # confirming a profile that is not selected is refused
    r = client.post("/api/battery/BAT-A-3000/presence", json={"confirmed": True})
    assert r.status_code == 409 and "Select it first" in r.json()["detail"]
    assign(client, "BAT-A-3000", at=now_minus(60))
    out = client.post("/api/battery/reading", json=fw(now_minus(30), v=3.9, seq=1)).json()
    assert out["cell_state"] == "unverified"                # selected, but not proven connected
    assert client.post("/api/battery/BAT-A-3000/presence", json={"confirmed": True}).status_code == 200
    assert client.post("/api/battery/reading", json=fw(now_minus(20), v=3.9, seq=2)).json()["cell_state"] == "confirmed"
    assign(client, "BAT-B-3000", at=now_minus(15))
    assert client.get("/api/battery/BAT-A-3000").json()["presence_evidence"] is None
    out = client.post("/api/battery/reading", json=fw(now_minus(10), v=3.9, seq=3)).json()
    assert out["battery_id"] == "BAT-B-3000" and out["cell_state"] == "unverified"


# ---------------------------------------------------------------- sessions across a switch
def test_switch_ends_session_and_new_battery_starts_from_zero(client):
    t0 = datetime.now(timezone.utc) - timedelta(seconds=120)
    assign(client, "BAT-A-3000", at=t0 - timedelta(seconds=5))
    for k in range(21):                                        # 40 s at 0.37 A on A
        client.post("/api/battery/reading", json=fw(t0 + timedelta(seconds=2 * k), v=3.8, i=0.37, seq=k))
    a_active = client.get("/api/battery/BAT-A-3000").json()["active_session"]
    assert a_active and a_active["charge_mah"] == pytest.approx(0.37 * 40 / 3.6)
    res = assign(client, "BAT-B-3000")
    assert res["ended"]["session_id"] == a_active["id"]
    a = client.get("/api/battery/BAT-A-3000/sessions").json()["sessions"][0]
    assert a["status"] == "ended" and a["end_reason"] == "battery_switched"
    assert a["charge_mah"] == pytest.approx(0.37 * 40 / 3.6)        # preserved, not extended
    # B: first on-reading alone does not start a session; nothing of A carries over
    t1 = datetime.now(timezone.utc)
    client.post("/api/battery/reading", json=fw(t1, v=3.7, i=0.5, seq=100))
    assert client.get("/api/battery/BAT-B-3000").json()["active_session"] is None
    client.post("/api/battery/reading", json=fw(t1 + timedelta(seconds=2), v=3.7, i=0.5, seq=101))
    b = client.get("/api/battery/BAT-B-3000").json()["active_session"]
    assert b["charge_mah"] == pytest.approx(0.5 * 2 / 3.6)


def test_quick_switch_back_does_not_bridge_the_other_battery(client):
    assign(client, "BAT-A-3000", at=now_minus(30))
    t = datetime.now(timezone.utc) - timedelta(seconds=10)
    client.post("/api/battery/reading", json=fw(t, i=0.4, seq=1))
    assign(client, "BAT-B-3000")
    assign(client, "BAT-A-3000")                    # back to A within seconds
    client.post("/api/battery/reading", json=fw(datetime.now(timezone.utc), i=0.4, seq=2))
    # A's pre-switch reading must not pair with the new one: no session spanning the swap
    assert client.get("/api/battery/BAT-A-3000").json()["active_session"] is None


def test_switch_closes_open_cycle_segment_as_incomplete(client):
    t0 = datetime.now(timezone.utc) - timedelta(seconds=100)
    assign(client, "BAT-A-3000", at=t0 - timedelta(seconds=5))
    for k in range(40):
        client.post("/api/battery/reading", json=fw(t0 + timedelta(seconds=2 * k), v=3.9 - 0.01 * k, i=0.37, seq=k))
    assign(client, "BAT-C-2500")
    cyc = client.get("/api/battery/BAT-A-3000/cycles").json()["cycles"]
    assert len(cyc) == 1 and cyc[0]["complete"] == 0
    assert any("battery switched" in n for n in cyc[0]["completeness_notes"])


# ---------------------------------------------------------------- capacity / cycles
def _full_cycle(client, start, seq0, rated_label):
    """Rest at 4.18 V, then a discharge to 2.98 V and rest: a completed experimental cycle."""
    seq = seq0
    for k in range(5):
        client.post("/api/battery/reading", json=fw(start + timedelta(seconds=2 * k), v=4.18, i=0.0, seq=seq)); seq += 1
    t = start + timedelta(seconds=10)
    for k in range(121):                               # 600 s at 0.5 A -> 83.33 mAh
        v = 4.10 - (4.10 - 2.98) * k / 120
        client.post("/api/battery/reading", json=fw(t + timedelta(seconds=5 * k), v=v, i=0.5, seq=seq)); seq += 1
    t2 = t + timedelta(seconds=605)
    for k in range(10):
        client.post("/api/battery/reading", json=fw(t2 + timedelta(seconds=2 * k), v=3.3, i=0.0, seq=seq)); seq += 1
    return seq


def test_efc_uses_each_batterys_own_rating_and_short_sessions_are_not_cycles(client):
    base = datetime.now(timezone.utc) - timedelta(hours=2)
    assign(client, "BAT-C-2500", note="2500 mAh cell", at=base - timedelta(seconds=5))
    # one short partial session: 60 s at 0.5 A = 8.33 mAh -> not a cycle
    for k in range(31):
        client.post("/api/battery/reading", json=fw(base + timedelta(seconds=2 * k), v=3.9, i=0.5, seq=k))
    for k in range(10):
        client.post("/api/battery/reading", json=fw(base + timedelta(seconds=70 + 2 * k), v=3.95, i=0.0, seq=50 + k))
    st = client.get("/api/battery/BAT-C-2500/stats").json()["stats"]
    assert st["completed_cycles"] == 0 and st["n_partial_sessions"] == 1
    assert st["equivalent_full_cycles"] == pytest.approx((0.5 * 60 / 3.6) / 2500)
    # a completed experimental cycle on C
    _full_cycle(client, base + timedelta(minutes=10), 1000, 2500)
    st = client.get("/api/battery/BAT-C-2500/stats").json()["stats"]
    assert st["completed_cycles"] == 1 and st["rated_capacity_mah"] == 2500
    cap = st["capacity_measurements"][0]["charge_mah"]
    assert cap == pytest.approx(0.5 * 600 / 3.6)
    assert st["equivalent_full_cycles"] == pytest.approx((0.5 * 60 / 3.6 + cap) / 2500)
    # same measured charge on a 3000 mAh battery gives a different EFC - its own rating
    assign(client, "BAT-A-3000", at=base + timedelta(minutes=39))
    _full_cycle(client, base + timedelta(minutes=40), 5000, 3000)
    a = client.get("/api/battery/BAT-A-3000/stats").json()["stats"]
    assert a["equivalent_full_cycles"] == pytest.approx(cap / 3000)
    # C's statistics are untouched by A's measurements
    assert client.get("/api/battery/BAT-C-2500/stats").json()["stats"]["completed_cycles"] == 1


def test_soh_rul_unavailable_for_physical_profiles(client):
    client.put("/api/battery/BAT-A-3000", json={"cycle_count_offset": 20, "cutoff_voltage_v": 2.7})
    # NASA-like complete discharge that would pass the model gate on an unmanaged battery
    t0 = datetime.now(timezone.utc) - timedelta(minutes=90)
    assign(client, "BAT-A-3000", at=t0 - timedelta(seconds=5))
    rows = []
    for k in range(120):
        f = k / 119
        rows.append(fw(t0 + timedelta(seconds=20 * k), v=4.19 + (2.7 - 4.19) * f, i=1.9, t=24.5 + 14.5 * f, seq=k))
    for k in range(8):
        rows.append(fw(t0 + timedelta(seconds=20 * (120 + k)), v=3.0, i=0.0, t=39.0, seq=200 + k))
    client.post("/api/battery/readings", json={"readings": rows})
    client.post("/api/battery/BAT-A-3000/cycles/aggregate")
    preds = client.get("/api/battery/BAT-A-3000/predictions").json()["predictions"]
    assert preds and all(p["status"] == "unavailable" and p["soh_pct"] is None for p in preds)
    assert "not been validated on these cells" in preds[-1]["reasons"][0]
    assert client.get("/api/battery/BAT-A-3000").json()["latest_valid_prediction"] is None


# ---------------------------------------------------------------- migration
def test_v5_database_migrates_and_keeps_history_unchanged(tmp_path, settings):
    path = tmp_path / "v5.db"
    con = sqlite3.connect(path)
    for sql in (_SCHEMA_V1, _SCHEMA_V2, _SCHEMA_V3, _SCHEMA_V4, _SCHEMA_V5):
        con.executescript(sql)
    for v in range(1, 6):
        con.execute("INSERT INTO schema_version(version) VALUES (?)", (v,))
    con.execute("INSERT INTO batteries(battery_id, source, created_at, rated_capacity_ah) "
                "VALUES ('CELL-18650-01','hardware','2026-11-17T00:00:00Z', 3.0)")
    t0 = datetime.now(timezone.utc) - timedelta(hours=1)
    for k in range(20):
        ts = t0 + timedelta(seconds=2 * k)
        con.execute("INSERT INTO readings(battery_id, device_id, source, ts, ts_ms, timestamp_source, received_at, "
                    "voltage_v, current_a, temperature_c, seq, boot_id, flags, cell_state) "
                    "VALUES ('CELL-18650-01',?,'hardware',?,?,'bridge',?,0.88,-0.0002,25.0,?,'old','[]','no_cell')",
                    (DEV, iso(ts), int(ts.timestamp() * 1000), iso(ts), k))
    con.execute("INSERT INTO discharge_sessions(battery_id, status, start_ts, start_ms, last_ms, charge_mah, created_at) "
                "VALUES ('CELL-18650-01','ended','2026-11-18T00:00:00Z', 1, 2, 12.5, '2026-11-18T00:00:00Z')")
    con.commit(); con.close()

    c = TestClient(create_app(dataclasses.replace(settings, db_path=path)))
    con = sqlite3.connect(path)
    assert con.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == 6
    # history intact and NOT re-attributed to any profile
    assert c.get("/api/battery/CELL-18650-01/history").json()["total"] == 20
    assert con.execute("SELECT COUNT(*) FROM readings WHERE battery_id LIKE 'BAT-%'").fetchone()[0] == 0
    assert c.get("/api/battery/CELL-18650-01/sessions").json()["sessions"][0]["charge_mah"] == 12.5
    legacy = c.get("/api/battery/CELL-18650-01").json()
    assert legacy["managed"] == 0
    cmp = {b["battery_id"] for b in c.get("/api/batteries/compare").json()["batteries"]}
    assert cmp == {"BAT-A-3000", "BAT-B-3000", "BAT-C-2500"}
    # the device keeps working: unassigned, it still records under its firmware ID
    out = c.post("/api/battery/reading", json=fw(datetime.now(timezone.utc), seq=99, boot="new")).json()
    assert out["battery_id"] == FW_ID and out["routed_by"] == "firmware"
    # restart is idempotent (no duplicate profiles)
    TestClient(create_app(dataclasses.replace(settings, db_path=path)))
    assert sqlite3.connect(path).execute("SELECT COUNT(*) FROM batteries WHERE managed=1").fetchone()[0] == 3
