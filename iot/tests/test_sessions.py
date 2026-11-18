"""Discharge-session charge/energy integration.

Accuracy is checked against analytic integrals; gaps, noise and session
boundaries against the rules in iot/backend/sessions.py (never assume current).
"""

from __future__ import annotations

import dataclasses
import math
import random
from datetime import datetime, timedelta, timezone

import pytest

from iot.backend.sessions import update_session

from .conftest import reading

T0 = datetime.now(timezone.utc) - timedelta(hours=3)


def post_series(client, samples, battery="HW-1", boot="s"):
    """samples: list of (seconds_from_T0, current_a, voltage_v). Power is computed by the backend (V*|I|)."""
    rows = [reading(battery=battery, ts=T0 + timedelta(seconds=t), v=v, i=i, t=25.0, seq=k, boot_id=boot)
            for k, (t, i, v) in enumerate(samples)]
    for c in range(0, len(rows), 500):
        out = client.post("/api/battery/readings", json={"readings": rows[c:c + 500]}).json()
        assert out["rejected"] == 0, out
    return rows


def sessions(client, battery="HW-1"):
    return list(reversed(client.get(f"/api/battery/{battery}/sessions").json()["sessions"]))  # oldest first


def rest(t0, t1, step=2.0, v=3.95):
    n = int((t1 - t0) / step)
    return [(t0 + k * step, 0.0, v) for k in range(n)]


# ---------------------------------------------------------------- accuracy
def test_linear_ramp_matches_analytic_integral(client):
    # I falls linearly 0.5 -> 0.3 A over exactly 3600 s: Q = 0.4 A * 1 h = 400 mAh (trapezoid is exact)
    on = [(60 + t, 0.5 - 0.2 * t / 3600, 3.7) for t in range(0, 3601, 10)]
    post_series(client, rest(0, 60) + on + rest(3670, 3700))
    s = sessions(client)
    assert len(s) == 1
    x = s[0]
    assert x["status"] == "ended" and x["end_reason"] == "load_stopped"
    assert x["charge_mah"] == pytest.approx(400.0, rel=1e-9)
    assert x["energy_wh"] == pytest.approx(3.7 * 0.4, rel=1e-9)           # P = V*I, V constant
    assert x["integrated_s"] == pytest.approx(3600.0) and x["gap_s"] == 0
    assert x["duration_s"] == pytest.approx(3600.0)
    assert x["start_ts"].startswith((T0 + timedelta(seconds=60)).strftime("%Y-%m-%dT%H:%M:%S"))
    # load switched on within the 2 s before the first on-reading (<= 0.5 A * 2 s) and off within the
    # 10 s after the last (<= 0.3 A * 10 s): neither is integrated, both are bounded
    assert x["uncertainty_mah"] == pytest.approx(0.5 * 2 / 3.6 + 0.3 * 10 / 3.6)
    assert x["mean_current_a"] == pytest.approx(0.4)
    assert x["rest_voltage_before_v"] == 3.95 and x["start_voltage_v"] == 3.7


def test_sinusoidal_current_within_0p1_percent_of_analytic(client):
    # I(t) = 0.37 + 0.05 sin(2 pi t / 600) over 3 full periods: integral = 0.37 A * 1800 s = 185 mAh
    on = [(10 + t, 0.37 + 0.05 * math.sin(2 * math.pi * t / 600), 3.7) for t in range(0, 1801, 2)]
    post_series(client, rest(0, 10) + on + rest(1820, 1850))
    x = sessions(client)[0]
    assert x["charge_mah"] == pytest.approx(185.0, rel=1e-3)


