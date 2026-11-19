"""Battery Test Manager: state transitions, battery identity, incomplete tests,
telemetry gaps, validity rules and data preservation."""

from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta, timezone

import pytest

from iot.backend import battery_tests as bt
from iot.backend.assignments import switch_assignment

from .conftest import reading

DEV = "esp32-devkit-01"
GOOD = {"battery_identity_confirmed": True, "load_rating_verified": True, "supervisor_approval": True,
        "disconnect_procedure_confirmed": True, "voltage_cutoff_confirmed": True, "supervisor_name": "Dr. Rao",
        "cutoff_v": 3.0, "load_resistance_ohm": 10.0, "load_power_rating_w": 5.0}


def ms(t: datetime) -> int:
    return int(t.timestamp() * 1000)


def assign(client, battery, at):
    app = client.app
    with app.state.db.write() as conn:
        switch_assignment(conn, DEV, battery, None, app.state.settings, now_ms=ms(at))


def act(client, uid, action, at, reason=None):
    app = client.app
    with app.state.db.write() as conn:
        return bt.transition(conn, uid, action, reason, app.state.settings, now_ms=ms(at))


def ready_test(client, battery="BAT-A-3000", at=None):
    at = at or datetime.now(timezone.utc) - timedelta(hours=2)
    assign(client, battery, at)
    uid = client.post("/api/tests", json={"battery_id": battery, "notes": "unit"}).json()["test_uid"]
    r = client.put(f"/api/tests/{uid}/checklist", json=GOOD).json()
    assert r["state"] == "ready", r
    return uid, at


class Bench:
    def __init__(self, client):
        self.c, self.seq = client, 0

    def rows(self, specs):
        out = []
        for t, v, i, temp in specs:
            self.seq += 1
            out.append(reading(battery="CELL-18650-01", device_id=DEV, ts=t, v=v, i=i, t=temp, seq=self.seq, boot_id="tm"))
        for k in range(0, len(out), 500):
            assert self.c.post("/api/battery/readings", json={"readings": out[k:k + 500]}).json()["rejected"] == 0

    def discharge(self, start, i=0.5, dur=600, end_v=2.98, rest_v=4.18, temp=25.0, skip=None, temp_none=()):
        spec = [(start - timedelta(seconds=10 - 2 * k), rest_v, 0.0, temp) for k in range(5)]      # rest before start
        n = dur // 5
        for k in range(n + 1):
            sec = 5 * k
            if skip and skip[0] <= sec < skip[1]:
                continue
            spec.append((start + timedelta(seconds=10 + sec), 4.10 - (4.10 - end_v) * k / n, i,
                         None if k in temp_none else temp))
        last = start + timedelta(seconds=10 + 5 * n)
        spec += [(last + timedelta(seconds=2 + 2 * k), 3.4, 0.0, temp) for k in range(5)]
        self.rows(spec)
        return last + timedelta(seconds=12)


def view(client, uid):
    return client.get(f"/api/tests/{uid}").json()


# ---------------------------------------------------------------- states
def test_state_machine_and_software_only_responses(client):
    uid = client.post("/api/tests", json={"battery_id": "BAT-A-3000"}).json()["test_uid"]
    assert uid.startswith("T-") and "BAT-A-3000" in uid
    assert view(client, uid)["state"] == "not_started"
    assert client.post(f"/api/tests/{uid}/start").status_code == 409                # checklist missing
    # a too-small load rating is a checklist problem, not a pass
    bad = dict(GOOD, load_power_rating_w=1.0)
    r = client.put(f"/api/tests/{uid}/checklist", json=bad).json()
    assert r["state"] == "not_started" and any("dissipates up to 1.76 W" in p for p in r["checklist_problems"])
    # complete checklist but the battery is not selected as connected: still not ready
    r = client.put(f"/api/tests/{uid}/checklist", json=GOOD).json()
    assert r["state"] == "not_started" and any("not selected" in p for p in r["checklist_problems"])
    assign(client, "BAT-A-3000", datetime.now(timezone.utc) - timedelta(seconds=5))
    assert client.put(f"/api/tests/{uid}/checklist", json=GOOD).json()["state"] == "ready"
    assert client.post(f"/api/tests/{uid}/pause").status_code == 409                 # not running yet
    started = client.post(f"/api/tests/{uid}/start").json()
    assert started["state"] == "running" and "did not connect, disconnect" in started["hardware_notice"]
    assert "warning" not in started
    paused = client.post(f"/api/tests/{uid}/pause").json()
    assert paused["state"] == "paused" and "Physically disconnect" in paused["warning"]
    assert client.post(f"/api/tests/{uid}/resume").json()["state"] == "running"
    done = client.post(f"/api/tests/{uid}/complete").json()
    assert done["state"] == "completed" and "Physically disconnect" in done["warning"]
    assert client.post(f"/api/tests/{uid}/complete").status_code == 409
    assert client.post(f"/api/tests/{uid}/abort").status_code == 409
    assert client.put(f"/api/tests/{uid}/checklist", json=GOOD).status_code == 409   # locked after start
    states = [e["state"] for e in view(client, uid)["history"]]
    assert states == ["not_started", "ready", "running", "paused", "running", "completed"]


