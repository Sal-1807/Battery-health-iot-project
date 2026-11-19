"""Request/response models. Units are part of every field name on purpose."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

ID_PATTERN = r"^[A-Za-z0-9_.\-]{1,48}$"

ReadingSource = Literal["hardware", "simulation"]


class SensorStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ina219: Literal["ok", "error", "not_found"] | None = None
    ds18b20: Literal["ok", "disconnected", "not_found", "error"] | None = None
    # Legacy field: older firmware sent its own voltage-threshold guess here. It
    # is stored for diagnostics only and NEVER used as evidence of a cell
    # (a floating INA219 input can exceed any such threshold). See presence.py.
    battery_connected: Optional[bool] = None


class ReadingIn(BaseModel):
    """One telemetry sample from an ESP32 (or the simulator).

    Units: volts, amperes, degrees Celsius, watts. ``current_a`` is POSITIVE
    while the battery is DISCHARGING (INA219 with battery + on VIN+ and the load
    on VIN-). Legacy field names ``voltage``/``current``/``temperature``/``power``
    are accepted as aliases but must still be in V/A/degC/W - values that look
    like mV/mA/mW are rejected by the range checks rather than silently scaled.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    battery_id: str = Field(pattern=ID_PATTERN)
    device_id: Optional[str] = Field(default=None, pattern=ID_PATTERN)
    timestamp: Optional[datetime] = Field(
        default=None, description="UTC, ISO-8601 with offset. Omit if the device has no synced clock.")
    voltage_v: Optional[float] = Field(
        default=None, ge=0.0, le=5.5, validation_alias=AliasChoices("voltage_v", "voltage"))
    current_a: Optional[float] = Field(
        default=None, ge=-5.0, le=5.0, validation_alias=AliasChoices("current_a", "current"))
    temperature_c: Optional[float] = Field(
        default=None, ge=-127.0, le=125.0, validation_alias=AliasChoices("temperature_c", "temperature"))
    power_w: Optional[float] = Field(
        default=None, ge=0.0, le=25.0, validation_alias=AliasChoices("power_w", "power"))
    seq: Optional[int] = Field(default=None, ge=0, description="Per-boot message counter (dedup key).")
    boot_id: Optional[str] = Field(default=None, pattern=ID_PATTERN)
    uptime_ms: Optional[int] = Field(default=None, ge=0)
    rssi_dbm: Optional[int] = Field(default=None, ge=-127, le=0)
    firmware_version: Optional[str] = Field(default=None, max_length=32)
    sensor_status: Optional[SensorStatus] = None
    source: ReadingSource = "hardware"
    transport: Optional[Literal["wifi", "usb_serial"]] = Field(
        default=None, description="How the reading reached the backend. usb_serial = via iot/bridge/serial_bridge.py, "
                                  "whose PC clock supplies the timestamp.")

    @field_validator("timestamp")
    @classmethod
    def _tz_required(cls, v: Optional[datetime]) -> Optional[datetime]:
        if v is not None and (v.tzinfo is None or v.utcoffset() is None):
            raise ValueError("timestamp must include a UTC offset, e.g. 2026-01-01T12:00:00Z")
        return v

    @field_validator("voltage_v", "current_a", "temperature_c", "power_w")
    @classmethod
    def _finite(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and v != v:  # NaN
            raise ValueError("NaN is not allowed; send null for a missing value")
        return v

    @model_validator(mode="after")
    def _something_measured(self) -> "ReadingIn":
        if all(x is None for x in (self.voltage_v, self.current_a, self.temperature_c)):
            if self.sensor_status is None:
                raise ValueError("reading has no measurements and no sensor_status")
        return self


class ReadingBatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    readings: list[ReadingIn] = Field(min_length=1, max_length=500)


class CycleIn(BaseModel):
    """A cycle-level feature vector posted directly (NASA replay / offline tools).

    Feature names and units match ``data/processed/battery_health_features.csv``:
    Current_mean is NEGATIVE for discharge (NASA convention).
    """

    model_config = ConfigDict(extra="forbid")

    battery_id: str = Field(pattern=ID_PATTERN)
    source: Literal["nasa_replay", "manual"] = "nasa_replay"
    cycle_number: int = Field(ge=1, le=100000)
    voltage_mean: float = Field(ge=0, le=5.5)
    voltage_min: float = Field(ge=0, le=5.5)
    voltage_max: float = Field(ge=0, le=5.5)
    voltage_range: Optional[float] = Field(default=None, ge=0, le=5.5)
    current_mean: float = Field(ge=-10, le=10)
    current_std: float = Field(ge=0, le=10)
    temperature_mean: float = Field(ge=-55, le=125)
    temperature_max: float = Field(ge=-55, le=125)
    temperature_rise: float = Field(ge=-50, le=100)
    ref_soh: Optional[float] = Field(default=None, ge=0, le=150, description="Ground-truth SoH %, if known")
    ref_rul: Optional[float] = Field(default=None, ge=0, description="Ground-truth RUL cycles, if known")
    rated_capacity_ah: Optional[float] = Field(default=None, gt=0, le=100)

    @model_validator(mode="after")
    def _consistent(self) -> "CycleIn":
        if not (self.voltage_min <= self.voltage_mean <= self.voltage_max):
            raise ValueError("require voltage_min <= voltage_mean <= voltage_max")
        if self.temperature_max < self.temperature_mean:
            raise ValueError("temperature_max must be >= temperature_mean")
        expected = self.voltage_max - self.voltage_min
        if self.voltage_range is None:
            self.voltage_range = expected
        elif abs(self.voltage_range - expected) > 1e-3:
            raise ValueError("voltage_range must equal voltage_max - voltage_min")
        return self


class BatteryUpsert(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: Optional[str] = Field(default=None, max_length=80)
    chemistry: Optional[str] = Field(default=None, max_length=40)
    rated_capacity_ah: Optional[float] = Field(default=None, gt=0, le=100)
    nominal_voltage_v: Optional[float] = Field(default=None, gt=0, le=60)
    cutoff_voltage_v: Optional[float] = Field(default=None, gt=0, le=5)
    cycle_count_offset: Optional[int] = Field(
        default=None, ge=0, description="Discharge cycles the cell had already done before monitoring. "
                                        "Required before the model can use Cycle_number for live data.")
    notes: Optional[str] = Field(default=None, max_length=500)


class Thresholds(BaseModel):
    model_config = ConfigDict(extra="forbid")

    temp_high_warn_c: float = Field(45.0, ge=-40, le=125)
    temp_high_crit_c: float = Field(55.0, ge=-40, le=125)
    temp_low_warn_c: float = Field(0.0, ge=-55, le=60)
    voltage_low_warn_v: float = Field(3.2, ge=0, le=5)
    voltage_low_crit_v: float = Field(3.0, ge=0, le=5)
    voltage_high_warn_v: float = Field(4.2, ge=0, le=5.5)
    voltage_high_crit_v: float = Field(4.25, ge=0, le=5.5)
    current_high_warn_a: float = Field(1.0, ge=0, le=5)
    soh_low_warn_pct: float = Field(80.0, ge=0, le=150)
    soh_low_crit_pct: float = Field(70.0, ge=0, le=150)
    offline_after_s: float = Field(30.0, ge=5, le=86400)

    @model_validator(mode="after")
    def _ordered(self) -> "Thresholds":
        errs = []
        if self.temp_high_crit_c < self.temp_high_warn_c:
            errs.append("temp_high_crit_c must be >= temp_high_warn_c")
        if self.voltage_low_crit_v > self.voltage_low_warn_v:
            errs.append("voltage_low_crit_v must be <= voltage_low_warn_v")
        if self.voltage_high_crit_v < self.voltage_high_warn_v:
            errs.append("voltage_high_crit_v must be >= voltage_high_warn_v")
        if self.voltage_low_warn_v >= self.voltage_high_warn_v:
            errs.append("voltage_low_warn_v must be < voltage_high_warn_v")
        if self.soh_low_crit_pct > self.soh_low_warn_pct:
            errs.append("soh_low_crit_pct must be <= soh_low_warn_pct")
        if errs:
            raise ValueError("; ".join(errs))
        return self


class PresenceIn(BaseModel):
    """Operator statement about the cell on the INA219 input."""

    model_config = ConfigDict(extra="forbid")

    confirmed: bool = Field(description="true = a cell is physically connected (checked with a meter); "
                                        "false = withdraw any presence evidence")
    note: Optional[str] = Field(default=None, max_length=200)


class DeviceAssignmentIn(BaseModel):
    """Select which physical battery profile is connected to a device (null = none)."""

    model_config = ConfigDict(extra="forbid")

    battery_id: Optional[str] = Field(default=None, pattern=ID_PATTERN)
    note: Optional[str] = Field(default=None, max_length=200)


class TestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    battery_id: str = Field(pattern=ID_PATTERN)
    notes: Optional[str] = Field(default=None, max_length=500)


class TestChecklist(BaseModel):
    """Operator confirmations. They are records of what a person checked, not hardware controls."""
    model_config = ConfigDict(extra="forbid")
    battery_identity_confirmed: bool = False
    load_rating_verified: bool = False
    supervisor_approval: bool = False
    disconnect_procedure_confirmed: bool = False
    voltage_cutoff_confirmed: bool = False
    supervisor_name: Optional[str] = Field(default=None, max_length=80)
    cutoff_v: Optional[float] = Field(default=None, gt=0, le=5)
    load_resistance_ohm: Optional[float] = Field(default=None, gt=0, le=10000)
    load_power_rating_w: Optional[float] = Field(default=None, gt=0, le=1000)


class TestAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: Optional[str] = Field(default=None, max_length=300)