# ---------------------------------------------------------------- gaps
def test_short_gap_is_not_filled_with_assumed_current(client, settings):
    assert settings.max_gap_s == 30
    a = [(t, 0.37, 3.8) for t in range(0, 101, 5)]
    b = [(t, 0.37, 3.7) for t in range(145, 246, 5)]            # 45 s without telemetry (> max_gap 30 s)
    post_series(client, a + b + rest(256, 280, v=3.85))
    s = sessions(client)
    assert len(s) == 1                                          # still one session
    x = s[0]
    assert x["charge_mah"] == pytest.approx(0.37 * 200 / 3.6)   # 100 s + 100 s, gap excluded
    assert x["gap_s"] == pytest.approx(45) and x["n_gaps"] == 1
    assert x["coverage"]["no_telemetry_gaps"] is False


def test_long_gap_ends_session_and_new_session_starts_from_zero(client):
    a = [(t, 0.37, 3.8) for t in range(0, 61, 5)]
    b = [(t, 0.37, 3.7) for t in range(460, 521, 5)]            # 400 s silence > session_end_gap 300 s
    post_series(client, a + b + rest(530, 560))
    s = sessions(client)
    assert [x["end_reason"] for x in s] == ["telemetry_gap", "load_stopped"]
    assert s[0]["charge_mah"] == pytest.approx(0.37 * 60 / 3.6)
    assert s[0]["end_ts"].startswith((T0 + timedelta(seconds=60)).strftime("%Y-%m-%dT%H:%M:%S"))
    assert s[1]["charge_mah"] == pytest.approx(0.37 * 60 / 3.6)  # reset: only the new session's charge


def test_silent_device_session_is_closed_and_kept(client):
    post_series(client, [(t, 0.37, 3.8) for t in range(0, 61, 5)])   # ends ~3 h ago, never stops
    client.get("/api/batteries")                                     # sweep closes stale sessions
    x = sessions(client)[0]
    assert x["status"] == "ended" and x["end_reason"] == "telemetry_lost"
    assert x["charge_mah"] == pytest.approx(0.37 * 60 / 3.6)


# ---------------------------------------------------------------- noise
def test_near_zero_noise_never_starts_a_session_or_accumulates(client):
    rng = random.Random(7)
    samples = [(2 * k, rng.uniform(-0.008, 0.008), 3.9) for k in range(400)]
    for k in (50, 120, 121 + 5, 300):                     # isolated single spikes above the on-threshold
        samples[k] = (samples[k][0], 0.05, 3.9)
    post_series(client, samples)
    assert sessions(client) == []


def test_charging_current_is_never_counted(client):
    post_series(client, [(t, -0.5, 4.0) for t in range(0, 121, 2)])
    assert sessions(client) == []


# ---------------------------------------------------------------- boundaries
def test_separate_loads_give_separate_sessions_and_counters_reset(client):
    load1 = [(t, 0.30, 3.8) for t in range(10, 71, 2)]
    load2 = [(t, 0.50, 3.7) for t in range(110, 171, 2)]
    post_series(client, rest(0, 10) + load1 + rest(72, 110) + load2 + rest(172, 200))
    s = sessions(client)
    assert len(s) == 2 and all(x["status"] == "ended" for x in s)
    assert s[0]["charge_mah"] == pytest.approx(0.30 * 60 / 3.6)
    assert s[1]["charge_mah"] == pytest.approx(0.50 * 60 / 3.6)
    assert s[0]["energy_wh"] == pytest.approx(0.30 * 3.8 * 60 / 3600)


def test_brief_dropout_shorter_than_stop_hold_keeps_one_session(client):
    samples = [(t, 0.37, 3.8) for t in range(0, 41, 2)] + [(42, 0.0, 3.85)] + [(t, 0.37, 3.8) for t in range(44, 81, 2)]
    post_series(client, samples + rest(82, 110))
    s = sessions(client)
    assert len(s) == 1
    # 40 s + 36 s integrated; the two 2 s intervals around the dropout are not, but bounded
    assert s[0]["charge_mah"] == pytest.approx(0.37 * 76 / 3.6)
    assert s[0]["uncertainty_mah"] == pytest.approx(0.37 * 2 / 3.6 * 3)   # dropout in + out + final stop


