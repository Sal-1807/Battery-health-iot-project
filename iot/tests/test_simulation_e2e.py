"""End-to-end simulation through the real API (see iot/simulation/run.py).

Runs the full scenario set against a temporary simulation database and asserts
that every verification check passes, plus the headline analytic numbers.
"""

from __future__ import annotations

import pytest

from iot.simulation.run import PRODUCTION_DB, Runner


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    r = Runner(tmp_path_factory.mktemp("sim") / "simulation.db", quiet=True)
    rep = r.run_all()
    rep["fixture"] = r.fixture()
    return rep


def test_every_simulation_check_passes(report):
    failed = [f"{c['scenario']}: {c['check']} (expected {c['expected']!r}, got {c['actual']!r})"
              for c in report["checks"] if not c["ok"]]
    assert not failed, "\n".join(failed)
    assert report["passed"] >= 50 and report["readings_posted"] > 4000


def test_analytic_capacity_and_energy(report):
    a = report["facts"]["analytic"]
    assert a["charge_mah"] == pytest.approx(1000.0, abs=5.0)       # 1.0 A x 1 h
    assert a["energy_wh"] == pytest.approx(4.0, abs=0.02)          # 4.0 V x 1.0 A x 1 h
    assert a["efc"] == pytest.approx(a["charge_mah"] / 3000)


def test_full_discharges_match_cell_truth(report):
    f = report["facts"]["full"]
    for truth, measured in zip(f["truth_mah"], f["measured_mah"]):
        assert measured == pytest.approx(truth, rel=0.01)
    assert f["retention_pct"] == pytest.approx(98.0, abs=1.0)


def test_fixture_shows_only_simulated_profiles(report):
    fx = report["fixture"]
    assert fx["/api/health"]["mode"] == "simulation"
    assert {b["battery_id"] for b in fx["/api/batteries/compare"]["batteries"]} == {
        "SIM-BAT-A-3000", "SIM-BAT-B-3000", "SIM-BAT-C-2500"}
    assert all(b["source"] == "simulation" for b in fx["/api/aging"]["batteries"])


def test_runner_refuses_production_database():
    with pytest.raises(SystemExit):
        Runner(PRODUCTION_DB, quiet=True)