def test_abort_before_start_is_kept_with_reason(client):
    uid = client.post("/api/tests", json={"battery_id": "BAT-B-3000"}).json()["test_uid"]
    r = client.post(f"/api/tests/{uid}/abort", json={"reason": "cell label unreadable"}).json()
    assert r["state"] == "aborted"
    v = view(client, uid)
    assert v["abort_reason"] == "cell label unreadable" and v["validity"]["valid_for_full_capacity"] is False
    assert uid in [t["test_uid"] for t in client.get("/api/tests").json()["tests"]]


def test_cutoff_outside_range_is_rejected_as_checklist_problem(client):
    uid = client.post("/api/tests", json={"battery_id": "BAT-C-2500"}).json()["test_uid"]
    r = client.put(f"/api/tests/{uid}/checklist", json=dict(GOOD, cutoff_v=2.0)).json()
    assert any("outside 2.5-3.3 V" in p for p in r["checklist_problems"])


# ---------------------------------------------------------------- identity
def test_battery_identity_rules(client):
    assert client.post("/api/tests", json={"battery_id": "NOPE"}).status_code == 404
    client.post("/api/battery/reading", json=reading(battery="SIM-1", device_id="sim", source="simulation"))
    assert client.post("/api/tests", json={"battery_id": "SIM-1"}).status_code == 400
    uid, at = ready_test(client, "BAT-A-3000")
    assign(client, "BAT-B-3000", at + timedelta(minutes=1))        # a different cell put on the device
    with pytest.raises(Exception) as e:
        act(client, uid, "start", at + timedelta(minutes=2))
    assert "no longer selected" in str(e.value.detail)


def test_only_one_active_test_per_battery_or_device(client):
    uid1, at = ready_test(client, "BAT-A-3000")
    uid2 = client.post("/api/tests", json={"battery_id": "BAT-A-3000"}).json()["test_uid"]
    client.put(f"/api/tests/{uid2}/checklist", json=GOOD)
    assert uid1 != uid2
    act(client, uid1, "start", at + timedelta(minutes=1))
    with pytest.raises(Exception) as e:
        act(client, uid2, "start", at + timedelta(minutes=2))
    assert "already active" in str(e.value.detail)


# ---------------------------------------------------------------- results + validity
def test_valid_full_discharge_records_all_results(client):
    uid, at = ready_test(client)
    start = at + timedelta(minutes=5)
    act(client, uid, "start", start)
    end = Bench(client).discharge(start)
    act(client, uid, "complete", end)
    v = view(client, uid)
    r = v["results"]
    assert v["results_frozen"] and v["validity"]["valid_for_full_capacity"] is True
    assert v["validity"]["classification"].startswith("full discharge")
    assert r["delivered_mah"] == pytest.approx(0.5 * 600 / 3.6)
    assert r["delivered_wh"] == pytest.approx(0.5 * (4.10 + 2.98) / 2 * 600 / 3600, rel=0.01)
    assert r["initial_voltage_v"] == 4.18 and r["final_voltage_v"] == 3.4 and r["min_loaded_voltage_v"] == pytest.approx(2.98)
    assert r["mean_current_a"] == pytest.approx(0.5) and r["peak_current_a"] == 0.5
    assert r["max_temperature_c"] == 25.0 and r["elapsed_s"] == pytest.approx((end - start).total_seconds())
    assert r["gaps"] == [] and v["soh"] == "unavailable" and v["rul"] == "unavailable"


def test_partial_discharge_is_never_a_full_cycle(client):
    uid, at = ready_test(client)
    start = at + timedelta(minutes=5)
    act(client, uid, "start", start)
    end = Bench(client).discharge(start, end_v=3.6)
    act(client, uid, "complete", end)
    v = view(client, uid)
    assert v["state"] == "completed" and v["validity"]["valid_for_full_capacity"] is False
    assert v["validity"]["classification"] == "partial discharge (not a full cycle)"
    failed = {c["check"] for c in v["validity"]["checks"] if not c["ok"]}
    assert failed == {"reached cutoff"}


def test_not_from_full_charge_is_invalid(client):
    uid, at = ready_test(client)
    start = at + timedelta(minutes=5)
    act(client, uid, "start", start)
    end = Bench(client).discharge(start, rest_v=3.95)
    act(client, uid, "complete", end)
    failed = {c["check"] for c in view(client, uid)["validity"]["checks"] if not c["ok"]}
    assert failed == {"started from rested full charge"}


def test_telemetry_gap_is_marked_and_not_filled(client):
    uid, at = ready_test(client)
    start = at + timedelta(minutes=5)
    act(client, uid, "start", start)
    end = Bench(client).discharge(start, skip=(200, 300))           # 100 s without telemetry
    act(client, uid, "complete", end)
    v = view(client, uid)
    r = v["results"]
    assert len(r["gaps"]) == 1 and r["gaps"][0]["duration_s"] == pytest.approx(105)
    assert r["delivered_mah"] == pytest.approx(0.5 * (600 - 105) / 3.6)
    assert v["validity"]["valid_for_full_capacity"] is False
    assert v["validity"]["classification"] == "invalid full discharge"


