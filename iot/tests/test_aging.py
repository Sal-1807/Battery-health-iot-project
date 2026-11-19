"""Aging analytics for the physical battery profiles: capacity integration, EFC,
comparability, missing data, interrupted sessions and battery isolation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from iot.backend.assignments import switch_assignment

from .conftest import reading

DEV = "esp32-devkit-01"
T0 = datetime.now(timezone.utc) - timedelta(hours=20)


def assign(client, battery_id, at):
    app = client.app
    with app.state.db.write() as conn:
        switch_assignment(conn, DEV, battery_id, None, app.state.settings, now_ms=int(at.timestamp() * 1000))


def post(client, rows):
    for k in range(0, len(rows), 500):
        out = client.post("/api/battery/readings", json={"readings": rows[k:k + 500]}).json()
        assert out["rejected"] == 0, out


class Bench:
    """Generates ESP32 readings for a scripted test on whatever battery is selected."""

    def __init__(self, client):
        self.c, self.seq = client, 0

    def r(self, t, v, i, temp):
        self.seq += 1
        return reading(battery="CELL-18650-01", device_id=DEV, ts=t, v=v, i=i, t=temp, seq=self.seq, boot_id="bench")

    def discharge(self, start, i=0.5, dur=600, temp=25.0, rest_v=4.18, end_v=2.98, gap=None, rest_after=True):
        rows = [self.r(start + timedelta(seconds=2 * k), rest_v, 0.0, temp) for k in range(5)]
        t_on = start + timedelta(seconds=10)
        n = dur // 5
        for k in range(n + 1):
            sec = 5 * k
            if gap and gap[0] <= sec < gap[1]:
                continue
            v = 4.10 - (4.10 - end_v) * k / n
            rows.append(self.r(t_on + timedelta(seconds=sec), v, i, temp))
        end = t_on + timedelta(seconds=5 * n)
        if rest_after:
            rows += [self.r(end + timedelta(seconds=2 + 2 * k), 3.3, 0.0, temp) for k in range(10)]
        post(self.c, rows)
        return end + timedelta(seconds=30)


def aging(client, bid):
    r = client.get(f"/api/aging/{bid}")
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------- missing data
def test_no_sessions_is_insufficient_data(client):
    for bid in ("BAT-A-3000", "BAT-B-3000", "BAT-C-2500"):
        a = aging(client, bid)
        assert a["retention"]["status"] == "insufficient_data" and a["retention"]["latest_pct"] is None
        assert a["capacity_series"] == [] and a["reference"] is None
        assert a["totals"]["delivered_mah"] == 0 and a["totals"]["equivalent_full_cycles"] == 0


def test_partial_sessions_count_in_totals_but_not_in_capacity(client):
    assign(client, "BAT-A-3000", T0)
    b = Bench(client)
    b.discharge(T0 + timedelta(minutes=1), rest_v=3.95, end_v=3.60)       # partial: not from full, no cutoff
    a = aging(client, "BAT-A-3000")
    assert a["totals"]["n_sessions"] == 1
    assert a["totals"]["delivered_mah"] == pytest.approx(0.5 * 600 / 3.6)
    assert a["n_full_tests"] == 0 and a["capacity_series"] == []
    assert a["retention"]["status"] == "insufficient_data" and "No full" in a["retention"]["reason"]
    issues = a["sessions"][0]["full_test_issues"]
    assert any("rested full charge" in x for x in issues) and any("partial discharge" in x for x in issues)


def test_single_full_test_is_a_reference_not_a_trend(client):
    assign(client, "BAT-A-3000", T0)
    Bench(client).discharge(T0 + timedelta(minutes=1))
    a = aging(client, "BAT-A-3000")
    assert a["n_full_tests"] == 1 and a["n_comparable_tests"] == 1
    assert a["reference"]["capacity_mah"] == pytest.approx(0.5 * 600 / 3.6)
    assert a["retention"]["status"] == "insufficient_data" and "at least 2" in a["retention"]["reason"]


# ---------------------------------------------------------------- capacity + retention
def test_retention_relative_to_first_measured_test_not_the_label(client):
    assign(client, "BAT-A-3000", T0)
    b = Bench(client)
    t = b.discharge(T0 + timedelta(minutes=1), dur=600)
    b.discharge(t + timedelta(hours=1), dur=540)                 # later test delivers 90 %
    a = aging(client, "BAT-A-3000")
    s = a["capacity_series"]
    assert [x["capacity_mah"] for x in s] == pytest.approx([0.5 * 600 / 3.6, 0.5 * 540 / 3.6])
    assert [x["retention_pct"] for x in s] == pytest.approx([100.0, 90.0])
    assert a["retention"]["status"] == "ok" and a["retention"]["latest_pct"] == pytest.approx(90.0)
    # the reference is the measured 83.3 mAh, never the 3000 mAh label
    assert a["reference"]["capacity_mah"] == pytest.approx(83.333, rel=1e-4) and a["rated_capacity_mah"] == 3000
    assert "Not absolute SoH" in a["retention"]["definition"]
    assert a["soh"]["status"] == "unavailable" and a["rul"]["status"] == "unavailable"
    # capacity integration includes energy and duration
    assert a["totals"]["discharge_duration_s"] == pytest.approx(600 + 540)
    mean_v = (4.10 + 2.98) / 2
    assert s[0]["energy_wh"] == pytest.approx(0.5 * mean_v * 600 / 3600, rel=0.01)


def test_different_load_or_temperature_is_not_compared(client):
    assign(client, "BAT-A-3000", T0)
    b = Bench(client)
    t = b.discharge(T0 + timedelta(minutes=1), i=0.5, temp=25.0)
    t = b.discharge(t + timedelta(hours=1), i=0.8, dur=360, temp=25.0)      # 60 % higher load
    t = b.discharge(t + timedelta(hours=1), i=0.5, temp=38.0)               # 13 degC warmer
    b.discharge(t + timedelta(hours=1), i=0.52, dur=570, temp=27.0)         # within tolerances
    a = aging(client, "BAT-A-3000")
    assert a["n_full_tests"] == 4 and a["n_comparable_tests"] == 2
    ses = {x["session_id"]: x for x in a["sessions"]}
    issues = [x["comparability_issues"] for x in a["sessions"]]
    assert any("load current" in i for x in issues for i in x)
    assert any("temperature" in i for x in issues for i in x)
    assert len(a["capacity_series"]) == 2
    assert sum(1 for x in ses.values() if x["is_reference"]) == 1


def test_gap_and_interrupted_sessions_are_excluded(client):
    assign(client, "BAT-A-3000", T0)
    b = Bench(client)
    t = b.discharge(T0 + timedelta(minutes=1))                                  # good reference
    t = b.discharge(t + timedelta(hours=1), gap=(200, 260))                     # 60 s telemetry gap
    t2 = t + timedelta(hours=1)
    b.discharge(t2, rest_after=False)                                           # reaches cutoff, then...
    assign(client, "BAT-B-3000", t2 + timedelta(seconds=620))                   # ...battery switched mid-session
    a = aging(client, "BAT-A-3000")
    by_reason = {x["session_id"]: x["full_test_issues"] for x in a["sessions"]}
    assert len(by_reason) == 3
    flat = [i for v in by_reason.values() for i in v]
    assert any("telemetry gap" in i for i in flat)
    assert any("interrupted (battery switched)" in i for i in flat)
    assert a["n_full_tests"] == 1 and a["retention"]["status"] == "insufficient_data"
    # completed-cycle count elsewhere agrees: interrupted runs are not completed cycles
    assert client.get("/api/battery/BAT-A-3000/stats").json()["stats"]["completed_cycles"] == 1


# ---------------------------------------------------------------- normalisation + isolation
def test_efc_and_c_rate_use_each_batterys_own_rating(client):
    b = Bench(client)
    assign(client, "BAT-A-3000", T0)
    t = b.discharge(T0 + timedelta(minutes=1))
    assign(client, "BAT-C-2500", t + timedelta(minutes=1))
    b.discharge(t + timedelta(minutes=2))
    ov = {x["battery_id"]: x for x in client.get("/api/aging").json()["batteries"]}
    q = 0.5 * 600 / 3.6
    assert ov["BAT-A-3000"]["totals"]["equivalent_full_cycles"] == pytest.approx(q / 3000)
    assert ov["BAT-C-2500"]["totals"]["equivalent_full_cycles"] == pytest.approx(q / 2500)
    assert ov["BAT-A-3000"]["reference"]["c_rate"] == pytest.approx(0.5 / 3.0)
    assert ov["BAT-C-2500"]["reference"]["c_rate"] == pytest.approx(0.5 / 2.5)
    assert ov["BAT-B-3000"]["totals"]["n_sessions"] == 0
    assert set(ov) == {"BAT-A-3000", "BAT-B-3000", "BAT-C-2500"}


def test_batteries_never_mix(client):
    b = Bench(client)
    assign(client, "BAT-A-3000", T0)
    t = b.discharge(T0 + timedelta(minutes=1), dur=600)
    t = b.discharge(t + timedelta(hours=1), dur=540)
    assign(client, "BAT-C-2500", t + timedelta(minutes=1))
    b.discharge(t + timedelta(minutes=2), dur=480, temp=30.0)
    a, c = aging(client, "BAT-A-3000"), aging(client, "BAT-C-2500")
    assert not ({x["session_id"] for x in a["sessions"]} & {x["session_id"] for x in c["sessions"]})
    assert c["reference"]["capacity_mah"] == pytest.approx(0.5 * 480 / 3.6)      # C's own first test, not A's
    assert c["retention"]["status"] == "insufficient_data"                      # A's tests don't help C
    assert all(x["mean_temperature_c"] == pytest.approx(30.0) for x in c["trends"])
    assert all(x["mean_temperature_c"] == pytest.approx(25.0) for x in a["trends"])


def test_temperature_and_loaded_voltage_trends(client):
    assign(client, "BAT-B-3000", T0)
    Bench(client).discharge(T0 + timedelta(minutes=1), temp=26.5)
    tr = aging(client, "BAT-B-3000")["trends"]
    assert len(tr) == 1 and tr[0]["kind"] == "comparable_full"
    assert tr[0]["mean_temperature_c"] == pytest.approx(26.5)
    assert tr[0]["mean_loaded_voltage_v"] == pytest.approx((4.10 + 2.98) / 2, abs=0.01)
    assert tr[0]["min_voltage_v"] == pytest.approx(2.98)


def test_only_physical_profiles(client):
    client.post("/api/battery/reading", json=reading(battery="SIM-X", device_id="sim", source="simulation"))
    assert client.get("/api/aging/SIM-X").status_code == 400
    assert client.get("/api/aging/NOPE").status_code == 404


# ---------------------------------------------------------------- partial-session throughput
def _partial_sessions(client, battery, start, sessions, boot):
    """Rest at 3.95 V (not a full charge), discharge, rest: partial sessions only."""
    b = Bench(client)
    t = start
    for amps, dur in sessions:
        rows = [b.r(t + timedelta(seconds=2 * k), 3.95, 0.0, 25.0) for k in range(6)]
        t += timedelta(seconds=12)
        rows += [b.r(t + timedelta(seconds=2 * k), 3.85, amps, 25.0) for k in range(dur // 2 + 1)]
        t += timedelta(seconds=dur + 2)
        rows += [b.r(t + timedelta(seconds=2 * k), 3.92, 0.0, 25.0) for k in range(8)]
        post(client, rows)
        t += timedelta(minutes=30)
    return b


BAT_A_SESSIONS = ((0.5, 216), (0.41, 36), (0.425, 144))      # 30.0, 4.1 and 17.0 mAh


def test_battery_a_partial_sessions_count_as_throughput_not_cycles(client):
    assign(client, "BAT-A-3000", T0)
    _partial_sessions(client, "BAT-A-3000", T0 + timedelta(minutes=1), BAT_A_SESSIONS, "a")
    a = aging(client, "BAT-A-3000")
    assert [round(x["charge_mah"], 2) for x in a["sessions"]] == [30.0, 4.1, 17.0]
    tp = a["throughput"]
    assert tp["total_mah"] == pytest.approx(51.1, abs=0.05)
    assert tp["partial_mah"] == pytest.approx(51.1, abs=0.05) and tp["n_partial_sessions"] == 3
    assert tp["full_mah"] == 0 and tp["n_full_tests"] == 0
    assert tp["equivalent_full_cycles"] == pytest.approx(51.1 / 3000, abs=1e-4)
    assert tp["total_wh"] > 0 and tp["duration_s"] == pytest.approx(216 + 36 + 144)
    assert "not the battery's capacity" in tp["definition"]
    # full-capacity analysis unchanged: no reference, no retention, no completed cycle
    assert a["reference"] is None and a["capacity_series"] == []
    assert a["retention"]["status"] == "insufficient_data"
    assert client.get("/api/battery/BAT-A-3000/stats").json()["stats"]["completed_cycles"] == 0
    assert all(s["counted_in_throughput"] and not s["full_test"] for s in a["sessions"])


def test_partial_throughput_efc_uses_own_rating_and_stays_isolated(client):
    assign(client, "BAT-C-2500", T0)
    _partial_sessions(client, "BAT-C-2500", T0 + timedelta(minutes=1), BAT_A_SESSIONS, "c")
    c = aging(client, "BAT-C-2500")["throughput"]
    assert c["equivalent_full_cycles"] == pytest.approx(51.1 / 2500, abs=1e-4)
    for other in ("BAT-A-3000", "BAT-B-3000"):
        assert aging(client, other)["throughput"]["total_mah"] == 0


def test_duplicate_telemetry_is_not_double_counted(client):
    assign(client, "BAT-A-3000", T0)
    b = _partial_sessions(client, "BAT-A-3000", T0 + timedelta(minutes=1), BAT_A_SESSIONS[:1], "d")
    before = aging(client, "BAT-A-3000")["throughput"]["total_mah"]
    # the bridge re-sends the whole batch (same boot_id + seq): every reading is a duplicate
    resend = [reading(battery="CELL-18650-01", device_id=DEV, ts=T0 + timedelta(minutes=1, seconds=2 * k),
                      v=3.85, i=0.5, t=25.0, seq=k + 1, boot_id="bench") for k in range(b.seq)]
    out = client.post("/api/battery/readings", json={"readings": resend[:500]}).json()
    assert out["stored"] == 0
    assert aging(client, "BAT-A-3000")["throughput"]["total_mah"] == pytest.approx(before)


def test_overlapping_session_record_is_counted_once(client):
    assign(client, "BAT-A-3000", T0)
    _partial_sessions(client, "BAT-A-3000", T0 + timedelta(minutes=1), BAT_A_SESSIONS[:1], "o")
    first = aging(client, "BAT-A-3000")["sessions"][0]
    with client.app.state.db.write() as conn:          # a record overlapping the first session in time
        row = dict(conn.execute("SELECT * FROM discharge_sessions WHERE id=?", (first["session_id"],)).fetchone())
        row.pop("id")
        row["start_ms"] += 10_000
        cols = list(row)
        conn.execute(f"INSERT INTO discharge_sessions({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                     [row[k] for k in cols])
    a = aging(client, "BAT-A-3000")
    assert a["throughput"]["total_mah"] == pytest.approx(30.0, abs=0.01)
    assert len(a["throughput"]["overlapping_sessions_excluded"]) == 1
    assert [s["counted_in_throughput"] for s in a["sessions"]] == [True, False]
    st = client.get("/api/battery/BAT-A-3000/stats").json()["stats"]          # Batteries page agrees
    assert st["total_discharged_mah"] == pytest.approx(30.0, abs=0.01)


def test_full_test_thresholds_unchanged():
    from iot.backend.config import Settings
    s = Settings()
    assert (s.full_charge_v, s.default_cutoff_v) == (4.15, 3.0)
