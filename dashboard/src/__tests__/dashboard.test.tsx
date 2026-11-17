// Renders pages against a mocked backend (fetch) - checks the API -> UI contract,
// empty/unavailable states and that no prediction value is invented.
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import Overview from "../pages/Overview";
import App from "../App";
import type { BatterySummary } from "../lib/api";
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
  notes: null,
  created_at: now,
  connection: { state: "online", age_s: 1 },
  latest_reading: {
    id: 1, battery_id: "CELL-1", device_id: "esp32-a", source: "hardware", ts: now, timestamp_source: "device",
    received_at: now, voltage_v: 3.891, current_a: 0.372, temperature_c: 24.56, power_w: 1.447,
    power_source: "device", ina219_status: "ok", ds18b20_status: "ok", battery_detected: true, flags: [],
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

describe("format helpers", () => {
  it("formats missing values as a dash, never zero", () => {
    expect(fmt(null)).toBe("—");
    expect(fmt(undefined, 1, "V")).toBe("—");
    expect(fmt(0, 1, "V")).toBe("0.0 V");
    expect(fmtAge(null)).toBe("never");
    expect(fmtAge(90)).toBe("2 min ago");
  });
});
