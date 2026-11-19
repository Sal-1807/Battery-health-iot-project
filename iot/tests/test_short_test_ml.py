"""Battery Health Predictions for Quick Tests: genuine features in, no fabricated SoH/RUL out."""

from __future__ import annotations

from datetime import timedelta

import pytest

from iot.backend import short_test_ml

from .test_quick_tests import NOW, assign, confirm, valid_run, view


def three_quick_tests(client, battery="BAT-A-3000"):
    t0 = NOW - timedelta(hours=2)
    assign(client, battery, t0 - timedelta(minutes=1))
    uids = []
    for k in range(3):
        uid = valid_run(client, battery, t0 + timedelta(minutes=20 * k))
        view(client, uid)
        confirm(client, uid)
        uids.append(uid)
    return uids


def test_every_model_input_is_documented_as_incompatible():
    expected = {"Voltage_mean", "Voltage_min", "Voltage_max", "Voltage_range", "Current_mean", "Current_std",
                "Temperature_mean", "Temperature_max", "Temperature_rise", "Cycle_number"}
    assert set(short_test_ml.INPUT_COMPATIBILITY) == expected
    for info in short_test_ml.INPUT_COMPATIBILITY.values():
        assert info["kind"] in ("missing", "different_quantity", "out_of_regime") and info["reason"]
    assert short_test_ml.INPUT_COMPATIBILITY["Cycle_number"]["kind"] == "missing"


def test_assessment_reads_the_three_genuine_quick_tests_and_produces_no_soh_or_rul(client):
    uids = three_quick_tests(client)
    quick = client.get("/api/quick-tests?battery_id=BAT-A-3000").json()["batteries"][0]
    r = client.get("/api/ml/quick-test-assessment?battery_id=BAT-A-3000")
    assert r.status_code == 200
    a = r.json()["batteries"][0]

    # genuine measured features, identical to the Quick Test records
    m = a["quick_tests"]["measured"]
    assert a["quick_tests"]["count"] == 3 and [x["test_uid"] for x in m] == uids
    for got, rec in zip(m, quick["cycles"]):
        for k in ("mean_voltage_v", "mean_current_a", "voltage_drop_v", "max_temperature_c", "delivered_mah", "delivered_wh"):
            assert got[k] == pytest.approx(rec[k])

    # every saved-model input explained; none usable; current compared by magnitude and out of the NASA regime
    inputs = {x["model_input"]: x for x in a["saved_model"]["inputs"]}
    assert len(inputs) == 10 and a["saved_model"]["usable"] is False
    assert all(x["usable_by_saved_model"] is False for x in inputs.values())
    cur = inputs["Current_mean"]
    assert cur["quick_values"] == [pytest.approx(0.37)] * 3 and cur["inside_nasa_range"] is False
    assert cur["nasa_training_range"]["min"] > 1.0                    # NASA ~2 A, compared as magnitude
    assert inputs["Cycle_number"]["quick_values"] == []               # not filled in

    # short-test model is checked, not assumed; with this data it is not defensible
    f = a["short_test_model"]
    assert f["defensible_short_test_model"] is False and f["implemented"] is False
    met = {c["requirement"].split(":")[0]: c["met"] for c in f["checks"]}
    assert met["Operating regime overlaps the NASA training data (load current)"] is False
    assert any("Ground-truth SoH" in c["requirement"] and c["met"] is False for c in f["checks"])

    assert a["soh"] == {"status": "unavailable", "value": None}
    assert a["rul"] == {"status": "unavailable", "value": None}


def test_assessment_never_invalidates_tests_or_creates_predictions(client):
    uids = three_quick_tests(client)
    before = client.get("/api/quick-tests?battery_id=BAT-A-3000").json()["batteries"][0]
    client.get("/api/ml/quick-test-assessment")
    after = client.get("/api/quick-tests?battery_id=BAT-A-3000").json()["batteries"][0]
    assert after["count"] == before["count"] == 3 and after["cycles"] == before["cycles"]
    assert all(view(client, u)["quick_test"]["status"] == "counted" for u in uids)
    assert all(view(client, u)["validity"]["valid_for_full_capacity"] is False for u in uids)   # still not full cycles
    a = next(b for b in client.get("/api/batteries").json()["batteries"] if b["battery_id"] == "BAT-A-3000")
    assert a["latest_valid_prediction"] is None
    preds = client.get("/api/battery/BAT-A-3000/predictions").json()["predictions"]
    assert all(p["status"] == "unavailable" and p["soh_pct"] is None and p["rul_cycles"] is None for p in preds)


def test_all_profiles_are_assessed_independently(client):
    three_quick_tests(client, "BAT-A-3000")
    rows = {b["battery_id"]: b for b in client.get("/api/ml/quick-test-assessment").json()["batteries"]}
    assert set(rows) == {"BAT-A-3000", "BAT-B-3000", "BAT-C-2500"}
    assert rows["BAT-A-3000"]["quick_tests"]["count"] == 3
    assert rows["BAT-B-3000"]["quick_tests"]["count"] == 0 and rows["BAT-B-3000"]["quick_tests"]["measured"] == []
