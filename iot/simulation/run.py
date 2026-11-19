"""One-command end-to-end simulation and verification.

    python -m iot.simulation.run                 # run all scenarios, print report, exit 1 on any failure
    python -m iot.simulation.run --fixture dashboard/src/__tests__/fixtures/simulation.json

What it does
------------
* Creates a SEPARATE simulation database (default iot/data/simulation.db,
  reset each run) and an in-process backend in SIMULATION mode (BHM_MODE).
  It refuses to touch the production database.
* Drives the REAL HTTP API (FastAPI TestClient against the real app): every
  reading goes through /api/battery/reading, the same validation, routing,
  presence, session, alert and cycle code as real telemetry. Battery switches
  and Test Manager transitions call the same functions the API does, with an
  explicit timestamp so a 3-day scenario runs in seconds.
* Uses the SIM-BAT-A-3000 / SIM-BAT-B-3000 / SIM-BAT-C-2500 profiles
  (source = simulation). Nothing is attributed to a real profile.
* Verifies results against analytically known values and against the
  simulated cell's own exact coulomb count, and writes a JSON report.

Afterwards, view the results in the dashboard:

    BHM_MODE=simulation BHM_DB_PATH=iot/data/simulation.db uvicorn iot.backend.main:app --port 8000
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import hashlib
import io
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi.testclient import TestClient

from iot.backend import battery_tests as bt
from iot.backend.api import create_app
from iot.backend.assignments import switch_assignment
from iot.backend.config import REPO_ROOT, Settings

from .model import DEVICE_ID, Cell, Emitter, Phase, Sensors

DEFAULT_DB = REPO_ROOT / "iot" / "data" / "simulation.db"
PRODUCTION_DB = Settings().db_path
A, B, C = "SIM-BAT-A-3000", "SIM-BAT-B-3000", "SIM-BAT-C-2500"
CHECKLIST = {"battery_identity_confirmed": True, "load_rating_verified": True, "supervisor_approval": True,
             "disconnect_procedure_confirmed": True, "voltage_cutoff_confirmed": True,
             "supervisor_name": "SIMULATION (no supervisor - synthetic run)", "cutoff_v": 3.0}


def _ms(t: datetime) -> int:
    return int(t.timestamp() * 1000)


def _sha(p: Path) -> Optional[str]:
    if not p.exists():
        return None
    h = hashlib.sha256()
    for name in (p, Path(str(p) + "-wal")):
        if name.exists():
            h.update(name.read_bytes())
    return h.hexdigest()


class Runner:
    def __init__(self, db_path: Path, quiet: bool = False):
        db_path = Path(db_path).resolve()
        if db_path == Path(PRODUCTION_DB).resolve():
            raise SystemExit(f"refusing to run the simulation against the production database {db_path}")
        for f in (db_path, Path(str(db_path) + "-wal"), Path(str(db_path) + "-shm")):
            f.unlink(missing_ok=True)
        self.prod_hash_before = _sha(Path(PRODUCTION_DB))
        self.settings = dataclasses.replace(Settings(), db_path=db_path, mode="simulation")
        self.app = create_app(self.settings)
        self.c = TestClient(self.app)
        self.quiet = quiet
        self.checks: list[dict[str, Any]] = []
        self.facts: dict[str, Any] = {}
        self.posted = 0

    # ---------------------------------------------------------------- plumbing
    def log(self, msg: str) -> None:
        if not self.quiet:
            print(msg, flush=True)

    def check(self, scenario: str, name: str, ok: bool, expected: Any = None, actual: Any = None) -> None:
        self.checks.append({"scenario": scenario, "check": name, "ok": bool(ok), "expected": expected, "actual": actual})
        if not self.quiet:
            print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f"  expected={expected!r} actual={actual!r}"))

    def post(self, r: dict) -> dict:
        resp = self.c.post("/api/battery/reading", json=r)
        self.posted += 1
        if resp.status_code != 200:
            raise RuntimeError(f"ingest rejected a simulated reading: {resp.status_code} {resp.text}")
        return resp.json()

    def send(self, readings) -> list[dict]:
        return [self.post(r) for r in readings]

    def assign(self, battery: Optional[str], at: datetime) -> dict:
        with self.app.state.db.write() as conn:
            return switch_assignment(conn, DEVICE_ID, battery, "simulation", self.settings, now_ms=_ms(at))

    def new_test(self, battery: str, at: datetime, load_ohm: float, rating_w: float) -> str:
        uid = self.c.post("/api/tests", json={"battery_id": battery, "notes": "SIMULATION",
                                              "kind": "supervised"}).json()["test_uid"]
        r = self.c.put(f"/api/tests/{uid}/checklist",
                       json={**CHECKLIST, "load_resistance_ohm": load_ohm, "load_power_rating_w": rating_w}).json()
        assert r["state"] == "ready", r
        self.act(uid, "start", at)
        return uid

    def act(self, uid: str, action: str, at: datetime, reason: Optional[str] = None) -> dict:
        with self.app.state.db.write() as conn:
            return bt.transition(conn, uid, action, reason, self.settings, now_ms=_ms(at))

    def sessions(self, battery: str) -> list[dict]:
        return list(reversed(self.c.get(f"/api/battery/{battery}/sessions").json()["sessions"]))

    def stats(self, battery: str) -> dict:
        return self.c.get(f"/api/battery/{battery}/stats").json()["stats"]

    def test_view(self, uid: str) -> dict:
        return self.c.get(f"/api/tests/{uid}").json()

    # ---------------------------------------------------------------- scenarios
    def s1_analytic(self, t0: datetime) -> datetime:
        """1.000 A at 4.000 V for exactly 1 h: 1000 mAh and 4.000 Wh, with dropped,
        duplicated and out-of-order deliveries mixed in."""
        sc = "S1 analytic integration"
        self.log(f"\n{sc} ({A})")
        self.assign(A, t0)
        uid = self.new_test(A, t0 + timedelta(seconds=5), load_ohm=4.0, rating_w=10.0)
        em = Emitter(Sensors(noise=False), boot_id="sim-s1")
        cell = Cell(capacity_mah=2900, soc=0.9)
        rd = list(em.run(cell, [Phase(30, 0.0, fixed_v=4.0), Phase(3600, 1.0, fixed_v=4.0), Phase(30, 0.0, fixed_v=4.0)],
                         t0 + timedelta(seconds=20), interval_s=2.0))
        dropped = {300, 301, 302}
        dup_idx = {100, 500, 900}
        late_idx = 1200
        statuses, held = [], None
        for k, r in enumerate(rd):
            if k in dropped:
                continue
            if k == late_idx:
                held = r
                continue
            statuses.append(self.post(r)["status"])
            if k in dup_idx:
                statuses.append(("dup", self.post(r)["status"]))
            if k == late_idx + 10 and held is not None:
                statuses.append(("late", self.post(held)["status"]))
        end = em.last_ts
        done = self.act(uid, "complete", end + timedelta(seconds=10))
        s = [x for x in self.sessions(A)][-1]
        v = self.test_view(uid)
        self.check(sc, "capacity: 1.0 A x 1 h = 1000 mAh (+/-0.5 %)", abs(s["charge_mah"] - 1000.0) <= 5.0, 1000.0, s["charge_mah"])
        self.check(sc, "energy: 4.0 V x 1.0 A x 1 h = 4.000 Wh (+/-0.5 %)", abs(s["energy_wh"] - 4.0) <= 0.02, 4.0, s["energy_wh"])
        self.check(sc, "session duration 3600 s", abs(s["duration_s"] - 3600) <= 2, 3600, s["duration_s"])
        self.check(sc, "load on/off transitions bounded, not integrated", s["uncertainty_mah"] == 1.0 * 2 / 3.6 * 2,
                   round(1.0 * 2 / 3.6 * 2, 6), round(s["uncertainty_mah"], 6))
        self.check(sc, "3 duplicate deliveries ignored", [x for x in statuses if isinstance(x, tuple) and x[0] == "dup"]
                   == [("dup", "duplicate")] * 3, 3, statuses.count(("dup", "duplicate")))
        self.check(sc, "out-of-order reading stored but not re-integrated", ("late", "stored") in statuses
                   and abs(s["charge_mah"] - 1000.0) < 1e-6, 1000.0, s["charge_mah"])
        self.check(sc, "3 dropped readings: no current assumed, no gap (within 60 s)", s["n_gaps"] == 0, 0, s["n_gaps"])
        self.check(sc, "Test Manager result = session result", abs(v["results"]["delivered_mah"] - s["charge_mah"]) < 1e-6
                   and abs(v["results"]["delivered_wh"] - s["energy_wh"]) < 1e-6,
                   s["charge_mah"], v["results"]["delivered_mah"])
        self.check(sc, "partial discharge (4.0 V start, never reached cutoff) is not a full cycle",
                   v["validity"]["classification"] == "partial discharge (not a full cycle)" and s["kind"] == "partial",
                   "partial", v["validity"]["classification"])
        self.check(sc, "stopping the software test claims no hardware action",
                   "did not connect, disconnect" in done["hardware_notice"] and "Physically disconnect" in done["warning"],
                   True, done)
        st = self.stats(A)
        self.check(sc, "EFC uses SIM-BAT-A's 3000 mAh rating", abs(st["equivalent_full_cycles"] - s["charge_mah"] / 3000) < 1e-9,
                   s["charge_mah"] / 3000, st["equivalent_full_cycles"])
        self.check(sc, "no completed cycle from a partial discharge", st["completed_cycles"] == 0, 0, st["completed_cycles"])
        self.facts["analytic"] = {"charge_mah": s["charge_mah"], "energy_wh": s["energy_wh"], "test_uid": uid,
                                  "efc": st["equivalent_full_cycles"]}
        return end + timedelta(minutes=10)

    def _full_run(self, cell: Cell, start: datetime, seed: int) -> tuple[list[dict], datetime]:
        em = Emitter(Sensors(noise=True, seed=seed), boot_id=f"sim-c{seed}")
        rd = list(em.run(cell, [Phase(120, 0.0), Phase(4 * 3600, 1.2, stop_at_v=3.0), Phase(300, 0.0)], start, 10.0))
        self.send(rd)
        return rd, em.last_ts

    def s2_full_cycles(self, t1: datetime) -> datetime:
        """Two realistic full discharges of SIM-BAT-C-2500 (actual capacity 2400 mAh, then 2% fade)."""
        sc = "S2 realistic full discharges"
        self.log(f"\n{sc} ({C})")
        self.assign(C, t1)
        truths = []
        uid = None
        t = t1 + timedelta(minutes=1)
        for run, cap in enumerate((2400.0, 2400.0 * 0.98), start=1):
            cell = Cell(capacity_mah=cap, soc=1.0)
            if run == 1:
                uid = self.new_test(C, t - timedelta(seconds=5), load_ohm=3.0, rating_w=10.0)
            _, end = self._full_run(cell, t, seed=10 + run)
            if run == 1:
                self.act(uid, "complete", end + timedelta(seconds=5))
            truths.append(cell.truth_mah)
            t = end + timedelta(hours=2)            # (re)charging is not instrumented; next run starts rested at full
        ss = self.sessions(C)
        full = [x for x in ss if x["kind"] == "full_range_unvalidated"]
        self.check(sc, "both runs recognised as full discharges", len(full) == 2, 2, len(full))
        for k, (x, truth) in enumerate(zip(full, truths), start=1):
            self.check(sc, f"run {k}: integrated charge within 1 % of the cell's exact coulomb count",
                       abs(x["charge_mah"] - truth) / truth <= 0.01, round(truth, 1), round(x["charge_mah"], 1))
        st = self.stats(C)
        self.check(sc, "2 completed cycles", st["completed_cycles"] == 2, 2, st["completed_cycles"])
        self.check(sc, "EFC uses SIM-BAT-C's 2500 mAh rating",
                   abs(st["equivalent_full_cycles"] - st["total_discharged_mah"] / 2500) < 1e-9,
                   st["total_discharged_mah"] / 2500, st["equivalent_full_cycles"])
        v = self.test_view(uid)
        self.check(sc, "Test Manager: valid for full-capacity analysis", v["validity"]["valid_for_full_capacity"],
                   True, [c for c in v["validity"]["checks"] if not c["ok"]])
        ag = self.c.get(f"/api/aging/{C}").json()
        expected_ret = 100 * full[1]["charge_mah"] / full[0]["charge_mah"]
        self.check(sc, "aging retention = run 2 / run 1 (measured reference, not label)",
                   ag["retention"]["status"] == "ok" and abs(ag["retention"]["latest_pct"] - expected_ret) < 1e-9
                   and ag["reference"]["capacity_mah"] == full[0]["charge_mah"],
                   round(expected_ret, 3), ag["retention"]["latest_pct"])
        self.check(sc, "simulated 2 % fade visible in retention (+/-1 pt)", abs(ag["retention"]["latest_pct"] - 98.0) <= 1.0,
                   98.0, ag["retention"]["latest_pct"])
        self.facts["full"] = {"truth_mah": truths, "measured_mah": [x["charge_mah"] for x in full],
                              "retention_pct": ag["retention"]["latest_pct"], "test_uid": uid}
        return t

    def s3_partial_and_switch(self, t2: datetime) -> datetime:
        sc = "S3 partial discharge + battery switch mid-session"
        self.log(f"\n{sc} ({B} -> {A})")
        self.assign(B, t2)
        em = Emitter(Sensors(noise=True, seed=31), boot_id="sim-s3")
        cell_b = Cell(capacity_mah=2900, soc=0.75)
        self.send(em.run(cell_b, [Phase(60, 0.0), Phase(1800, 0.5), Phase(60, 0.0)], t2 + timedelta(minutes=1), 5.0))
        truth_partial = cell_b.truth_mah
        t = em.last_ts + timedelta(minutes=10)
        before = cell_b.truth_mah
        self.send(em.run(cell_b, [Phase(600, 0.5)], t, 5.0))            # load on B ...
        truth_b2 = cell_b.truth_mah - before
        switch_at = em.last_ts + timedelta(seconds=2)
        self.assign(A, switch_at)                                       # ... operator swaps to A mid-discharge
        cell_a = Cell(capacity_mah=2950, soc=0.9)
        self.send(em.run(cell_a, [Phase(600, 0.5), Phase(60, 0.0)], switch_at + timedelta(seconds=3), 5.0))
        sb = self.sessions(B)
        self.check(sc, "partial session classified partial", sb[0]["kind"] == "partial", "partial", sb[0]["kind"])
        self.check(sc, "partial charge within 1 % of truth", abs(sb[0]["charge_mah"] - truth_partial) / truth_partial <= 0.01,
                   round(truth_partial, 2), round(sb[0]["charge_mah"], 2))
        self.check(sc, "B session ended by the switch", sb[-1]["end_reason"] == "battery_switched", "battery_switched", sb[-1]["end_reason"])
        self.check(sc, "B keeps only its own charge", abs(sb[-1]["charge_mah"] - truth_b2) / truth_b2 <= 0.02,
                   round(truth_b2, 2), round(sb[-1]["charge_mah"], 2))
        sa = self.sessions(A)[-1]
        self.check(sc, "A's post-switch readings form A's own session", sa["start_ms"] > _ms(switch_at)
                   and abs(sa["charge_mah"] - cell_a.truth_mah) / cell_a.truth_mah <= 0.02,
                   round(cell_a.truth_mah, 2), round(sa["charge_mah"], 2))
        hb = self.c.get(f"/api/battery/{B}/history", params={"start": switch_at.isoformat()}).json()["total"]
        self.check(sc, "no reading after the switch stored under B", hb == 0, 0, hb)
        st = self.stats(B)
        self.check(sc, "B: no completed cycle; EFC = charge / 3000", st["completed_cycles"] == 0
                   and abs(st["equivalent_full_cycles"] - st["total_discharged_mah"] / 3000) < 1e-9,
                   0, st["completed_cycles"])
        return em.last_ts + timedelta(minutes=10)

    def s4_interruptions(self, t3: datetime) -> datetime:
        sc = "S4 telemetry gaps, missing readings, interrupted session"
        self.log(f"\n{sc} ({A})")
        em = Emitter(Sensors(noise=False), boot_id="sim-s4")
        uid = self.new_test(A, t3 - timedelta(seconds=20), load_ohm=8.0, rating_w=5.0)
        rd = list(em.run(Cell(capacity_mah=2950, soc=0.8), [Phase(20, 0.0, fixed_v=3.9), Phase(600, 0.5, fixed_v=3.8)],
                         t3, 5.0))
        for k in range(40, 45):                                          # DS18B20 dropout
            rd[k]["temperature_c"], rd[k]["sensor_status"]["ds18b20"] = None, "disconnected"
        rd[60]["voltage_v"] = rd[60]["current_a"] = rd[60]["power_w"] = None      # INA219 fault
        rd[60]["sensor_status"]["ina219"] = "error"
        self.send(rd)
        t = em.last_ts + timedelta(seconds=90)                           # 90 s telemetry gap (> 60 s)
        self.send(em.run(Cell(capacity_mah=2950, soc=0.7), [Phase(600, 0.5, fixed_v=3.75)], t, 5.0))
        t = em.last_ts + timedelta(minutes=10)                           # 10 min silence (> 5 min): interrupted
        self.send(em.run(Cell(capacity_mah=2950, soc=0.6), [Phase(300, 0.5, fixed_v=3.7), Phase(30, 0.0, fixed_v=3.8)], t, 5.0))
        self.act(uid, "complete", em.last_ts + timedelta(seconds=5))
        ss = [x for x in self.sessions(A) if x["start_ms"] >= _ms(t3)]
        first = ss[0]
        self.check(sc, "first session interrupted by the 10 min silence", first["end_reason"] == "telemetry_gap",
                   "telemetry_gap", first["end_reason"])
        self.check(sc, "90 s gap recorded, not filled", first["n_gaps"] == 1 and abs(first["gap_s"] - 95) < 1e-6,
                   95.0, first["gap_s"])
        # 1190 s of load minus the gap interval and the two 5 s intervals around the INA219 fault
        expected = 0.5 * (600 - 10 + 600) / 3.6
        self.check(sc, "charge counts only measured intervals", abs(first["charge_mah"] - expected) < 1e-6,
                   round(expected, 4), round(first["charge_mah"], 4))
        self.check(sc, "a new session after the interruption, counted from zero",
                   len(ss) >= 2 and abs(ss[1]["charge_mah"] - 0.5 * 300 / 3.6) < 1e-6, round(0.5 * 300 / 3.6, 4),
                   ss[1]["charge_mah"] if len(ss) > 1 else None)
        v = self.test_view(uid)
        r = v["results"]
        self.check(sc, "Test Manager flags the gaps", len(r["gaps"]) == 2, 2, len(r["gaps"]))
        # 5 DS18B20 dropouts + 1 INA219 fault (no voltage/current, temperature still present)
        want = {"voltage_v": 1, "current_a": 1, "temperature_c": 5, "readings_with_any_missing": 6}
        self.check(sc, "Test Manager counts missing measurements", r["missing_measurements"] == want,
                   want, r["missing_measurements"])
        self.check(sc, "interrupted test is not valid for capacity analysis", not v["validity"]["valid_for_full_capacity"],
                   False, v["validity"]["classification"])
        ag = self.c.get(f"/api/aging/{A}").json()
        reasons = [i for x in ag["sessions"] for i in x["full_test_issues"]]
        self.check(sc, "aging excludes gap / interrupted sessions with reasons",
                   any("telemetry gap" in i for i in reasons) and ag["n_full_tests"] == 0, 0, ag["n_full_tests"])
        return em.last_ts

    def s5_live_stop(self) -> None:
        """Software 'complete' while simulated load current is still flowing (timestamps near real time)."""
        sc = "S5 stopping a software test does not imply disconnection"
        self.log(f"\n{sc} ({A})")
        now = datetime.now(timezone.utc).replace(microsecond=0)
        start = now - timedelta(seconds=300)
        uid = self.new_test(A, start - timedelta(seconds=5), load_ohm=8.0, rating_w=5.0)
        em = Emitter(Sensors(noise=True, seed=55), boot_id="sim-s5")
        cell = Cell(capacity_mah=2950, soc=0.6)
        rd = list(em.run(cell, [Phase(290, 0.45)], start, 5.0))
        self.send(rd[:30])
        done = self.act(uid, "complete", start + timedelta(seconds=150))
        self.send(rd[30:])                                               # load still on after "complete"
        v = self.test_view(uid)
        self.check(sc, "transition response states it controlled no hardware",
                   "did not connect, disconnect or switch" in done["hardware_notice"], True, done["hardware_notice"])
        self.check(sc, "response tells the operator to disconnect physically",
                   "Physically disconnect the discharge load" in done.get("warning", ""), True, done.get("warning"))
        self.check(sc, "measured hardware state still shows load current after the software stop",
                   v["state"] == "completed" and "load current" in v["hardware_state"]["description"],
                   "load current ... appears to be connected", v["hardware_state"]["description"])

    def s6_isolation(self) -> None:
        sc = "S6 isolation from real data"
        self.log(f"\n{sc}")
        hw = {"battery_id": "CELL-18650-01", "device_id": "esp32-devkit-01", "source": "hardware",
              "voltage_v": 3.9, "current_a": 0.0, "temperature_c": 25.0}
        r = self.c.post("/api/battery/reading", json=hw)
        self.check(sc, "hardware telemetry refused by the simulation backend", r.status_code == 403, 403, r.status_code)
        for real in ("BAT-A-3000", "BAT-B-3000", "BAT-C-2500"):
            n = self.c.get(f"/api/battery/{real}/history").json()["total"]
            ns = len(self.c.get(f"/api/battery/{real}/sessions").json()["sessions"])
            self.check(sc, f"real profile {real} has no simulated readings or sessions", n == 0 and ns == 0, 0, (n, ns))
        cmp = self.c.get("/api/batteries/compare").json()["batteries"]
        self.check(sc, "profile views list only SIM-* profiles", sorted(b["battery_id"] for b in cmp) == [A, B, C]
                   and all(b["source"] == "simulation" for b in cmp), [A, B, C], [b["battery_id"] for b in cmp])
        self.check(sc, "SoH/RUL unavailable for every simulated profile", all(b["soh"] == "unavailable" for b in cmp), True,
                   [b["soh"] for b in cmp])
        preds = [p for bid in (A, B, C) for p in self.c.get(f"/api/battery/{bid}/predictions").json()["predictions"]]
        self.check(sc, "no SoH/RUL prediction produced", all(p["status"] == "unavailable" and p["soh_pct"] is None
                                                              for p in preds), 0,
                   sum(1 for p in preds if p["status"] != "unavailable"))
        health = self.c.get("/api/health").json()
        self.check(sc, "backend reports SIMULATION mode", health["mode"] == "simulation", "simulation", health["mode"])
        self.check(sc, "production database untouched", _sha(Path(PRODUCTION_DB)) == self.prod_hash_before,
                   self.prod_hash_before, _sha(Path(PRODUCTION_DB)))

    def s7_exports(self) -> None:
        sc = "S7 exports"
        self.log(f"\n{sc}")
        tests = self.c.get("/api/tests").json()["tests"]
        rows = list(csv.DictReader(io.StringIO(self.c.get("/api/tests/export.csv").text)))
        by = {r["test_uid"]: r for r in rows}
        self.check(sc, "tests CSV has one row per test", set(by) == {t["test_uid"] for t in tests}, len(tests), len(rows))
        ok = all(abs(float(by[t["test_uid"]]["delivered_mah"]) - t["results"]["delivered_mah"]) < 1e-6
                 for t in tests if t["results"].get("available"))
        self.check(sc, "tests CSV delivered mAh = API values", ok, True, ok)
        for bid in (A, B, C):
            total = self.c.get(f"/api/battery/{bid}/history").json()["total"]
            lines = self.c.get(f"/api/battery/{bid}/history", params={"format": "csv", "limit": 50000}).text.strip().splitlines()
            self.check(sc, f"{bid} history CSV rows = stored readings", len(lines) - 1 == total, total, len(lines) - 1)

    # ---------------------------------------------------------------- run
    def run_all(self) -> dict:
        t_start = time.time()
        t0 = (datetime.now(timezone.utc) - timedelta(days=3)).replace(microsecond=0)
        t = self.s1_analytic(t0)
        t = self.s2_full_cycles(t)
        t = self.s3_partial_and_switch(t)
        self.s4_interruptions(t)
        self.s5_live_stop()
        self.s6_isolation()
        self.s7_exports()
        failed = [c for c in self.checks if not c["ok"]]
        return {"passed": len(self.checks) - len(failed), "failed": len(failed), "checks": self.checks,
                "facts": self.facts, "readings_posted": self.posted, "seconds": round(time.time() - t_start, 1),
                "database": str(self.settings.db_path)}

    def fixture(self) -> dict:
        """API responses captured from this run, for the dashboard tests."""
        get = lambda p: self.c.get(p).json()  # noqa: E731
        tests = get("/api/tests")
        return {
            "_note": "Generated by python -m iot.simulation.run --fixture. Synthetic data from the simulation database.",
            "facts": self.facts,
            "/api/health": get("/api/health"),
            "/api/batteries": get("/api/batteries"),
            "/api/batteries/compare": get("/api/batteries/compare"),
            "/api/devices": get("/api/devices"),
            "/api/aging": get("/api/aging"),
            "/api/quick-tests": get("/api/quick-tests"),
            **{f"/api/aging/{b}": get(f"/api/aging/{b}") for b in (A, B, C)},
            "/api/tests": tests,
            **{f"/api/tests/{t['test_uid']}": t for t in tests["tests"]},
            "/api/settings/thresholds": get("/api/settings/thresholds"),
        }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=str(DEFAULT_DB), help="simulation database (reset each run)")
    ap.add_argument("--report", default=str(REPO_ROOT / "iot" / "data" / "simulation_report.json"))
    ap.add_argument("--fixture", default=None, help="also write dashboard test fixture JSON here")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    runner = Runner(Path(args.db), quiet=args.quiet)
    print(f"SIMULATION - synthetic data only. Database: {runner.settings.db_path}")
    rep = runner.run_all()
    Path(args.report).write_text(json.dumps(rep, indent=2, default=str))
    if args.fixture:
        Path(args.fixture).parent.mkdir(parents=True, exist_ok=True)
        Path(args.fixture).write_text(json.dumps(runner.fixture(), indent=1, default=str))
    print(f"\n{rep['passed']} checks passed, {rep['failed']} failed - {rep['readings_posted']} simulated readings "
          f"through the real ingest API in {rep['seconds']} s. Report: {args.report}")
    if rep["failed"]:
        for c in rep["checks"]:
            if not c["ok"]:
                print(f"FAILED: {c['scenario']}: {c['check']} (expected {c['expected']!r}, got {c['actual']!r})")
    print(f"View it: BHM_MODE=simulation BHM_DB_PATH={runner.settings.db_path} uvicorn iot.backend.main:app --port 8000")
    return 0 if rep["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
