// Typed client for the FastAPI backend. All requests are relative ("/api/...");
// the Vite dev/preview server proxies them to the backend (see vite.config.ts).
// Set VITE_API_BASE at build time to call a backend on another origin instead.

const BASE = (import.meta.env.VITE_API_BASE as string | undefined)?.replace(/\/$/, "") ?? "";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(BASE + path, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    });
  } catch {
    throw new ApiError(0, "Backend unreachable");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail || `HTTP ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export const apiUrl = (path: string) => BASE + path;

// ------------------------------------------------------------------ types
export type BatterySource = "hardware" | "simulation" | "nasa_replay";
export type ConnState = "online" | "stale" | "offline" | "never_seen" | "not_applicable";
export type PredictionStatus = "unavailable" | "experimental" | "nasa_in_sample";

export interface Reading {
  id: number;
  battery_id: string;
  device_id: string;
  source: BatterySource;
  ts: string;
  ts_ms?: number;
  timestamp_source: "device" | "bridge" | "server";
  transport?: "wifi" | "usb_serial" | null;
  received_at: string;
  voltage_v: number | null;
  voltage_raw_v?: number | null;
  current_a: number | null;
  temperature_c: number | null;
  power_w: number | null;
  power_source: "device" | "computed" | null;
  ina219_status: string | null;
  ds18b20_status: string | null;
  battery_detected: boolean | null;
  flags: string[];
}

export interface OutOfRange {
  feature: string;
  value: number;
  train_min: number;
  train_max: number;
  allowed_min: number;
  allowed_max: number;
}

export interface Prediction {
  id: number;
  battery_id: string;
  cycle_id: number | null;
  cycle_number: number | null;
  created_at: string;
  status: PredictionStatus;
  model_version: string | null;
  data_source: string;
  soh_pct: number | null;
  eol_probability: number | null;
  eol_predicted: number | null;
  rul_cycles: number | null;
  reasons: string[];
  warnings: string[];
  out_of_range: OutOfRange[];
  input_features: Record<string, number> | null;
  ref_soh?: number | null;
  ref_rul?: number | null;
}

export interface BatterySummary {
  battery_id: string;
  label: string | null;
  chemistry: string | null;
  rated_capacity_ah: number | null;
  nominal_voltage_v: number | null;
  cutoff_voltage_v: number | null;
  cycle_count_offset: number | null;
  source: BatterySource;
  notes: string | null;
  created_at: string;
  connection: { state: ConnState; age_s: number | null };
  latest_reading: Reading | null;
  latest_prediction: Prediction | null;
  latest_valid_prediction: Prediction | null;
  active_alerts: Partial<Record<"info" | "warning" | "critical", number>>;
  n_readings: number;
  n_cycles: number;
  n_complete_cycles: number;
}

export interface HistoryPoint {
  ts: string;
  ts_ms?: number;
  voltage_v: number | null;
  current_a: number | null;
  temperature_c: number | null;
  power_w: number | null;
  battery_detected?: boolean | null;
  flags?: string[];
  device_id?: string;
  source?: string;
  n?: number;
}

export interface HistoryResponse {
  battery_id: string;
  total: number;
  downsampled: boolean;
  truncated?: boolean;
  bucket_s?: number;
  points: HistoryPoint[];
}

export interface Cycle {
  id: number;
  battery_id: string;
  source: string;
  segment_index: number;
  cycle_number: number | null;
  start_ts: string | null;
  end_ts: string | null;
  n_readings: number | null;
  duration_s: number | null;
  voltage_mean: number | null;
  voltage_min: number | null;
  voltage_max: number | null;
  current_mean: number | null;
  temperature_mean: number | null;
  temperature_rise: number | null;
  complete: number;
  completeness_notes: string[];
  ref_soh: number | null;
  ref_rul: number | null;
}

export interface Alert {
  id: number;
  battery_id: string;
  device_id: string | null;
  type: string;
  severity: "info" | "warning" | "critical";
  message: string;
  value: number | null;
  threshold: number | null;
  unit: string | null;
  status: "active" | "resolved";
  acknowledged: number;
  first_seen: string;
  last_seen: string;
  resolved_at: string | null;
  occurrences: number;
  experimental: number;
}

export interface Thresholds {
  temp_high_warn_c: number;
  temp_high_crit_c: number;
  temp_low_warn_c: number;
  voltage_low_warn_v: number;
  voltage_low_crit_v: number;
  voltage_high_warn_v: number;
  voltage_high_crit_v: number;
  current_high_warn_a: number;
  soh_low_warn_pct: number;
  soh_low_crit_pct: number;
  offline_after_s: number;
}

export interface Health {
  status: "ok" | "degraded";
  api_version: string;
  server_time: string;
  started_at: string;
  database: { ok: boolean; path: string };
  models: { loaded: boolean; version: string | null; error: string | null };
}

export interface LoboFold {
  test_battery: string;
  n_test: number;
  soh_RMSE: number;
  soh_MAE: number;
  soh_R2: number;
  eol_Accuracy: number;
  eol_Recall: number;
  eol_F1: number;
  rul_RMSE: number;
  rul_MAE: number;
  rul_R2: number;
}

export interface ModelInfo {
  loaded: boolean;
  error: string | null;
  model_version: string | null;
  load_warnings: string[];
  sklearn_version: string;
  features: string[];
  training_batteries: string[] | null;
  n_training_samples: number | null;
  estimators: Record<string, string> | null;
  nasa_rated_capacity_ah: number;
  nasa_eol_soh_pct: number;
  feature_envelope: Record<string, { min: number; max: number }>;
  envelope_tolerance: number;
  limitations: string[];
  lobo: { per_fold: LoboFold[] | null; aggregate: { metric: string; mean: number; std: number }[] | null; note: string };
}

export interface Device {
  device_id: string;
  battery_id: string;
  firmware_version: string | null;
  boot_id: string | null;
  last_rssi_dbm: number | null;
  last_seen_at: string | null;
  last_sensor_status: { ina219?: string; ds18b20?: string; battery_connected?: boolean } | null;
  age_s: number | null;
  state: ConnState;
  data_origin: BatterySource | null;
}

export interface SystemStatus {
  api: Health;
  devices: Device[];
  readings_by_source: Record<string, number>;
  last_telemetry_ts: string | null;
  last_received_at: string | null;
  offline_after_s: number;
  model: Omit<ModelInfo, "lobo">;
}

export interface NasaBattery {
  battery_id: string;
  n_cycles: number;
  eol_cycle: number | null;
  initial_soh_pct: number;
  final_soh_pct: number;
}

export interface NasaCycle {
  Battery_ID: string;
  Cycle: number;
  SoH: number;
  RUL: number | null;
  Reached_EOL: number;
  SoH_lobo_pred?: number | null;
  RUL_lobo_pred?: number | null;
  Reached_EOL_lobo_pred?: number | null;
  [feature: string]: string | number | null | undefined;
}

// ------------------------------------------------------------------ calls
const q = (params: Record<string, string | number | undefined | null>) => {
  const s = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") s.set(k, String(v));
  const str = s.toString();
  return str ? `?${str}` : "";
};
const enc = encodeURIComponent;

export const api = {
  health: () => request<Health>("/api/health"),
  system: () => request<SystemStatus>("/api/system/status"),
  batteries: () => request<{ batteries: BatterySummary[] }>("/api/batteries"),
  updateBattery: (id: string, body: Partial<BatterySummary>) =>
    request<BatterySummary>(`/api/battery/${enc(id)}`, { method: "PUT", body: JSON.stringify(body) }),
  history: (id: string, p: { start?: string; end?: string; limit?: number; max_points?: number }) =>
    request<HistoryResponse>(`/api/battery/${enc(id)}/history${q(p)}`),
  historyCsvUrl: (id: string, p: { start?: string; end?: string; limit?: number }) =>
    apiUrl(`/api/battery/${enc(id)}/history${q({ ...p, format: "csv" })}`),
  cycles: (id: string) => request<{ cycles: Cycle[] }>(`/api/battery/${enc(id)}/cycles`),
  predictions: (id: string) => request<{ predictions: Prediction[] }>(`/api/battery/${enc(id)}/predictions`),
  alerts: (p: { battery_id?: string; status?: "active" | "resolved"; limit?: number }) =>
    request<{ alerts: Alert[] }>(`/api/alerts${q(p)}`),
  ackAlert: (id: number) => request<{ id: number }>(`/api/alerts/${id}/acknowledge`, { method: "POST" }),
  thresholds: () => request<Thresholds>("/api/settings/thresholds"),
  saveThresholds: (t: Thresholds) =>
    request<Thresholds>("/api/settings/thresholds", { method: "PUT", body: JSON.stringify(t) }),
  models: () => request<ModelInfo>("/api/models"),
  nasaBatteries: () => request<{ batteries: NasaBattery[] }>("/api/nasa/batteries"),
  nasaCycles: (id: string) => request<{ cycles: NasaCycle[] }>(`/api/nasa/${enc(id)}/cycles`),
};
