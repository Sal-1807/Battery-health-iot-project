"""ESP32 / data-replay simulator for demos without hardware.

Every record this tool sends is labelled as non-hardware, so the dashboard can
never confuse it with a real measurement:

``telemetry``   Synthetic readings in the ESP32 JSON format, ``source="simulation"``.
                A simple equivalent-circuit cell (generic Li-ion OCV curve +
                internal resistance) discharging into a fixed resistor. It is a
                plausibility model for exercising the pipeline, NOT a model of
                your cell.
``nasa-replay`` Real cycle-level feature rows from
                data/processed/battery_health_features.csv posted to
                /api/battery/cycle (``source="nasa_replay"``), with the measured
                SoH/RUL attached as reference values.

Standard library only, so it runs without installing anything:

    python -m iot.simulator.simulate_esp32 telemetry --api http://localhost:8000
    python -m iot.simulator.simulate_esp32 nasa-replay --battery B0005
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterator, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
FEATURES_CSV = REPO_ROOT / "data" / "processed" / "battery_health_features.csv"

# Generic Li-ion (NMC/graphite) open-circuit-voltage curve, SoC -> V.
OCV_TABLE = [(0.0, 3.00), (0.05, 3.30), (0.10, 3.45), (0.20, 3.55), (0.30, 3.62), (0.40, 3.68),
             (0.50, 3.74), (0.60, 3.80), (0.70, 3.88), (0.80, 3.96), (0.90, 4.06), (1.00, 4.18)]

Poster = Callable[[str, dict], tuple[int, dict]]


def http_poster(api: str, timeout: float = 5.0) -> Poster:
    def post(path: str, body: dict) -> tuple[int, dict]:
        req = urllib.request.Request(api.rstrip("/") + path, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read() or b"{}")
            except json.JSONDecodeError:
                return e.code, {}
    return post


def ocv(soc: float) -> float:
    soc = min(max(soc, 0.0), 1.0)
    for (s0, v0), (s1, v1) in zip(OCV_TABLE, OCV_TABLE[1:]):
        if soc <= s1:
            return v0 + (v1 - v0) * (soc - s0) / (s1 - s0)
    return OCV_TABLE[-1][1]


@dataclass
class CellSim:
    capacity_ah: float = 3.0
    soc: float = 0.85
    r_int_ohm: float = 0.08
    load_ohm: float = 10.0
    ambient_c: float = 25.0
    temp_c: float = 25.0
    cutoff_v: float = 3.0
    charge_a: float = 0.5
    charge_to_v: float = 4.15
    thermal_mass_j_per_c: float = 45.0   # ~45 g cell
    h_w_per_c: float = 0.05              # convective loss

    def step(self, mode: str, dt_s: float) -> tuple[float, float]:
        """Advance dt_s seconds. Returns (terminal_voltage_v, current_a [+ = discharge])."""
        e = ocv(self.soc)
        if mode == "discharge":
            i = e / (self.load_ohm + self.r_int_ohm)
        elif mode == "charge":
            i = -self.charge_a
        else:
            i = 0.0
        v = e - i * self.r_int_ohm
        self.soc -= i * dt_s / 3600 / self.capacity_ah
        heat = i * i * self.r_int_ohm
        self.temp_c += (heat - self.h_w_per_c * (self.temp_c - self.ambient_c)) * dt_s / self.thermal_mass_j_per_c
        return v, i


def telemetry_stream(battery_id: str, device_id: str, interval_s: float, speedup: float, cycles: int,
                     rest_s: float, start: datetime, seed: int = 1, fault: Optional[str] = None,
                     cell: Optional[CellSim] = None) -> Iterator[dict]:
    """Yield ESP32-format readings. Wall-clock spacing is interval_s; simulated
    electrochemistry advances interval_s * speedup per reading."""
    rng = random.Random(seed)
    cell = cell or CellSim()
    boot_id = uuid.UUID(int=rng.getrandbits(128)).hex[:12]
    seq, t = 0, start
    for cyc in range(cycles):
        for mode in ("discharge", "rest", "charge", "rest"):
            if mode == "charge" and cyc == cycles - 1:
                break
            elapsed = 0.0
            while True:
                v, i = cell.step(mode, interval_s * speedup)
                elapsed += interval_s * speedup
                temp = round(cell.temp_c + rng.gauss(0, 0.03), 4)
                reading = {
                    "battery_id": battery_id, "device_id": device_id, "source": "simulation",
                    "timestamp": t.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                    "voltage_v": round(v + rng.gauss(0, 0.002), 4),
                    "current_a": round(i + rng.gauss(0, 0.001), 4),
                    "temperature_c": round(temp * 16) / 16,   # DS18B20 12-bit = 0.0625 degC steps
                    "seq": seq, "boot_id": boot_id, "firmware_version": "simulator",
                    "sensor_status": {"ina219": "ok", "ds18b20": "ok", "battery_connected": True},
                }
                reading["power_w"] = round(abs(reading["voltage_v"] * reading["current_a"]), 4)
                if fault == "temp-disconnect" and seq % 50 == 25:
                    reading["temperature_c"] = None
                    reading["sensor_status"]["ds18b20"] = "disconnected"
                yield reading
                seq += 1
                t += timedelta(seconds=interval_s)
                if mode == "discharge" and v <= cell.cutoff_v:
                    break
                if mode == "charge" and v >= cell.charge_to_v:
                    break
                if mode == "rest" and elapsed >= rest_s:
                    break


def run_telemetry(post: Poster, args) -> int:
    realtime = not args.backfill
    stream = telemetry_stream(args.battery, args.device, args.interval, args.speedup, args.cycles, args.rest_s,
                              datetime.now(timezone.utc), args.seed, args.fault)
    if not realtime:
        # Lay the whole run out so it ends ~2 tail windows ago (accepted, and all segments can close).
        readings = list(stream)
        span = timedelta(seconds=len(readings) * args.interval + 600)
        if span > timedelta(days=6):
            raise SystemExit("backfill run longer than 6 days; reduce --cycles or raise --speedup")
        shift = (datetime.now(timezone.utc) - span) - datetime.fromisoformat(readings[0]["timestamp"].replace("Z", "+00:00"))
        for r in readings:
            ts = datetime.fromisoformat(r["timestamp"].replace("Z", "+00:00")) + shift
            r["timestamp"] = ts.isoformat(timespec="milliseconds").replace("+00:00", "Z")
        stream = iter(readings)
    n_ok = n_err = 0
    for r in stream:
        if realtime:
            r["timestamp"] = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        status, body = _post_with_retry(post, "/api/battery/reading", r)
        if status == 200:
            n_ok += 1
            for c in body.get("cycles_closed", []):
                print(f"  cycle closed: segment {c['segment_index']} complete={c['complete']} "
                      f"prediction={c['prediction']['status']} {c['notes'] or ''}")
        else:
            n_err += 1
            print(f"  HTTP {status}: {body}", file=sys.stderr)
        if n_ok % 50 == 0:
            print(f"[{r['timestamp']}] seq={r['seq']} V={r['voltage_v']:.3f} I={r['current_a']:.3f} "
                  f"T={r['temperature_c']} sent={n_ok} errors={n_err}")
        if realtime:
            time.sleep(args.interval)
    # Close the final segment (the "device" stops talking after the last reading).
    status, body = _post_with_retry(post, f"/api/battery/{args.battery}/cycles/aggregate", {})
    for c in body.get("cycles_closed", []) if status == 200 else []:
        print(f"  cycle closed: segment {c['segment_index']} complete={c['complete']} "
              f"prediction={c['prediction']['status']} {c['notes'] or ''}")
    print(f"done: {n_ok} stored/duplicate, {n_err} failed")
    return 0 if n_err == 0 else 1


def _post_with_retry(post: Poster, path: str, body: dict, attempts: int = 4) -> tuple[int, dict]:
    delay = 0.5
    for k in range(attempts):
        try:
            status, resp = post(path, body)
            if status < 500:
                return status, resp
        except (urllib.error.URLError, OSError) as e:
            status, resp = 0, {"error": str(e)}
        if k < attempts - 1:
            time.sleep(delay)
            delay *= 2
    return status, resp


def nasa_rows(battery: str, csv_path: Path = FEATURES_CSV) -> list[dict]:
    with open(csv_path, newline="") as f:
        rows = [r for r in csv.DictReader(f) if r["Battery_ID"] == battery]
    if not rows:
        raise SystemExit(f"battery {battery} not in {csv_path}")
    return rows


def nasa_cycle_payload(row: dict, battery_id: str) -> dict:
    fl = lambda k: float(row[k])  # noqa: E731
    rul = row.get("RUL")
    return {
        "battery_id": battery_id, "source": "nasa_replay", "cycle_number": int(float(row["Cycle_number"])),
        "voltage_mean": fl("Voltage_mean"), "voltage_min": fl("Voltage_min"), "voltage_max": fl("Voltage_max"),
        "voltage_range": fl("Voltage_range"), "current_mean": fl("Current_mean"), "current_std": fl("Current_std"),
        "temperature_mean": fl("Temperature_mean"), "temperature_max": fl("Temperature_max"),
        "temperature_rise": fl("Temperature_rise"), "ref_soh": fl("SoH"),
        "ref_rul": float(rul) if rul not in (None, "", "nan") else None, "rated_capacity_ah": 2.0,
    }


def run_nasa_replay(post: Poster, args) -> int:
    battery_id = args.battery_id or f"NASA-{args.battery}"
    n_err = 0
    for row in nasa_rows(args.battery):
        status, body = _post_with_retry(post, "/api/battery/cycle", nasa_cycle_payload(row, battery_id))
        if status != 200:
            n_err += 1
            print(f"  HTTP {status}: {body}", file=sys.stderr)
        else:
            p = body.get("prediction") or {}
            print(f"cycle {row['Cycle_number']:>3}: {body['status']:<9} SoH ref={float(row['SoH']):6.2f}  "
                  f"model={p.get('soh_pct') if p.get('soh_pct') is None else round(p['soh_pct'], 2)} ({p.get('status')})")
        if args.delay:
            time.sleep(args.delay)
    return 0 if n_err == 0 else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--api", default="http://localhost:8000", help="backend base URL")
    sub = ap.add_subparsers(dest="mode", required=True)

    t = sub.add_parser("telemetry", help="synthetic ESP32 readings (source=simulation)")
    t.add_argument("--battery", default="SIM-18650-01")
    t.add_argument("--device", default="sim-esp32-01")
    t.add_argument("--interval", type=float, default=2.0, help="seconds between readings")
    t.add_argument("--speedup", type=float, default=60.0, help="simulated seconds per real second")
    t.add_argument("--cycles", type=int, default=1)
    t.add_argument("--rest-s", type=float, default=600.0, help="simulated rest after each phase")
    t.add_argument("--backfill", action="store_true", help="post as fast as possible with past timestamps")
    t.add_argument("--fault", choices=["temp-disconnect"], default=None)
    t.add_argument("--seed", type=int, default=1)

    n = sub.add_parser("nasa-replay", help="replay real NASA cycle features (source=nasa_replay)")
    n.add_argument("--battery", default="B0005", choices=["B0005", "B0006", "B0018"])
    n.add_argument("--battery-id", default=None, help="ID to store under (default NASA-<battery>)")
    n.add_argument("--delay", type=float, default=0.0, help="seconds between cycles")

    args = ap.parse_args(argv)
    post = http_poster(args.api)
    return run_telemetry(post, args) if args.mode == "telemetry" else run_nasa_replay(post, args)


if __name__ == "__main__":
    sys.exit(main())
