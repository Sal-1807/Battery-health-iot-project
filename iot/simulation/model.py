"""Synthetic INA219 + DS18B20 telemetry for controlled discharge sessions.

Everything produced here is SYNTHETIC and is labelled ``source="simulation"``.

Two kinds of signal:

* ``ideal``: exact, noiseless values (e.g. 1.000 A at 4.000 V) for analytic
  verification of the integration code.
* ``realistic``: an equivalent-circuit cell (generic Li-ion OCV curve, internal
  resistance, a given actual capacity, first-order thermal model) seen
  through sensor models: INA219 bus voltage 4 mV steps, current 0.1 mA steps,
  power 2 mW steps (32 V / 1 A calibration) plus small Gaussian noise; DS18B20
  12-bit 0.0625 degC steps. The cell's own exact coulomb count is kept as the
  reference ("truth") the backend's integration is compared against.

Readings are emitted in exactly the JSON shape the USB serial bridge forwards,
so they go through the real ingest path.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Iterator, Optional

from iot.simulator.simulate_esp32 import ocv

DEVICE_ID = "sim-esp32-01"
FIRMWARE_CLAIM = "SIM-FIRMWARE-ID"     # what the simulated firmware's config.h would send (ignored by routing)


@dataclass
class Cell:
    capacity_mah: float                  # ACTUAL capacity of the simulated cell (not the label)
    soc: float = 1.0
    r_int_ohm: float = 0.06
    ambient_c: float = 25.0
    temp_c: float = 25.0
    thermal_mass_j_per_c: float = 45.0
    h_w_per_c: float = 0.08
    truth_mah: float = 0.0               # exact charge drawn while discharging

    def ocv(self) -> float:
        return ocv(self.soc)

    def step(self, i_a: float, dt_s: float) -> tuple[float, float]:
        """Draw i_a (A, + = discharge) for dt_s; returns (terminal V at end of step, temperature)."""
        if i_a > 0:
            self.truth_mah += i_a * dt_s / 3.6
        self.soc = max(0.0, self.soc - i_a * dt_s / 3.6 / self.capacity_mah)
        heat = i_a * i_a * self.r_int_ohm
        self.temp_c += (heat - self.h_w_per_c * (self.temp_c - self.ambient_c)) * dt_s / self.thermal_mass_j_per_c
        return self.ocv() - i_a * self.r_int_ohm, self.temp_c


@dataclass
class Sensors:
    noise: bool = True
    seed: int = 1
    rng: random.Random = field(init=False)

    def __post_init__(self):
        self.rng = random.Random(self.seed)

    def bus_v(self, v: float) -> float:
        if not self.noise:
            return round(v, 4)
        return round(round((v + self.rng.gauss(0, 0.001)) / 0.004) * 0.004, 4)

    def current(self, i: float) -> float:
        if not self.noise:
            return round(i, 5)
        return round(round((i + self.rng.gauss(0, 0.0003)) / 0.0001) * 0.0001, 5)

    def power(self, v: float, i: float) -> float:
        p = abs(v * i)
        return round(p, 5) if not self.noise else round(round(p / 0.002) * 0.002, 4)

    def temp(self, t: float) -> float:
        if not self.noise:
            return round(t, 4)
        return round((t + self.rng.gauss(0, 0.02)) * 16) / 16


@dataclass
class Phase:
    """One part of a scripted session. load_a > 0 = constant-current discharge, 0 = rest."""
    duration_s: float
    load_a: float = 0.0
    stop_at_v: Optional[float] = None    # end the phase early when the loaded voltage reaches this
    fixed_v: Optional[float] = None      # ideal mode: report exactly this voltage


class Emitter:
    """Turns phases into firmware-shaped readings with a running seq number."""

    def __init__(self, sensors: Sensors, boot_id: str = "simboot01"):
        self.s, self.seq, self.boot_id = sensors, 0, boot_id

    def reading(self, ts: datetime, v: Optional[float], i: Optional[float], p: Optional[float], t: Optional[float],
                ina: str = "ok", ds: str = "ok") -> dict:
        self.seq += 1
        return {
            "battery_id": FIRMWARE_CLAIM, "device_id": DEVICE_ID, "source": "simulation", "transport": "usb_serial",
            "timestamp": ts.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "voltage_v": v, "current_a": i, "power_w": p, "temperature_c": t,
            "seq": self.seq, "boot_id": self.boot_id, "firmware_version": "simulation",
            "sensor_status": {"ina219": ina, "ds18b20": ds, "battery_connected": None},
        }

    def run(self, cell: Cell, phases: list[Phase], start: datetime, interval_s: float) -> Iterator[dict]:
        """Yield readings for the phases, sampled every interval_s; the cell advances between samples."""
        t = start
        for ph in phases:
            elapsed = 0.0
            while elapsed <= ph.duration_s + 1e-9:
                if ph.fixed_v is not None:
                    v_true, temp = ph.fixed_v, cell.temp_c
                else:
                    v_true, temp = cell.ocv() - ph.load_a * cell.r_int_ohm, cell.temp_c
                v, i = self.s.bus_v(v_true), self.s.current(ph.load_a)
                yield self.reading(t, v, i, self.s.power(v, i) if i else 0.0, self.s.temp(temp))
                if ph.stop_at_v is not None and v_true <= ph.stop_at_v:
                    break
                if elapsed + interval_s > ph.duration_s + 1e-9:
                    break
                cell.step(ph.load_a, interval_s)
                t += timedelta(seconds=interval_s)
                elapsed += interval_s
            t += timedelta(seconds=interval_s)
        self.last_ts = t
