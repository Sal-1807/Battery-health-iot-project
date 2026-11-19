import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import AgingPage from "../pages/Aging";
import type { Aging } from "../lib/api";

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
const rows = [withData, empty("BAT-B-3000", 3000), empty("BAT-C-2500", 2500)];
const rules = { current_tolerance_frac: 0.15, temperature_tolerance_c: 5, max_uncertainty_frac: 0.02, min_trend_points: 2, full_test: "rested full charge -> cutoff" };

function mockApi(urls: string[]) {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = String(input);
    urls.push(url);
    const id = url.match(/\/api\/aging\/([A-Z0-9-]+)/)?.[1];
    const body = id ? { ...rows.find((r) => r.battery_id === id)!, rules } : { batteries: rows, rules };
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  });
}

describe("Aging analytics page", () => {
  it("compares batteries normalised by their own rating and keeps RUL unavailable", async () => {
    mockApi([]);
    render(<AgingPage />);
    await waitFor(() => expect(screen.getByText("2500 mAh")).toBeTruthy());
    await waitFor(() => expect(screen.getAllByText("0.900").length).toBe(2));   // EFC for A: comparison row + detail tile
    expect(screen.getByText("96.4 %")).toBeTruthy();                   // retention, A only
    expect(screen.getAllByText("Insufficient data").length).toBeGreaterThanOrEqual(2);  // B and C
    expect(screen.getAllByText("Unavailable").length).toBe(3);
    expect(screen.getByText(/not absolute SoH/)).toBeTruthy();
  });

  it("shows capacity, retention and why sessions are excluded for the selected battery", async () => {
    mockApi([]);
    render(<AgingPage />);
    await screen.findByText(/Measured full capacity per comparable full test/);
    expect(screen.getByText("2700.0")).toBeTruthy();                   // delivered mAh tile
    expect(screen.getByText(/interrupted \(telemetry gap\)/)).toBeTruthy();
    expect(screen.getByText(/delivered ÷ 3000 mAh label rating/)).toBeTruthy();
  });

  it("shows Insufficient data for a battery without comparable tests", async () => {
    const urls: string[] = [];
    mockApi(urls);
    render(<AgingPage />);
    await screen.findByText(/Measured full capacity per comparable full test/);
    fireEvent.click(screen.getByRole("radio", { name: "BAT-C-2500" }));
    await waitFor(() => expect(urls.some((u) => u.endsWith("/api/aging/BAT-C-2500"))).toBe(true));
    await waitFor(() => expect(screen.getAllByText("Insufficient data").length).toBeGreaterThanOrEqual(4));
    expect(screen.getByText(/delivered ÷ 2500 mAh label rating/)).toBeTruthy();
  });
});

describe("Aging: partial-session throughput (Battery A: 30.0 + 4.1 + 17.0 mAh)", () => {
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
    throughput: { definition: "not the battery's capacity", total_mah: 51.1, total_wh: 0.197, duration_s: 396,
                  equivalent_full_cycles: 51.1 / 3000, uncertainty_mah: 0.9, n_sessions: 3, n_partial_sessions: 3,
                  partial_mah: 51.1, partial_wh: 0.197, n_full_tests: 0, full_mah: 0, in_progress_mah: 0,
                  sessions_with_gaps: 0, overlapping_sessions_excluded: [] },
    sessions: [sess(1, 30.0), sess(2, 4.1), sess(3, 17.0)],
  };

  it("shows 51.1 mAh throughput and EFC while full capacity stays insufficient and cycles 0", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      const body = url.includes("/api/aging/") ? { ...a, rules } : { batteries: [a, empty("BAT-B-3000", 3000), empty("BAT-C-2500", 2500)], rules };
      return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
    });
    render(<AgingPage />);
    await screen.findByText(/Measured discharge throughput/);
    expect(screen.getAllByText("51.1").length).toBeGreaterThanOrEqual(1);                   // throughput tile
    expect(screen.getByText("3 partial (51.1 mAh) + 0 full (0.0 mAh)")).toBeTruthy();
    expect(screen.getAllByText("0.017").length).toBeGreaterThanOrEqual(1);                  // EFC = 51.1 / 3000
    expect(screen.getByText("3 (3 / 0)")).toBeTruthy();                                     // sessions (partial / full)
    expect(screen.getByText(/Measured full capacity/, { selector: "h3" })).toBeTruthy();
    expect(screen.getAllByText("Insufficient data").length).toBeGreaterThanOrEqual(2);      // capacity + retention
    expect(screen.getAllByText("partial").length).toBe(3);                                  // session table types
    expect(screen.queryByText("full test")).toBeNull();
  });
});
