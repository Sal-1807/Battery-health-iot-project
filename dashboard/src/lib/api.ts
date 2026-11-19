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
export type ConnState = "online" | "stale" | "offline" | "never_seen" | "not_applicable" | "not_selected";
export type PredictionStatus = "unavailable" | "experimental" | "nasa_in_sample";
/** Backend measurement-validity state (iot/backend/presence.py). */
export type CellState = "no_cell" | "unverified" | "verified_current" | "confirmed" | "unknown";
export const VALID_CELL_STATES: CellState[] = ["verified_current", "confirmed"];

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
  cell_state: CellState | null;
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

export interface DischargeSession {
  id: number;
  battery_id: string;
  status: "active" | "ended";
  start_ts: string;
  end_ts: string | null;
  end_reason: "load_stopped" | "telemetry_gap" | "telemetry_lost" | null;
  charge_mah: number;
  energy_wh: number;
  uncertainty_mah: number;
  integrated_s: number;
  gap_s: number;
  n_gaps: number;
  energy_missing_s: number;
  n_readings: number;
  n_on: number;
  start_voltage_v: number | null;
  rest_voltage_before_v: number | null;
  end_voltage_v: number | null;
  min_voltage_v: number | null;
  peak_current_a: number | null;
  max_temperature_c: number | null;
  last_on_ms: number | null;
  duration_s: number;
  mean_current_a: number | null;
  kind: "partial" | "full_range_unvalidated";
  coverage: {
    started_from_full_rest_voltage: boolean;
    reached_cutoff_voltage: boolean;
    no_telemetry_gaps: boolean;
    full_charge_v: number;
    cutoff_v: number;
  };
  rated_capacity_mah: number | null;
  interpretation: string;
}

export interface DeviceAssignment {
  id: number;
  device_id: string;
  battery_id: string;
  start_ms: number;
  start_ts: string;
  end_ms: number | null;
  end_ts: string | null;
  note: string | null;
}

export interface BatteryStats {
  n_sessions: number;
  n_partial_sessions: number;
  total_discharged_mah: number;
  total_energy_wh: number;
  rated_capacity_mah: number | null;
  equivalent_full_cycles: number | null;
  completed_cycles: number;
  capacity_measurements: { session_id: number; start_ts: string; charge_mah: number; uncertainty_mah: number; energy_wh: number }[];
  last_session_start: string | null;
  notes: string;
}

export interface CompareRow {
  battery_id: string;
  source?: BatterySource;
  label: string | null;
  managed: boolean;
  rated_capacity_mah: number | null;
  assignment: DeviceAssignment | null;
  presence_evidence: "current" | "operator" | null;
  last_reading_ts: string | null;
  last_cell_state: CellState | null;
  n_readings: number;
  stats: BatteryStats;
  quick_test_cycles?: number;
  soh: "unavailable" | "experimental" | null;
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
  presence_evidence: "current" | "operator" | null;
  presence_since: string | null;
  presence_note: string | null;
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
  active_session: DischargeSession | null;
  last_session: DischargeSession | null;
  managed?: number | boolean;
  archived?: boolean;
  quick_test_cycles?: { count: number; target: number; last: QuickCycle | null; demo_estimate?: DemoEstimate };
  assignment?: DeviceAssignment | null;
  stats?: BatteryStats;
}

