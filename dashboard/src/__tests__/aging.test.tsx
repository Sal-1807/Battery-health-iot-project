import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import AgingPage from "../pages/Aging";
import type { Aging, BatterySummary, QuickCycle, QuickOverview } from "../lib/api";

beforeAll(() => {
  globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} } as unknown as typeof ResizeObserver;
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const empty = (id: string, rated: number): Aging => ({
  battery_id: id, label: id, rated_capacity_mah: rated, rated_note: "label",
  totals: { n_sessions: 0, delivered_mah: 0, delivered_wh: 0, discharge_duration_s: 0, equivalent_full_cycles: 0 },
  n_full_tests: 0, n_comparable_tests: 0, reference: null,
  retention: { status: "insufficient_data", reason: "No full, uninterrupted, gap-free discharge test has been recorded.", latest_pct: null, definition: "Not absolute SoH" },
  capacity_series: [], trends: [], sessions: [],
  rul: { status: "unavailable", reason: "" }, soh: { status: "unavailable", reason: "" },
});
const withData: Aging = {
  ...empty("BAT-A-3000", 3000),
  totals: { n_sessions: 3, delivered_mah: 2700, delivered_wh: 9.6, discharge_duration_s: 21600, equivalent_full_cycles: 0.9 },
  n_full_tests: 3, n_comparable_tests: 2,
  reference: { session_id: 1, start_ts: "2026-11-19T00:00:00Z", capacity_mah: 2800, mean_current_a: 0.37, c_rate: 0.123, mean_temperature_c: 25, cutoff_v: 3 },
  retention: { status: "ok", reason: null, latest_pct: 96.4, definition: "Not absolute SoH" },
  capacity_series: [
    { index: 1, session_id: 1, start_ts: "2026-11-19T00:00:00Z", capacity_mah: 2800, uncertainty_mah: 1, energy_wh: 10, mean_current_a: 0.37, mean_temperature_c: 25, retention_pct: 100 },
    { index: 2, session_id: 3, start_ts: "2026-11-20T00:00:00Z", capacity_mah: 2700, uncertainty_mah: 1, energy_wh: 9.6, mean_current_a: 0.37, mean_temperature_c: 25, retention_pct: 96.4 },
  ],
  sessions: [{ session_id: 2, start_ts: "2026-11-19T06:00:00Z", end_ts: null, status: "ended", end_reason: "telemetry_gap",
    charge_mah: 500, energy_wh: 1.8, duration_s: 4800, mean_current_a: 0.37, c_rate: 0.123, mean_temperature_c: 25, mean_loaded_voltage_v: 3.6,
    full_test: false, full_test_issues: ["interrupted (telemetry gap)"], comparable: false, comparability_issues: [], is_reference: false }],
};
const rules = { current_tolerance_frac: 0.15, temperature_tolerance_c: 5, max_uncertainty_frac: 0.02, min_trend_points: 2, full_test: "rested full charge -> cutoff" };

const cyc = (n: number, drop: number, mah: number): QuickCycle => ({
  number: n, test_uid: `T${n}`, completed_at: "2026-11-19T01:00:00Z", duration_s: 300,
  initial_voltage_v: 4.0, final_voltage_v: 4.0 - drop, voltage_drop_v: drop, mean_voltage_v: 3.9, mean_current_a: 0.36,
  mean_power_w: 1.4, max_temperature_c: 26.5, temperature_rise_c: 1.2, delivered_mah: mah, delivered_wh: mah * 0.0039,
});
const quick: QuickOverview = {
  batteries: [
    { battery_id: "BAT-A-3000", count: 3, target: 3, duration_s: 300, next_test_number: null, complete: true, definition: "",
      cycles: [cyc(1, 0.12, 30.1), cyc(2, 0.13, 29.9), cyc(3, 0.14, 30.0)], attempts: [], summary: {}, not_counted: { interrupted: 1 } },
    { battery_id: "BAT-B-3000", count: 0, target: 3, duration_s: 300, next_test_number: 1, complete: false, definition: "",
      cycles: [], attempts: [], summary: {}, not_counted: {} },
  ],
  comparison: {
    rows: [
      { battery_id: "BAT-A-3000", count: 3, target: 3, delivered_mah: 30.0, delivered_mah_range: 0.2, delivered_mah_diff_pct: 0,
        voltage_drop_v: 0.13, voltage_drop_v_range: 0.02, voltage_drop_v_diff_pct: 0 },
      { battery_id: "BAT-B-3000", count: 0, target: 3, delivered_mah: null },
    ],
    group_mean: {}, note: "Averages over each battery's first 3 valid Quick Test Cycles",
  },
  model_status: { validated_model: false, message: "No validated model" },
  protocol: { duration_s: 300, target_per_battery: 3 },
};
const bat = (id: string, extra: Partial<BatterySummary> = {}) => ({ battery_id: id, managed: 1, archived: false, source: "hardware", ...extra }) as unknown as BatterySummary;

