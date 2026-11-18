// Renders pages against a mocked backend (fetch) - checks the API -> UI contract,
// empty/unavailable states and that no prediction value is invented.
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import Overview from "../pages/Overview";
import App from "../App";
import type { BatterySummary, DischargeSession } from "../lib/api";
import { fmt, fmtAge } from "../lib/format";

beforeAll(() => {
  // Recharts' ResponsiveContainer needs ResizeObserver, which jsdom lacks.
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const now = new Date().toISOString();
const base: BatterySummary = {
  battery_id: "CELL-1",
  label: null,
  chemistry: null,
  rated_capacity_ah: 3,
  nominal_voltage_v: null,
  cutoff_voltage_v: null,
  cycle_count_offset: null,
  source: "hardware",
  presence_evidence: "current",
  presence_since: now,
  presence_note: null,
  notes: null,
  created_at: now,
  connection: { state: "online", age_s: 1 },
  latest_reading: {
    id: 1, battery_id: "CELL-1", device_id: "esp32-a", source: "hardware", ts: now, timestamp_source: "device",
    received_at: now, voltage_v: 3.891, current_a: 0.372, temperature_c: 24.56, power_w: 1.447,
    power_source: "device", ina219_status: "ok", ds18b20_status: "ok", battery_detected: true,
    cell_state: "verified_current", flags: [],
  },
  latest_prediction: {
    id: 9, battery_id: "CELL-1", cycle_id: 1, cycle_number: null, created_at: now, status: "unavailable",
    model_version: "rf-nasa-x", data_source: "hardware", soh_pct: null, eol_probability: null, eol_predicted: null,
    rul_cycles: null, reasons: ["features outside NASA training range: Current_mean"], warnings: [], out_of_range: [],
    input_features: null,
  },
  latest_valid_prediction: null,
  active_alerts: {},
  n_readings: 10,
  n_cycles: 1,
  n_complete_cycles: 1,
  active_session: null,
  last_session: null,
};

describe("Overview", () => {
  it("shows measured values with units and provenance", () => {
    render(<Overview battery={base} batteries={[base]} batteriesLoading={false} />);
    expect(screen.getByText("3.891")).toBeTruthy();
    expect(screen.getByText("0.372")).toBeTruthy();
    expect(screen.getByText("24.6")).toBeTruthy();
    expect(screen.getAllByText("Measured").length).toBe(4); // V, I, T and device-reported power
  });

  it("never shows a SoH number when the prediction is unavailable, and explains why", () => {
    render(<Overview battery={base} batteries={[base]} batteriesLoading={false} />);
    expect(screen.getAllByText("Unavailable").length).toBeGreaterThanOrEqual(2);
    expect(screen.getAllByText(/outside NASA training range/).length).toBeGreaterThan(0);
    expect(screen.queryByText("%")).toBeNull();
  });

  it("labels an experimental estimate", () => {
    const b = {
      ...base,
      latest_valid_prediction: { ...base.latest_prediction!, status: "experimental" as const, soh_pct: 81.23, rul_cycles: 40.4, eol_probability: 0.1, cycle_number: 50, reasons: [] },
    };
    render(<Overview battery={b} batteries={[b]} batteriesLoading={false} />);
    expect(screen.getByText("81.2")).toBeTruthy();
    expect(screen.getAllByText("Experimental").length).toBe(2);
    expect(screen.getByText(/Not validated for this cell/)).toBeTruthy();
  });

  it("marks simulated data", () => {
    const b = { ...base, source: "simulation" as const, latest_reading: { ...base.latest_reading!, source: "simulation" as const } };
    render(<Overview battery={b} batteries={[b]} batteriesLoading={false} />);
    expect(screen.getAllByText("Simulated").length).toBeGreaterThanOrEqual(3);
  });

  it("has an empty state without batteries", () => {
    render(<Overview battery={undefined} batteries={[]} batteriesLoading={false} />);
    expect(screen.getByText(/Waiting for the first reading/)).toBeTruthy();
  });
});

describe("App shell", () => {
  it("shows a clear error when the backend is unreachable", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("Failed to fetch"));
    render(<App />);
    await waitFor(() => expect(screen.getByText(/Cannot reach the backend/)).toBeTruthy());
  });

  it("renders batteries returned by the API", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      const body = url.includes("/api/health")
        ? { status: "ok", api_version: "1", server_time: now, started_at: now, database: { ok: true, path: "x" }, models: { loaded: true, version: "v", error: null } }
        : url.includes("/api/batteries")
          ? { batteries: [base] }
          : {};
      return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
    });
    render(<App />);
    await waitFor(() => expect(screen.getByText("3.891")).toBeTruthy());
    expect(screen.getByText("API connected")).toBeTruthy();
  });
});

