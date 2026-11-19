// Renders pages against a mocked backend (fetch) - checks the API -> UI contract,
// empty/unavailable states and that no prediction value is invented.
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import Overview from "../pages/Overview";
import App from "../App";
import type { BatterySummary, DemoEstimate } from "../lib/api";
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

const demo: DemoEstimate = {
  status: "demo", label: "Experimental Demo Estimate", method: "demo-heuristic-v1", disclaimer: "", reason: null, required_tests: 3,
  tests_used: ["T1", "T2", "T3"], soh_pct: 87.7, rul_cycles: 193, inputs: { voltage_drop_v: 0.09 },
};

describe("Overview", () => {
  it("shows measured values with units and provenance", () => {
    render(<Overview battery={base} batteries={[base]} batteriesLoading={false} />);
    expect(screen.getByText("3.891")).toBeTruthy();
    expect(screen.getByText("0.372")).toBeTruthy();
    expect(screen.getByText("24.6")).toBeTruthy();
    expect(screen.getAllByText("Measured").length).toBe(5); // V, I, T, device power + live chart header
  });

  it("shows exactly one SoH/RUL pair, with the labelled sample values instead of the computed 0.0 % / 0", () => {
    const zero = { ...demo, soh_pct: 0, rul_cycles: 0 };
    const b = { ...base, managed: 1, quick_test_cycles: { count: 3, target: 3, last: null, demo_estimate: zero } } as BatterySummary;
    render(<Overview battery={b} batteries={[b]} batteriesLoading={false} />);
    expect(screen.getAllByText("SoH").length).toBe(1);
    expect(screen.getAllByText("RUL").length).toBe(1);
    expect(screen.getByText("91.6")).toBeTruthy();
    expect(screen.getByText("84")).toBeTruthy();
    expect(screen.getByText("remaining cycles")).toBeTruthy();
    expect(screen.getAllByText("Sample Demonstration Result").length).toBe(2);           // inside each card
    expect(screen.queryByText("0.0")).toBeNull();
    expect(screen.queryByText(/From 3 measured 300 s Quick Tests/)).toBeNull();          // never attributed to the tests
    expect(screen.queryByRole("region", { name: "Sample Demonstration Result" })).toBeNull();   // no extra section
    expect(screen.getByText("3.891")).toBeTruthy();                                        // live telemetry unchanged
  });

  it("keeps a genuine experimental ML estimate separate from the sample values", () => {
    const b = {
      ...base, managed: 1, quick_test_cycles: { count: 3, target: 3, last: null, demo_estimate: demo },
      latest_valid_prediction: { ...base.latest_prediction!, status: "experimental" as const, soh_pct: 81.23, rul_cycles: 40.4, eol_probability: 0.1, cycle_number: 50, reasons: [] },
    } as BatterySummary;
    render(<Overview battery={b} batteries={[b]} batteriesLoading={false} />);
    expect(screen.getByText("91.6")).toBeTruthy();
    expect(screen.getByText(/Saved ML model \(separate\): Experimental ML Estimate 81\.2 % SoH, 40 cycles RUL/)).toBeTruthy();
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

describe("single-battery focus", () => {
  it("shows only the focus battery, four sections, and keeps hidden profiles out of the selector", async () => {
    const mk = (id: string, extra: Partial<BatterySummary> = {}) => ({ ...base, battery_id: id, managed: 1, archived: false, ...extra }) as BatterySummary;
    const list = [mk("BAT-A-3000"), mk("BAT-B-3000"), mk("BAT-C-2500"), mk("CELL-18650-01", { managed: 0, archived: true })];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      const body = url.includes("/api/health")
        ? { status: "ok", mode: "production", focus_battery: "BAT-A-3000", api_version: "1", server_time: now, started_at: now, database: { ok: true, path: "x" }, models: { loaded: true, version: "v", error: null } }
        : url.includes("/api/batteries") ? { batteries: list } : {};
      return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
    });
    render(<App />);
    const sel = (await screen.findByLabelText("Select battery")) as HTMLSelectElement;
    await waitFor(() => expect([...sel.options].map((o) => o.value)).toEqual(["BAT-A-3000", "CELL-18650-01"]));
    expect(sel.value).toBe("BAT-A-3000");
    const nav = screen.getByRole("navigation", { name: "Sections" });
    expect([...nav.querySelectorAll("button")].map((x) => x.textContent?.trim())).toEqual(
      ["Overview", "Quick Tests", "Battery Analysis", "Battery Health Predictions"]);
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
    expect(screen.queryAllByText("Measured").length).toBe(4);               // I, T, P + chart header - not V
    expect(screen.getByRole("button", { name: /Confirm cell connected/ })).toBeTruthy();
  });

  it("an operator-confirmed cell is a measurement and can be withdrawn", () => {
    withReading({ voltage_v: 3.888, cell_state: "confirmed" }, { presence_evidence: "operator" });
    expect(screen.getAllByText("Measured").length).toBe(5);
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

describe("Quick Test progress on the Overview", () => {
  it("shows n / 3 valid Quick Test Cycles for a battery profile", () => {
    const b = { ...base, managed: 1, quick_test_cycles: { count: 2, target: 3, last: null } } as BatterySummary;
    render(<Overview battery={b} batteries={[b]} batteriesLoading={false} />);
    expect(screen.getByText(/Quick Test Cycles 2 \/ 3/)).toBeTruthy();
  });
});

describe("readiness: unselected profile", () => {
  it("shows a switched-away profile as not selected rather than offline", () => {
    const b = { ...base, managed: 1, connection: { state: "not_selected" as const, age_s: 900 }, assignment: null };
    render(<Overview battery={b} batteries={[b]} batteriesLoading={false} />);
    expect(screen.getAllByText(/not selected as connected/i).length).toBeGreaterThan(0);
    expect(screen.queryByText(/Offline/)).toBeNull();
  });
});
