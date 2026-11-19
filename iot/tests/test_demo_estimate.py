"""Experimental Demo Estimate: deterministic, derived from the measured Quick Tests, clearly labelled."""

from __future__ import annotations

from datetime import timedelta

import pytest

from iot.backend import demo_estimate as de

from .test_quick_tests import NOW, Rig, act, assign, confirm, new_quick, view


def cyc(n, drop=0.09, i=0.37, rise=0.8, p=1.45):
    return {"test_uid": f"T{n}", "initial_voltage_v": 4.0, "final_voltage_v": 4.0 - drop, "voltage_drop_v": drop,
            "mean_current_a": i, "mean_power_w": p, "max_temperature_c": 26.0, "temperature_rise_c": rise,
            "delivered_mah": 30.8, "delivered_wh": 0.12}


def test_formula_is_documented_and_deterministic():
    cycles = [cyc(1), cyc(2), cyc(3)]
    a, b = de.estimate(cycles, 3), de.estimate([dict(c) for c in cycles], 3)
    assert a == b and a["status"] == "demo" and a["label"] == "Experimental Demo Estimate"
    r = 0.09 / 0.37
    soh = 100 - 20 * (r / 0.15 - 1)                       # thermal 0.8/1.45 K/W < 2 K/W -> no penalty
    assert a["soh_pct"] == pytest.approx(round(soh, 1))
    assert a["rul_cycles"] == round(500 * (soh - 80) / 20)
    assert a["inputs"]["apparent_resistance_ohm"] == pytest.approx(r)
    assert "not a machine-learning prediction" in a["disclaimer"]


def test_estimate_changes_with_the_measurements():
    base = de.estimate([cyc(k) for k in (1, 2, 3)], 3)
    worse = de.estimate([cyc(k, drop=0.15) for k in (1, 2, 3)], 3)        # larger voltage drop -> higher resistance
    hot = de.estimate([cyc(k, rise=6.0) for k in (1, 2, 3)], 3)           # stronger heating -> thermal penalty
    assert worse["soh_pct"] < base["soh_pct"] and worse["rul_cycles"] < base["rul_cycles"]
    assert hot["soh_pct"] < base["soh_pct"] and hot["components"]["thermal_penalty_pts"] > 0
    healthy = de.estimate([cyc(k, drop=0.03) for k in (1, 2, 3)], 3)
    assert healthy["soh_pct"] == 100.0 and healthy["rul_cycles"] == de.RATED_CYCLE_LIFE


def test_no_estimate_without_enough_valid_tests():
    for cycles in ([], [cyc(1)], [cyc(1), cyc(2)]):
        e = de.estimate(cycles, 3)
        assert e["status"] == "no_estimate" and e["soh_pct"] is None and e["rul_cycles"] is None
    missing = de.estimate([cyc(1), cyc(2), {**cyc(3), "voltage_drop_v": None}], 3)
    assert missing["status"] == "no_estimate"


def _three(client, battery, i):
    t0 = NOW - timedelta(hours=3)
    assign(client, battery, t0 - timedelta(minutes=1))
    for k in range(3):
        uid = new_quick(client, battery)
        start = t0 + timedelta(minutes=20 * k)
        act(client, uid, "start", start)
        Rig(client).run(start, i=i)
        view(client, uid)
        confirm(client, uid)


def test_api_uses_the_saved_quick_tests_and_keeps_ml_separate(client):
    b = client.get("/api/batteries").json()["batteries"]
    assert all(x["quick_test_cycles"]["demo_estimate"]["status"] == "no_estimate" for x in b if x.get("managed"))
    _three(client, "BAT-A-3000", i=0.37)
    q = client.get("/api/quick-tests?battery_id=BAT-A-3000").json()["batteries"][0]
    e = q["demo_estimate"]
    assert e["status"] == "demo" and e["tests_used"] == [c["test_uid"] for c in q["cycles"]]
    assert e["inputs"]["voltage_drop_v"] == pytest.approx(sum(c["voltage_drop_v"] for c in q["cycles"]) / 3)
    assert e == de.estimate(q["cycles"], 3)                                 # same records -> same numbers
    a = next(x for x in client.get("/api/batteries").json()["batteries"] if x["battery_id"] == "BAT-A-3000")
    assert a["quick_test_cycles"]["demo_estimate"]["soh_pct"] == e["soh_pct"]
    assert a["latest_valid_prediction"] is None                             # ML predictions untouched
    assert all(t["quick_test"]["status"] == "counted" for t in client.get("/api/tests?battery_id=BAT-A-3000").json()["tests"])


def test_different_measurements_on_another_battery_give_different_estimates(client):
    _three(client, "BAT-A-3000", i=0.37)
    _three(client, "BAT-B-3000", i=0.50)            # same voltage profile, higher current -> lower apparent resistance
    rows = {b["battery_id"]: b["demo_estimate"] for b in client.get("/api/quick-tests").json()["batteries"]}
    assert rows["BAT-B-3000"]["soh_pct"] > rows["BAT-A-3000"]["soh_pct"]
    assert rows["BAT-C-2500"]["status"] == "no_estimate"


def test_focus_battery_is_reported_and_other_profiles_are_kept(client):
    h = client.get("/api/health").json()
    assert h["focus_battery"] == "BAT-A-3000"
    ids = {b["battery_id"] for b in client.get("/api/batteries").json()["batteries"]}
    assert {"BAT-A-3000", "BAT-B-3000", "BAT-C-2500"} <= ids                # hidden in the UI, never deleted
