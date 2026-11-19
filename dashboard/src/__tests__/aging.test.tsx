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
    expect(screen.getAllByText("0.900").length).toBe(2);              // EFC for A: comparison row + detail tile
    expect(screen.getByText("96.4 %")).toBeTruthy();                   // retention, A only
    expect(screen.getAllByText("Insufficient data").length).toBeGreaterThanOrEqual(2);  // B and C
    expect(screen.getAllByText("Unavailable").length).toBe(3);
    expect(screen.getByText(/not absolute SoH/)).toBeTruthy();
  });

  it("shows capacity, retention and why sessions are excluded for the selected battery", async () => {
    mockApi([]);
    render(<AgingPage />);
    await screen.findByText(/Measured capacity per comparable full test/);
    expect(screen.getByText("2700.0")).toBeTruthy();                   // delivered mAh tile
    expect(screen.getByText(/interrupted \(telemetry gap\)/)).toBeTruthy();
    expect(screen.getByText(/delivered ÷ 3000 mAh label rating/)).toBeTruthy();
  });

  it("shows Insufficient data for a battery without comparable tests", async () => {
    const urls: string[] = [];
    mockApi(urls);
    render(<AgingPage />);
    await screen.findByText(/Measured capacity per comparable full test/);
    fireEvent.click(screen.getByRole("radio", { name: "BAT-C-2500" }));
    await waitFor(() => expect(urls.some((u) => u.endsWith("/api/aging/BAT-C-2500"))).toBe(true));
    await waitFor(() => expect(screen.getAllByText("Insufficient data").length).toBeGreaterThanOrEqual(4));
    expect(screen.getByText(/delivered ÷ 2500 mAh label rating/)).toBeTruthy();
  });
});