describe("cell presence states", () => {
  const withReading = (patch: Partial<BatterySummary["latest_reading"] & object>, presence: Partial<BatterySummary> = {}) => {
    const b = { ...base, ...presence, latest_reading: { ...base.latest_reading!, ...patch } } as BatterySummary;
    render(<Overview battery={b} batteries={[b]} batteriesLoading={false} />);
  };

  for (const v of [0.72, 0.88, 1.14]) {
    it(`floating ${v} V is shown as "No cell", never as a voltage`, () => {
      withReading({ voltage_v: v, current_a: -0.0002, battery_detected: false, cell_state: "no_cell", flags: ["battery_not_detected"] },
        { presence_evidence: null });
      expect(screen.getByText("No cell")).toBeTruthy();
      expect(screen.queryByText(v.toFixed(3))).toBeNull();               // no headline number
      expect(screen.getByText(/Not a battery voltage/)).toBeTruthy();
      expect(screen.getAllByText("Unavailable").length).toBeGreaterThanOrEqual(1);
      cleanup();
    });
  }

  it("a resting in-range reading is Unverified and offers confirmation", () => {
    withReading({ voltage_v: 3.889, current_a: 0.0001, battery_detected: null, cell_state: "unverified", flags: ["cell_unverified"] },
      { presence_evidence: null });
    expect(screen.getByText("3.889")).toBeTruthy();
    expect(screen.getAllByText("Unverified").length).toBeGreaterThanOrEqual(1);
    expect(screen.queryAllByText("Measured").length).toBe(3);               // I, T, P only - not V
    expect(screen.getByRole("button", { name: /Confirm cell connected/ })).toBeTruthy();
  });

  it("an operator-confirmed cell is a measurement and can be withdrawn", () => {
    withReading({ voltage_v: 3.888, cell_state: "confirmed" }, { presence_evidence: "operator" });
    expect(screen.getAllByText("Measured").length).toBe(4);
    expect(screen.getByText(/Cell confirmed by operator/)).toBeTruthy();
    expect(screen.getByRole("button", { name: /Withdraw confirmation/ })).toBeTruthy();
  });

  it("a current-verified cell is a measurement", () => {
    withReading({ voltage_v: 3.71, current_a: 0.37, cell_state: "verified_current" });
    expect(screen.getByText("3.710")).toBeTruthy();
    expect(screen.getByText(/load current observed/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Confirm cell connected/ })).toBeNull();
  });

  it("labels USB-bridge timestamps as PC clock", () => {
    const b = { ...base, latest_reading: { ...base.latest_reading!, timestamp_source: "bridge" as const, transport: "usb_serial" as const } };
    render(<Overview battery={b} batteries={[b]} batteriesLoading={false} />);
    expect(screen.getByText(/PC clock \(USB bridge\)/)).toBeTruthy();
  });
});

describe("format helpers", () => {
  it("never prints negative zero", () => {
    expect(fmt(-0.0002, 3)).toBe("0.000");
    expect(fmt(-0.0006, 3)).toBe("-0.001");
  });

  it("formats missing values as a dash, never zero", () => {
    expect(fmt(null)).toBe("—");
    expect(fmt(undefined, 1, "V")).toBe("—");
    expect(fmt(0, 1, "V")).toBe("0.0 V");
    expect(fmtAge(null)).toBe("never");
    expect(fmtAge(90)).toBe("2 min ago");
  });
});

const session: DischargeSession = {
  id: 3, battery_id: "CELL-1", status: "active", start_ts: now, end_ts: null, end_reason: null,
  charge_mah: 123.456, energy_wh: 0.4567, uncertainty_mah: 0.2, integrated_s: 1200, gap_s: 0, n_gaps: 0,
  energy_missing_s: 0, n_readings: 600, n_on: 600, start_voltage_v: 3.85, rest_voltage_before_v: 3.95,
  end_voltage_v: 3.62, min_voltage_v: 3.61, peak_current_a: 0.38, max_temperature_c: 26.4, last_on_ms: Date.now(),
  duration_s: 1200, mean_current_a: 0.370, kind: "partial",
  coverage: { started_from_full_rest_voltage: false, reached_cutoff_voltage: false, no_telemetry_gaps: true, full_charge_v: 4.15, cutoff_v: 3 },
  rated_capacity_mah: 3000, interpretation: "Charge delivered during this session only.",
};

describe("discharge session panel", () => {
  it("shows measured session charge and energy, distinct from capacity", () => {
    const b = { ...base, active_session: session, last_session: session };
    render(<Overview battery={b} batteries={[b]} batteriesLoading={false} />);
    expect(screen.getByText("In progress")).toBeTruthy();
    expect(screen.getByText("123.5")).toBeTruthy();            // mAh
    expect(screen.getByText("0.457")).toBeTruthy();            // Wh
    expect(screen.getByText(/Discharged charge ≠ battery capacity/)).toBeTruthy();
    expect(screen.getByText("3000 mAh")).toBeTruthy();         // label rating shown as such
    expect(screen.getByText(/manufacturer rating, not measured/)).toBeTruthy();
    expect(screen.queryByText(/4\.1 ?%/)).toBeNull();          // 123.5/3000 never shown as a percentage
    // health stays unavailable: a session is not a health measurement
    expect(screen.getAllByText("Unavailable").length).toBeGreaterThanOrEqual(2);
  });

  it("keeps the last session after discharge stops", () => {
    const ended = { ...session, status: "ended" as const, end_ts: now, end_reason: "load_stopped" as const };
    const b = { ...base, active_session: null, last_session: ended };
    render(<Overview battery={b} batteries={[b]} batteriesLoading={false} />);
    expect(screen.getByText(/Last session · load stopped/)).toBeTruthy();
    expect(screen.getByText("123.5")).toBeTruthy();
  });

  it("explains how a session starts when none exists", () => {
    render(<Overview battery={base} batteries={[base]} batteriesLoading={false} />);
    expect(screen.getByText(/No discharge recorded/)).toBeTruthy();
  });
});