def test_missing_measurements_are_counted(client):
    uid, at = ready_test(client)
    start = at + timedelta(minutes=5)
    act(client, uid, "start", start)
    end = Bench(client).discharge(start, temp_none=set(range(10, 20)))  # DS18B20 dropouts
    act(client, uid, "complete", end)
    v = view(client, uid)
    assert v["results"]["missing_measurements"]["temperature_c"] == 10
    chk = next(c for c in v["validity"]["checks"] if c["check"] == "measurements complete")
    assert chk["ok"] is False


def test_pause_with_load_still_connected_is_flagged(client):
    uid, at = ready_test(client)
    start = at + timedelta(minutes=5)
    act(client, uid, "start", start)
    act(client, uid, "pause", start + timedelta(seconds=310))       # software pause - load stays on physically
    act(client, uid, "resume", start + timedelta(seconds=410))
    end = Bench(client).discharge(start)
    act(client, uid, "complete", end)
    r = view(client, uid)["results"]
    assert r["paused"] and r["load_current_while_paused_readings"] > 0
    assert r["delivered_mah"] < 0.5 * 600 / 3.6                    # paused interval not counted
    assert not view(client, uid)["validity"]["valid_for_full_capacity"]


def test_battery_switch_during_test_interrupts_it(client):
    uid, at = ready_test(client)
    start = at + timedelta(minutes=5)
    act(client, uid, "start", start)
    Bench(client).discharge(start)
    assign(client, "BAT-C-2500", start + timedelta(seconds=300))     # cell swapped mid-test
    act(client, uid, "complete", start + timedelta(seconds=700))
    v = view(client, uid)
    assert v["results"]["battery_stayed_selected"] is False
    assert any(c["check"] == "battery stayed selected" and not c["ok"] for c in v["validity"]["checks"])
    # readings after the swap belong to BAT-C and are not counted in BAT-A's test
    assert v["results"]["delivered_mah"] < 0.5 * 600 / 3.6


def test_aborted_test_freezes_results_and_is_invalid(client):
    uid, at = ready_test(client)
    start = at + timedelta(minutes=5)
    act(client, uid, "start", start)
    Bench(client).discharge(start, dur=300, end_v=3.7)
    act(client, uid, "abort", start + timedelta(seconds=330), reason="temperature rising")
    v = view(client, uid)
    assert v["state"] == "aborted" and v["results_frozen"] and v["validity"]["classification"] == "aborted"
    assert v["results"]["delivered_mah"] == pytest.approx(0.5 * 300 / 3.6)


# ---------------------------------------------------------------- preservation + export
def test_tests_never_modify_measurements_and_export_csv(client):
    uid, at = ready_test(client)
    start = at + timedelta(minutes=5)
    act(client, uid, "start", start)
    end = Bench(client).discharge(start)
    before = client.get("/api/battery/BAT-A-3000/history").json()["total"]
    sessions_before = client.get("/api/battery/BAT-A-3000/sessions").json()["sessions"]
    act(client, uid, "complete", end)
    uid2 = client.post("/api/tests", json={"battery_id": "BAT-C-2500"}).json()["test_uid"]
    client.post(f"/api/tests/{uid2}/abort", json={"reason": "not needed"})
    assert client.get("/api/battery/BAT-A-3000/history").json()["total"] == before
    assert client.get("/api/battery/BAT-A-3000/sessions").json()["sessions"] == sessions_before

    rows = list(csv.DictReader(io.StringIO(client.get("/api/tests/export.csv").text)))
    by = {r["test_uid"]: r for r in rows}
    assert set(by) == {uid, uid2}
    assert by[uid]["valid_for_full_capacity"] == "True" and float(by[uid]["delivered_mah"]) == pytest.approx(83.333, rel=1e-4)
    assert by[uid2]["state"] == "aborted" and by[uid2]["abort_reason"] == "not needed"
    only_c = list(csv.DictReader(io.StringIO(client.get("/api/tests/export.csv?battery_id=BAT-C-2500").text)))
    assert [r["test_uid"] for r in only_c] == [uid2]
    raw = list(csv.DictReader(io.StringIO(client.get(f"/api/tests/{uid}/readings.csv").text)))
    assert raw and all(r["battery_id"] == "BAT-A-3000" for r in raw)
    assert {r["test_running"] for r in raw} <= {"True", "False"}


def test_history_filters_per_battery(client):
    a = client.post("/api/tests", json={"battery_id": "BAT-A-3000"}).json()["test_uid"]
    c = client.post("/api/tests", json={"battery_id": "BAT-C-2500"}).json()["test_uid"]
    assert [t["test_uid"] for t in client.get("/api/tests?battery_id=BAT-A-3000").json()["tests"]] == [a]
    assert {t["test_uid"] for t in client.get("/api/tests").json()["tests"]} == {a, c}