def test_single_on_reading_does_not_start_session(client):
    post_series(client, rest(0, 20) + [(20, 0.4, 3.8)] + rest(22, 60))
    assert sessions(client) == []


def test_out_of_order_reading_is_not_reintegrated(client):
    rows = post_series(client, [(t, 0.37, 3.8) for t in range(0, 21, 2)])
    before = sessions(client)[0]["charge_mah"]
    late = dict(rows[3], seq=999, current_a=5.0)          # arrives late with an older timestamp
    late["current_a"] = 2.0
    client.post("/api/battery/reading", json=late)
    assert sessions(client)[0]["charge_mah"] == pytest.approx(before)


def test_energy_not_integrated_without_power(settings):
    """Unit level: intervals lacking power on both ends are counted as missing, not assumed."""
    import sqlite3
    from iot.backend.db import Database
    db = Database(settings.db_path)
    with db.write() as conn:
        conn.execute("INSERT INTO batteries(battery_id, source, created_at) VALUES ('U','hardware','x')")
        prev = None
        for k in range(6):
            rec = {"ts_ms": k * 2000, "current_a": 0.4, "voltage_v": 3.8,
                   "power_w": None if k in (2, 3) else 1.52, "temperature_c": 25.0}
            update_session(conn, "U", rec, prev, settings)
            prev = rec
        row = conn.execute("SELECT charge_mah, energy_wh, energy_missing_s FROM discharge_sessions").fetchone()
    assert row["charge_mah"] == pytest.approx(0.4 * 10 / 3.6)
    assert row["energy_missing_s"] == pytest.approx(6.0)          # intervals 1-2, 2-3, 3-4
    assert row["energy_wh"] == pytest.approx(1.52 * 4 / 3600)


# ---------------------------------------------------------------- interpretation / health guard
def test_partial_session_is_never_presented_as_capacity_or_health(client):
    client.post("/api/battery/reading", json=reading(ts=T0, v=3.95, i=0.0, seq=0, boot_id="p"))
    client.put("/api/battery/HW-1", json={"rated_capacity_ah": 3.0, "nominal_voltage_v": 3.7})
    post_series(client, [(t, 0.37, 3.8 - t / 1000) for t in range(2, 603, 2)] + rest(604, 640, v=3.3), boot="q")
    b = client.get("/api/battery/HW-1").json()
    x = b["last_session"]
    assert x["kind"] == "partial" and x["coverage"]["started_from_full_rest_voltage"] is False
    assert "Not the cell's total capacity" in x["interpretation"]
    assert x["rated_capacity_mah"] == 3000                          # label shown separately, unchanged
    assert x["charge_mah"] == pytest.approx(0.37 * 600 / 3.6, rel=1e-6)
    # no health number appears because of a session
    assert b["latest_valid_prediction"] is None
    preds = client.get("/api/battery/HW-1/predictions").json()["predictions"]
    assert all(p["status"] == "unavailable" and p["soh_pct"] is None for p in preds)


def test_full_range_session_is_labelled_unvalidated(client):
    on = [(t, 0.37, 4.10 - (4.10 - 2.98) * (t - 10) / 1200) for t in range(10, 1211, 5)]
    post_series(client, rest(0, 10, step=2, v=4.18) + on + rest(1212, 1240, v=3.2))
    x = sessions(client)[0]
    assert x["coverage"]["started_from_full_rest_voltage"] and x["coverage"]["reached_cutoff_voltage"]
    assert x["kind"] == "full_range_unvalidated"
    assert "not a validated capacity measurement" in x["interpretation"]


def test_protection_thresholds_untouched(client):
    t = client.get("/api/settings/thresholds").json()
    assert (t["voltage_low_crit_v"], t["voltage_low_warn_v"], t["temp_high_crit_c"]) == (3.0, 3.2, 55.0)