function mockApi(agingBody: Aging, urls: string[] = []) {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = String(input);
    urls.push(url);
    const body = url.includes("/api/quick-tests") ? quick : url.includes("/api/aging/") ? { ...agingBody, rules } : { points: [], total: 0 };
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  });
}

describe("Battery Analysis page", () => {
  it("compares A/B/C Quick Test averages with ranges and never invents values for an untested battery", async () => {
    mockApi(withData);
    render(<AgingPage battery={bat("BAT-A-3000")} batteries={[bat("BAT-A-3000"), bat("BAT-B-3000")]} />);
    await screen.findByText("Quick Test comparison");
    expect(screen.getByText("3 / 3")).toBeTruthy();
    expect(screen.getByText("0 / 3")).toBeTruthy();
    expect(screen.getByText("30.0")).toBeTruthy();       // A: average mAh over 3 tests
    expect(screen.getByText(/±0\.1/)).toBeTruthy();       // half-range
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);   // B: no value, not zero
  });

  it("plots per-test trends and shows measured full capacity separately from Quick Tests", async () => {
    mockApi(withData);
    render(<AgingPage battery={bat("BAT-A-3000")} batteries={[bat("BAT-A-3000")]} />);
    await screen.findByText("Voltage drop per test (V)");
    expect(screen.getByText("Discharged per test (mAh)")).toBeTruthy();
    await screen.findByText(/Measured full capacity per comparable full test/);
    expect(screen.getByText("2700.0")).toBeTruthy();                   // throughput tile
    expect(screen.getByText(/96\.4 % of the first measured full test/)).toBeTruthy();
  });

  it("shows an empty state for a profile without valid Quick Tests", async () => {
    mockApi(empty("BAT-B-3000", 3000));
    render(<AgingPage battery={bat("BAT-B-3000")} batteries={[bat("BAT-B-3000")]} />);
    await screen.findByText(/No valid Quick Test Cycles for BAT-B-3000 yet/);
    await screen.findByText("Insufficient data.");
  });

  it("shows an archived record read-only with its history open", async () => {
    const urls: string[] = [];
    mockApi(withData, urls);
    render(<AgingPage battery={bat("CELL-18650-01", { managed: 0 })} batteries={[]} />);
    await screen.findByText("Archived record");
    expect(screen.getByText("Archived (read-only)")).toBeTruthy();
    expect(screen.queryByText(/Measured discharge throughput/)).toBeNull();
    await waitFor(() => expect(urls.some((u) => u.includes("/api/battery/CELL-18650-01/history") || u.includes("CELL-18650-01"))).toBe(true));
  });
});

describe("Battery Analysis: partial-session throughput (Battery A: 30.0 + 4.1 + 17.0 mAh)", () => {
  const sess = (id: number, mah: number) => ({
    session_id: id, start_ts: "2026-11-19T01:00:00Z", end_ts: "2026-11-19T01:05:00Z", status: "ended", end_reason: "load_stopped",
    charge_mah: mah, energy_wh: mah * 0.00385, duration_s: mah * 7.2, mean_current_a: 0.5, c_rate: 0.167,
    mean_temperature_c: 25, mean_loaded_voltage_v: 3.85, full_test: false,
    full_test_issues: ["did not start from a rested full charge (>= 4.15 V)", "did not reach the cutoff voltage (3 V): partial discharge"],
    comparable: false, comparability_issues: [], is_reference: false, n_gaps: 0, uncertainty_mah: 0.3,
    counted_in_throughput: true, throughput_excluded: null,
  });
  const a: Aging = {
    ...empty("BAT-A-3000", 3000),
    totals: { n_sessions: 3, delivered_mah: 51.1, delivered_wh: 0.197, discharge_duration_s: 396, equivalent_full_cycles: 51.1 / 3000 },
    sessions: [sess(1, 30.0), sess(2, 4.1), sess(3, 17.0)],
  };

  it("shows 51.1 mAh throughput and EFC while full capacity stays insufficient", async () => {
    mockApi(a);
    render(<AgingPage battery={bat("BAT-A-3000")} batteries={[bat("BAT-A-3000")]} />);
    await screen.findByText(/Measured discharge throughput/);
    expect(screen.getByText("51.1")).toBeTruthy();
    expect(screen.getByText("0.017")).toBeTruthy();                       // EFC = 51.1 / 3000
    expect(screen.getByText("Insufficient data.")).toBeTruthy();
    const det = screen.getByText("All discharge sessions (3)").closest("details")!;
    det.open = true;
    fireEvent(det, new Event("toggle"));
    await waitFor(() => expect(screen.getAllByText("partial").length).toBe(3));
    expect(screen.queryByText("full test")).toBeNull();
  });
});
