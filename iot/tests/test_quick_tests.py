"""Quick Test Cycle protocol: 3 x 300 s supervised tests per battery.

Covers the timer, the automatic end of SOFTWARE recording at 300 s (which must
not claim to disconnect anything), the physical-disconnect confirmation, valid
vs interrupted / incomplete / telemetry-gap tests, battery switching and isolation.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta, timezone

import pytest

from iot.backend import battery_tests as bt
from iot.backend.assignments import switch_assignment

from .conftest import reading

DEV = "esp32-devkit-01"
CHECK = {"battery_identity_confirmed": True, "load_rating_verified": True, "supervisor_approval": True,
         "disconnect_procedure_confirmed": True, "voltage_cutoff_confirmed": True, "supervisor_name": "Supervisor",
         "cutoff_v": 3.0, "load_resistance_ohm": 10.0, "load_power_rating_w": 5.0}


def ms(t):
    return int(t.timestamp() * 1000)


def assign(client, battery, at):
    app = client.app
    with app.state.db.write() as conn:
        switch_assignment(conn, DEV, battery, None, app.state.settings, now_ms=ms(at))


def act(client, uid, action, at, reason=None):
    with client.app.state.db.write() as conn:
        return bt.transition(conn, uid, action, reason, client.app.state.settings, now_ms=ms(at))


class Rig:
    """Simulated ESP32 readings for the selected battery (test-only, routed through the real API)."""

    _boots = 0

    def __init__(self, client):
        Rig._boots += 1                              # each rig = a fresh device boot (seq restarts)
        self.c, self.seq, self.boot = client, 0, f"qt{Rig._boots}"

    def post(self, specs):
        rows = []
        for t, v, i in specs:
            self.seq += 1
            rows.append(reading(battery="CELL-18650-01", device_id=DEV, ts=t, v=v, i=i, t=26.0, seq=self.seq, boot_id=self.boot))
        for k in range(0, len(rows), 500):
            assert self.c.post("/api/battery/readings", json={"readings": rows[k:k + 500]}).json()["rejected"] == 0

    def run(self, start, seconds=300, i=0.37, gap=None, load_from=0):
        spec = [(start - timedelta(seconds=6 - 2 * k), 3.95, 0.0) for k in range(3)]       # at rest before start
        for sec in range(0, seconds + 1, 2):
            if gap and gap[0] <= sec < gap[1]:
                continue
            on = sec >= load_from
            spec.append((start + timedelta(seconds=sec), 3.90 - 0.0004 * sec if on else 3.95, i if on else 0.0))
        self.post(spec)


def new_quick(client, battery):
    r = client.post("/api/tests", json={"battery_id": battery})
    assert r.status_code == 200, r.text
    uid = r.json()["test_uid"]
    assert client.put(f"/api/tests/{uid}/checklist", json=CHECK).json()["state"] == "ready"
    return uid


def view(client, uid):
    return client.get(f"/api/tests/{uid}").json()


def confirm(client, uid):
    r = client.post(f"/api/tests/{uid}/confirm-disconnect")
    assert r.status_code == 200, r.text


def valid_run(client, battery, start):
    uid = new_quick(client, battery)
    act(client, uid, "start", start)
    Rig(client).run(start)
    return uid


NOW = datetime.now(timezone.utc)


# ---------------------------------------------------------------- defaults + timer
def test_default_quick_test_is_300_s(client):
    assign(client, "BAT-A-3000", NOW - timedelta(minutes=5))
    uid = new_quick(client, "BAT-A-3000")
    v = view(client, uid)
    assert v["kind"] == "quick" and v["duration_s"] == 300
    assert v["quick_test"]["target"] == 3 and v["quick_test"]["status"] == "not_started"


def test_timer_reports_elapsed_and_remaining(client):
    assign(client, "BAT-A-3000", NOW - timedelta(minutes=10))
    uid = new_quick(client, "BAT-A-3000")
    act(client, uid, "start", datetime.now(timezone.utc) - timedelta(seconds=100))
    q = view(client, uid)["quick_test"]
    assert q["status"] == "in_progress"
    assert q["elapsed_s"] == pytest.approx(100, abs=3) and q["remaining_s"] == pytest.approx(200, abs=3)


# ---------------------------------------------------------------- auto end + disconnect
def test_recording_ends_automatically_at_300_s_without_claiming_disconnection(client):
    start = NOW - timedelta(seconds=400)
    assign(client, "BAT-A-3000", start - timedelta(minutes=1))
    uid = valid_run(client, "BAT-A-3000", start)
    v = view(client, uid)                                   # polling triggers the end of the software window
    assert v["state"] == "completed"
    assert v["ended_at"].startswith((start + timedelta(seconds=300)).strftime("%Y-%m-%dT%H:%M:%S"))
    assert v["results"]["elapsed_s"] == pytest.approx(300)
    note = v["history"][-1]["note"]
    assert "did NOT disconnect the battery or stop the discharge current" in note
    q = v["quick_test"]
    assert q["status"] == "counted" and q["number"] == 1 and q["auto_ended"]
    assert q["needs_disconnect_confirmation"] is True
    assert "did not connect, disconnect" in v["hardware_notice"]
    # stored full-capacity verdict is unchanged: a 5-minute test is not a full cycle
    assert v["validity"]["valid_for_full_capacity"] is False
    assert client.get("/api/battery/BAT-A-3000/stats").json()["stats"]["completed_cycles"] == 0


def test_next_test_requires_physical_disconnect_confirmation(client):
    start = NOW - timedelta(seconds=400)
    assign(client, "BAT-A-3000", start - timedelta(minutes=1))
    uid = valid_run(client, "BAT-A-3000", start)
    view(client, uid)
    r = client.post("/api/tests", json={"battery_id": "BAT-A-3000"})
    assert r.status_code == 409 and "physically disconnected" in r.json()["detail"]
    # load current still measured right now -> confirmation refused
    Rig(client).post([(datetime.now(timezone.utc), 3.80, 0.37)])
    r = client.post(f"/api/tests/{uid}/confirm-disconnect")
    assert r.status_code == 409 and "still being measured" in r.json()["detail"]
    Rig(client).post([(datetime.now(timezone.utc) + timedelta(seconds=1), 3.86, 0.0)])
    confirm(client, uid)
    assert view(client, uid)["quick_test"]["needs_disconnect_confirmation"] is False
    assert client.post("/api/tests", json={"battery_id": "BAT-A-3000"}).status_code == 200


# ---------------------------------------------------------------- 3/3 protocol
def test_three_valid_tests_complete_the_protocol(client):
    t0 = NOW - timedelta(hours=2)
    assign(client, "BAT-A-3000", t0 - timedelta(minutes=1))
    uids = []
    for k in range(3):
        start = t0 + timedelta(minutes=20 * k)
        uid = valid_run(client, "BAT-A-3000", start)
        view(client, uid)
        confirm(client, uid)
        uids.append(uid)
    q = client.get("/api/quick-tests?battery_id=BAT-A-3000").json()["batteries"][0]
    assert q["count"] == 3 and q["complete"] and q["next_test_number"] is None
    assert [c["number"] for c in q["cycles"]] == [1, 2, 3]
    c = q["cycles"][0]
    assert c["delivered_mah"] == pytest.approx(0.37 * 298 / 3.6, rel=0.01)    # load from first reading after start
    assert c["initial_voltage_v"] == 3.95 and c["voltage_drop_v"] == pytest.approx(3.95 - (3.90 - 0.0004 * 300), abs=1e-6)
    assert c["max_temperature_c"] == 26.0 and c["mean_current_a"] == pytest.approx(0.37)
    assert q["summary"]["delivered_mah"]["mean"] == pytest.approx(c["delivered_mah"])
    # viewing again never counts a test twice
    for u in uids:
        view(client, u)
    assert client.get("/api/quick-tests?battery_id=BAT-A-3000").json()["batteries"][0]["count"] == 3


# ---------------------------------------------------------------- not counted
def _ended(client, battery, start, **run):
    assign(client, battery, start - timedelta(minutes=1))
    uid = new_quick(client, battery)
    act(client, uid, "start", start)
    Rig(client).run(start, **run)
    return uid


def test_stopped_early_is_incomplete(client):
    start = NOW - timedelta(seconds=400)
    uid = _ended(client, "BAT-B-3000", start, seconds=150)
    act(client, uid, "complete", start + timedelta(seconds=152))
    q = view(client, uid)["quick_test"]
    assert q["status"] == "incomplete" and q["number"] is None


def test_load_connected_late_is_incomplete(client):
    start = NOW - timedelta(seconds=400)
    uid = _ended(client, "BAT-B-3000", start, load_from=150)
    assert view(client, uid)["quick_test"]["status"] == "incomplete"


def test_telemetry_gap_is_marked_separately(client):
    start = NOW - timedelta(seconds=400)
    uid = _ended(client, "BAT-B-3000", start, gap=(100, 200))
    v = view(client, uid)
    assert v["quick_test"]["status"] == "telemetry_gap" and v["results"]["gaps"]


def test_aborted_and_switched_tests_are_interrupted(client):
    start = NOW - timedelta(hours=1)
    uid = _ended(client, "BAT-C-2500", start, seconds=120)
    act(client, uid, "abort", start + timedelta(seconds=125), reason="supervisor stopped test")
    assert view(client, uid)["quick_test"]["status"] == "interrupted"
    confirm(client, uid)
    start2 = start + timedelta(minutes=20)
    uid2 = _ended(client, "BAT-C-2500", start2)
    assign(client, "BAT-B-3000", start2 + timedelta(seconds=150))           # battery swapped mid-test
    v = view(client, uid2)
    assert v["quick_test"]["status"] == "interrupted"
    q = client.get("/api/quick-tests?battery_id=BAT-C-2500").json()["batteries"][0]
    assert q["count"] == 0 and q["not_counted"]["interrupted"] == 2


# ---------------------------------------------------------------- isolation + comparison
def test_batteries_are_tracked_independently(client):
    t0 = NOW - timedelta(hours=3)
    for k, (bat, amps) in enumerate((("BAT-A-3000", 0.37), ("BAT-C-2500", 0.40))):
        start = t0 + timedelta(minutes=30 * k)
        assign(client, bat, start - timedelta(minutes=1))
        uid = new_quick(client, bat)
        act(client, uid, "start", start)
        Rig(client).run(start, i=amps)
        view(client, uid)
        confirm(client, uid)
    ov = client.get("/api/quick-tests").json()
    by = {q["battery_id"]: q for q in ov["batteries"]}
    assert set(by) == {"BAT-A-3000", "BAT-B-3000", "BAT-C-2500"}
    assert (by["BAT-A-3000"]["count"], by["BAT-B-3000"]["count"], by["BAT-C-2500"]["count"]) == (1, 0, 1)
    rows = {r["battery_id"]: r for r in ov["comparison"]["rows"]}
    assert rows["BAT-C-2500"]["mean_current_a"] == pytest.approx(0.40)
    assert rows["BAT-A-3000"]["mean_current_a"] == pytest.approx(0.37)
    assert rows["BAT-B-3000"]["delivered_mah"] is None
    g = ov["comparison"]["group_mean"]["delivered_mah"]
    assert rows["BAT-A-3000"]["delivered_mah_diff_pct"] == pytest.approx(100 * (rows["BAT-A-3000"]["delivered_mah"] - g) / g)
    assert "not a capacity" in ov["comparison"]["note"]
    assert ov["model_status"]["validated_model"] is False
    summaries = {b["battery_id"]: b for b in client.get("/api/batteries").json()["batteries"]}
    assert summaries["BAT-A-3000"]["quick_test_cycles"]["count"] == 1
    assert summaries["BAT-A-3000"]["latest_valid_prediction"] is None          # still no SoH/RUL


def test_features_csv_for_experimental_ml(client):
    start = NOW - timedelta(seconds=400)
    assign(client, "BAT-A-3000", start - timedelta(minutes=1))
    view(client, valid_run(client, "BAT-A-3000", start))
    text = client.get("/api/quick-tests/features.csv").text
    assert text.startswith("# No validated capacity, SoH or RUL model exists")
    rows = list(csv.DictReader(io.StringIO("\n".join(text.splitlines()[1:]))))
    assert len(rows) == 1 and rows[0]["battery_id"] == "BAT-A-3000" and rows[0]["rated_capacity_mah"] == "3000.0"


def test_retired_battery_is_archived_not_merged(client):
    client.post("/api/battery/reading", json=reading(battery="CELL-18650-01", device_id="old-dev"))
    bats = {b["battery_id"]: b for b in client.get("/api/batteries").json()["batteries"]}
    assert bats["CELL-18650-01"]["archived"] is True
    assert not any(bats[p]["archived"] for p in ("BAT-A-3000", "BAT-B-3000", "BAT-C-2500"))
    assert client.get("/api/battery/CELL-18650-01/history").json()["total"] == 1      # still readable
    assert client.get("/api/battery/BAT-A-3000/history").json()["total"] == 0          # never merged


def test_supervised_legacy_tests_are_not_quick_cycles(client):
    start = NOW - timedelta(hours=1)
    assign(client, "BAT-A-3000", start - timedelta(minutes=1))
    uid = client.post("/api/tests", json={"battery_id": "BAT-A-3000", "kind": "supervised"}).json()["test_uid"]
    client.put(f"/api/tests/{uid}/checklist", json=CHECK)
    act(client, uid, "start", start)
    Rig(client).run(start)
    act(client, uid, "complete", start + timedelta(seconds=300))
    assert view(client, uid)["quick_test"] is None
    assert client.get("/api/quick-tests?battery_id=BAT-A-3000").json()["batteries"][0]["count"] == 0


# ---------------------------------------------------------------- regression: aborted tests never block creation
def test_aborted_tests_do_not_block_a_new_quick_test(client):
    """An aborted legacy (supervised, kind NULL) test and an aborted never-started quick test must
    not disable test creation. Records are kept and the battery assignment is unchanged."""
    assign(client, "BAT-A-3000", NOW - timedelta(minutes=30))
    legacy = client.post("/api/tests", json={"battery_id": "BAT-A-3000", "kind": "supervised"}).json()["test_uid"]
    assert client.put(f"/api/tests/{legacy}/checklist", json=CHECK).json()["state"] == "ready"
    act(client, legacy, "start", NOW - timedelta(minutes=20))
    act(client, legacy, "abort", NOW - timedelta(minutes=19), "aborted by operator")
    with client.app.state.db.write() as conn:            # as stored before migration v8
        conn.execute("UPDATE battery_tests SET kind=NULL, duration_s=NULL WHERE test_uid=?", (legacy,))
    unstarted = new_quick(client, "BAT-A-3000")
    act(client, unstarted, "abort", NOW - timedelta(minutes=10), "created by mistake")

    assert view(client, unstarted)["quick_test"]["needs_disconnect_confirmation"] is False
    assert client.get("/api/quick-tests").status_code == 200
    r = client.post("/api/tests", json={"battery_id": "BAT-A-3000"})
    assert r.status_code == 200, r.text
    uids = [t["test_uid"] for t in client.get("/api/tests?battery_id=BAT-A-3000").json()["tests"]]
    assert {legacy, unstarted, r.json()["test_uid"]} <= set(uids)          # every record kept
    dev = next(d for d in client.get("/api/devices").json()["devices"] if d["device_id"] == DEV)
    assert dev["assignment"]["battery_id"] == "BAT-A-3000"


def test_aborted_started_quick_test_still_requires_disconnect_confirmation(client):
    """Safety rule unchanged: aborting a started quick test leaves the load possibly connected."""
    start = NOW - timedelta(seconds=200)
    assign(client, "BAT-B-3000", start - timedelta(minutes=1))
    uid = new_quick(client, "BAT-B-3000")
    act(client, uid, "start", start)
    act(client, uid, "abort", start + timedelta(seconds=60), "aborted by operator")
    assert view(client, uid)["quick_test"]["needs_disconnect_confirmation"] is True
    assert client.post("/api/tests", json={"battery_id": "BAT-B-3000"}).status_code == 409
    confirm(client, uid)
    assert client.post("/api/tests", json={"battery_id": "BAT-B-3000"}).status_code == 200
