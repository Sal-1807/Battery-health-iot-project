export function fmt(v: number | null | undefined, digits = 2, unit = ""): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  let txt = v.toFixed(digits);
  if (/^-0\.?0*$/.test(txt)) txt = txt.slice(1); // noise like -0.0002 must not read as "-0.000"
  return `${txt}${unit ? ` ${unit}` : ""}`;
}

export function fmtAge(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "never";
  if (seconds < 1) return "just now";
  if (seconds < 60) return `${Math.round(seconds)} s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  if (seconds < 86400) return `${(seconds / 3600).toFixed(1)} h ago`;
  return `${(seconds / 86400).toFixed(1)} d ago`;
}

export function ageSeconds(iso: string | null | undefined, now = Date.now()): number | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  return Number.isNaN(t) ? null : Math.max(0, (now - t) / 1000);
}

export function fmtTime(iso: string | number | null | undefined, withDate = false): string {
  if (iso === null || iso === undefined) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return withDate
    ? d.toLocaleString(undefined, { year: "numeric", month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" })
    : d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function fmtDuration(s: number | null | undefined): string {
  if (s === null || s === undefined) return "—";
  if (s < 120) return `${Math.round(s)} s`;
  if (s < 7200) return `${Math.round(s / 60)} min`;
  return `${(s / 3600).toFixed(1)} h`;
}

export const TIMESTAMP_LABEL: Record<string, string> = {
  device: "ESP32 clock (NTP)",
  bridge: "PC clock (USB bridge)",
  server: "server receive time",
};

export const SOURCE_LABEL: Record<string, string> = {
  hardware: "Hardware (ESP32)",
  simulation: "Simulation",
  nasa_replay: "NASA replay",
};

export const FLAG_LABEL: Record<string, string> = {
  server_timestamp: "server timestamp (device clock not synced)",
  ina219_fault: "INA219 fault",
  temperature_sensor_fault: "DS18B20 fault",
  ds18b20_power_on_value: "DS18B20 power-on value (85 °C) discarded",
  battery_not_detected: "no usable cell measurement (floating input or deeply discharged cell)",
  cell_unverified: "cell presence unverified",
  power_inconsistent: "power ≠ V×I",
};

export const ALERT_LABEL: Record<string, string> = {
  temperature_high: "High temperature",
  temperature_low: "Low temperature",
  voltage_low: "Low voltage",
  voltage_high: "High voltage",
  current_high: "High current",
  temperature_sensor_fault: "Temperature sensor fault",
  ina219_fault: "INA219 sensor fault",
  battery_not_detected: "No cell measurement",
  cell_signal_lost: "Cell signal lost",
  device_offline: "Device offline",
  soh_low: "Low predicted SoH",
};

export const CELL_STATE_LABEL: Record<string, string> = {
  no_cell: "No cell",
  unverified: "Unverified",
  verified_current: "Verified (current)",
  confirmed: "Confirmed (operator)",
  unknown: "Unknown",
};