export interface HistoryPoint {
  ts: string;
  ts_ms?: number;
  voltage_v: number | null;
  current_a: number | null;
  temperature_c: number | null;
  power_w: number | null;
  battery_detected?: boolean | null;
  cell_state?: CellState | null;
  voltage_raw_v?: number | null;
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
  classification: "valid" | "invalid_measurement" | null;
  resolution_note: string | null;
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
  mode?: "production" | "simulation";
  focus_battery?: string | null;
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
  cell_state: CellState | null;
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

export interface RegMetrics { n: number; mae: number | null; rmse: number | null }
export interface ClfMetrics {
  n: number; accuracy: number; precision: number; recall: number; f1: number;
  confusion: { tp: number; tn: number; fp: number; fn: number };
}
export interface MlFold {
  cell: string;
  train_cells: string[];
  n_cycles: number;
  eol: { status: "observed" | "censored"; eol_cycle: number | null; last_cycle: number; cycles_after_eol: number | null;
         predicted_eol_cycle: number | null; note?: string };
  soh: { model: RegMetrics; baseline: RegMetrics };
  rul_classifier: ClfMetrics;
  rul_regressor_pre_eol: { model: RegMetrics; baseline: RegMetrics };
  rul_two_stage: { model: RegMetrics; baseline: RegMetrics };
  rul_by_life_stage: { stage: string; n: number; model: RegMetrics; baseline: RegMetrics }[];
  baseline_definition: { soh: string; rul: string };
}
export interface MlSummary {
  cells: string[];
  protocol: string;
  disclaimer: string;
  eol_threshold_soh_pct: number;
  eol_definition: string;
  production_models_note: string;
  rul_difficulty: string[];
  folds: Record<string, MlFold>;
  aggregate: Record<string, { mean: number; std: number } | null>;
  reproduction: { checked: boolean; matches_committed_lobo_predictions?: boolean; file?: string };
}
export interface MlCycle {
  Cycle: number; SoH: number; SoH_pred: number; SoH_baseline: number; RUL: number | null; RUL_pred: number;
  RUL_reg_pred: number; RUL_baseline: number | null; Reached_EOL: number; EOL_pred: number; EOL_prob: number;
}

export interface AgingSession {
  session_id: number; start_ts: string; end_ts: string | null; status: string; end_reason: string | null;
  charge_mah: number; energy_wh: number; duration_s: number; mean_current_a: number | null; c_rate: number | null;
  mean_temperature_c: number | null; mean_loaded_voltage_v: number | null;
  full_test: boolean; full_test_issues: string[]; comparable: boolean; comparability_issues: string[]; is_reference: boolean;
  n_gaps?: number; uncertainty_mah?: number; counted_in_throughput?: boolean; throughput_excluded?: string | null;
}
export interface Aging {
  battery_id: string;
  source?: BatterySource;
  label: string | null;
  rated_capacity_mah: number | null;
  rated_note: string;
  totals: { n_sessions: number; delivered_mah: number; delivered_wh: number; discharge_duration_s: number;
            equivalent_full_cycles: number | null };
  throughput?: {
    definition: string; total_mah: number; total_wh: number; duration_s: number; equivalent_full_cycles: number | null;
    uncertainty_mah: number; n_sessions: number; n_partial_sessions: number; partial_mah: number; partial_wh: number;
    n_full_tests: number; full_mah: number; in_progress_mah: number; sessions_with_gaps: number;
    overlapping_sessions_excluded: number[];
  };
  full_capacity_definition?: string;
  n_full_tests: number;
  n_comparable_tests: number;
  reference: { session_id: number; start_ts: string; capacity_mah: number; mean_current_a: number | null;
               c_rate: number | null; mean_temperature_c: number | null; cutoff_v: number } | null;
  retention: { status: "ok" | "insufficient_data"; reason: string | null; latest_pct: number | null; definition: string };
  capacity_series: { index: number; session_id: number; start_ts: string; capacity_mah: number; uncertainty_mah: number;
                     energy_wh: number; mean_current_a: number | null; mean_temperature_c: number | null;
                     retention_pct: number | null }[];
  trends?: { session_id: number; start_ts: string; start_ms: number | null; mean_temperature_c: number | null;
             max_temperature_c: number | null; mean_loaded_voltage_v: number | null; min_voltage_v: number | null;
             kind: "comparable_full" | "full" | "partial" }[];
  sessions?: AgingSession[];
  rul: { status: string; reason: string };
  soh: { status: string; reason: string };
}
export interface AgingRules {
  current_tolerance_frac: number; temperature_tolerance_c: number; max_uncertainty_frac: number;
  min_trend_points: number; full_test: string;
}

export interface QuickCycle {
  number: number; test_uid: string; completed_at: string; duration_s: number;
  initial_voltage_v: number | null; final_voltage_v: number | null; voltage_drop_v: number | null;
  mean_voltage_v: number | null; mean_current_a: number | null; mean_power_w: number | null;
  max_temperature_c: number | null; temperature_rise_c: number | null; delivered_mah: number; delivered_wh: number;
}
/** Illustrative heuristic score from the Quick Tests (iot/backend/demo_estimate.py). Not ML, not a measurement. */
export interface DemoEstimate {
  status: "demo" | "no_estimate"; label: string; method: string; disclaimer: string; reason: string | null;
  required_tests: number; tests_used: string[]; soh_pct: number | null; rul_cycles: number | null;
  inputs: Record<string, number | null> | null;
  components?: { resistance_penalty_pts: number; thermal_penalty_pts: number };
  constants?: Record<string, number>;
}
export type QuickStatus = "not_started" | "in_progress" | "counted" | "incomplete" | "interrupted" | "telemetry_gap" | "invalid";
export interface QuickBattery {
  battery_id: string; count: number; target: number; duration_s: number; next_test_number: number | null; complete: boolean;
  definition: string; cycles: QuickCycle[];
  attempts: { test_uid: string; state: string; status: QuickStatus; label: string; number?: number }[];
  summary: Record<string, { mean: number; min: number; max: number } | null>;
  not_counted: Record<string, number>;
  demo_estimate?: DemoEstimate;
}
export type QuickMetric = "mean_voltage_v" | "mean_current_a" | "mean_power_w" | "max_temperature_c" | "voltage_drop_v" | "delivered_mah" | "delivered_wh";
export interface QuickOverview {
  batteries: QuickBattery[];
  comparison: { rows: ({ battery_id: string; count: number; target: number } & Record<string, number | string | null>)[];
                group_mean: Record<string, number | null>; note: string };
  model_status: { validated_model: boolean; message: string };
  protocol: { duration_s: number; target_per_battery: number };
}

export type TestState = "not_started" | "ready" | "running" | "paused" | "completed" | "aborted";
export interface TestChecklist {
  battery_identity_confirmed: boolean; load_rating_verified: boolean; supervisor_approval: boolean;
  disconnect_procedure_confirmed: boolean; voltage_cutoff_confirmed: boolean;
  supervisor_name: string | null; cutoff_v: number | null; load_resistance_ohm: number | null; load_power_rating_w: number | null;
}
export interface TestResults {
  available: boolean; reason?: string;
  elapsed_s?: number; load_on_s?: number; n_readings?: number;
  initial_voltage_v?: number | null; initial_current_a?: number | null; final_voltage_v?: number | null;
  min_loaded_voltage_v?: number | null; mean_current_a?: number | null; peak_current_a?: number | null;
  mean_power_w?: number | null; peak_power_w?: number | null; initial_temperature_c?: number | null;
  max_temperature_c?: number | null; final_temperature_c?: number | null; delivered_mah?: number; delivered_wh?: number;
  gaps?: { from: string; to: string; duration_s: number; note?: string }[];
  missing_measurements?: { voltage_v: number; current_a: number; temperature_c: number; readings_with_any_missing: number };
  paused?: boolean; load_current_while_paused_readings?: number; battery_stayed_selected?: boolean;
  last_reading_age_s?: number | null;
}
export interface BatteryTest {
  test_uid: string; battery_id: string; device_id: string | null; state: TestState; created_at: string;
  started_at: string | null; ended_at: string | null; notes: string | null; abort_reason: string | null;
  checklist: Partial<TestChecklist>; checklist_problems: string[];
  history: { state: TestState; ts: string; note: string | null }[];
  results: TestResults; results_frozen: boolean;
  validity: { valid_for_full_capacity: boolean; classification: string; checks: { check: string; ok: boolean; detail: string }[] };
  hardware_state: { description: string; last_reading_ts: string | null; battery_selected_on: string | null };
  hardware_notice: string; disconnect_warning: string; soh: string; rul: string;
  kind?: "quick" | "supervised"; duration_s?: number | null;
  quick_test?: {
    status: QuickStatus; label: string; checks: { check: string; ok: boolean; detail: string }[];
    number: number | null; target: number; duration_s: number; elapsed_s: number | null; remaining_s: number | null;
    auto_ended: boolean; disconnect_confirmed_at: string | null; needs_disconnect_confirmation: boolean;
  } | null;
}

export interface QuickAssessment {
  battery_id: string;
  quick_tests: { count: number; target: number; window_s: number;
    measured: ({ number: number; test_uid: string; completed_at: string; duration_s: number } & Record<string, number | string | null>)[] };
  saved_model: {
    usable: boolean; reason: string;
    inputs: { model_input: string; kind: "missing" | "different_quantity" | "out_of_regime"; quick_metric: string | null;
              quick_values: (number | null)[]; nasa_training_range: { min: number; max: number } | null;
              inside_nasa_range: boolean | null; usable_by_saved_model: boolean; reason: string }[];
  };
  short_test_model: { defensible_short_test_model: boolean; implemented: boolean; conclusion: string;
                      checks: { requirement: string; met: boolean; detail: string }[] };
  soh: { status: "unavailable"; value: null }; rul: { status: "unavailable"; value: null }; note: string;
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
  history: (id: string, p: { start?: string; end?: string; limit?: number; max_points?: number }) =>
    request<HistoryResponse>(`/api/battery/${enc(id)}/history${q(p)}`),
  historyCsvUrl: (id: string, p: { start?: string; end?: string; limit?: number }) =>
    apiUrl(`/api/battery/${enc(id)}/history${q({ ...p, format: "csv" })}`),
  setPresence: (id: string, confirmed: boolean, note?: string) =>
    request<{ presence: { evidence: string | null } }>(`/api/battery/${enc(id)}/presence`, {
      method: "POST",
      body: JSON.stringify({ confirmed, note: note || null }),
    }),
  devices: () => request<{ devices: { device_id: string; last_seen_at: string | null; assignment: DeviceAssignment | null }[] }>("/api/devices"),
  setAssignment: (deviceId: string, batteryId: string | null, note?: string) =>
    request<{ changed: boolean; ended: { battery_id: string; session_id?: number } | null }>(
      `/api/devices/${enc(deviceId)}/assignment`,
      { method: "PUT", body: JSON.stringify({ battery_id: batteryId, note: note || null }) },
    ),
  compare: () => request<{ batteries: CompareRow[]; note: string }>("/api/batteries/compare"),
  sessions: (id: string) =>
    request<{ sessions: DischargeSession[]; rated_capacity_mah: number | null; rated_capacity_note: string }>(
      `/api/battery/${enc(id)}/sessions`,
    ),
  cycles: (id: string) => request<{ cycles: Cycle[] }>(`/api/battery/${enc(id)}/cycles`),
  alerts: (p: { battery_id?: string; status?: "active" | "resolved"; limit?: number }) =>
    request<{ alerts: Alert[] }>(`/api/alerts${q(p)}`),
  ackAlert: (id: number) => request<{ id: number }>(`/api/alerts/${id}/acknowledge`, { method: "POST" }),
  thresholds: () => request<Thresholds>("/api/settings/thresholds"),
  saveThresholds: (t: Thresholds) =>
    request<Thresholds>("/api/settings/thresholds", { method: "PUT", body: JSON.stringify(t) }),
  tests: (batteryId?: string) => request<{ tests: BatteryTest[] }>(`/api/tests${q({ battery_id: batteryId })}`),
  test: (uid: string) => request<BatteryTest>(`/api/tests/${enc(uid)}`),
  createTest: (batteryId: string, notes?: string) =>
    request<{ test_uid: string }>("/api/tests", { method: "POST", body: JSON.stringify({ battery_id: batteryId, notes: notes || null, kind: "quick" }) }),
  confirmDisconnect: (uid: string) =>
    request<{ note: string }>(`/api/tests/${enc(uid)}/confirm-disconnect`, { method: "POST" }),
  quickTests: (batteryId?: string) => request<QuickOverview>(`/api/quick-tests${q({ battery_id: batteryId })}`),
  quickFeaturesCsvUrl: () => apiUrl("/api/quick-tests/features.csv"),
  saveChecklist: (uid: string, c: Partial<TestChecklist>) =>
    request<{ state: TestState; checklist_problems: string[] }>(`/api/tests/${enc(uid)}/checklist`, { method: "PUT", body: JSON.stringify(c) }),
  testAction: (uid: string, action: "start" | "pause" | "resume" | "complete" | "abort", reason?: string) =>
    request<{ state: TestState; hardware_notice: string; warning?: string }>(`/api/tests/${enc(uid)}/${action}`, {
      method: "POST", body: JSON.stringify({ reason: reason || null }),
    }),
  testsCsvUrl: (batteryId?: string) => apiUrl(`/api/tests/export.csv${q({ battery_id: batteryId })}`),
  testReadingsCsvUrl: (uid: string) => apiUrl(`/api/tests/${enc(uid)}/readings.csv`),
  aging: () => request<{ batteries: Aging[]; rules: AgingRules }>("/api/aging"),
  agingDetail: (id: string) => request<Aging & { rules: AgingRules }>(`/api/aging/${enc(id)}`),
  mlValidation: () => request<MlSummary>("/api/ml-validation"),
  quickAssessment: () => request<{ batteries: QuickAssessment[] }>("/api/ml/quick-test-assessment"),
  mlDemo: (cell: string, cycle: number) =>
    request<{
      cell: string; cycle: number; source: string; features: Record<string, number>;
      actual: { soh_pct: number; rul_cycles: number | null; reached_eol: number };
      saved_model: { status: string; soh_pct: number | null; rul_cycles: number | null; eol_probability: number | null; model_version: string | null; note: string };
      held_out: { soh_pct: number; rul_cycles: number; trained_on: string[]; note: string };
      disclaimer: string;
    }>(`/api/ml-validation/${enc(cell)}/demo${q({ cycle })}`),
  mlValidationCell: (cell: string) =>
    request<{ cell: string; fold: MlFold; cycles: MlCycle[]; disclaimer: string }>(`/api/ml-validation/${enc(cell)}`),
};
