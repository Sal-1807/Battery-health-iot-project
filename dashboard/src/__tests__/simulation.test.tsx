// Validates dashboard views against API responses captured from the end-to-end
// simulation run (python -m iot.simulation.run --fixture ...). Every expected
// value is read from the simulation results, except the analytic truths
// (1000 mAh, 4.000 Wh), which come from physics, not from the code under test.
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import fixture from "./fixtures/simulation.json";
import App from "../App";
import Batteries from "../pages/Batteries";
import Aging from "../pages/Aging";
import TestManager from "../pages/TestManager";
import { fmt } from "../lib/format";
import type { Aging as AgingT, BatterySummary, BatteryTest, CompareRow } from "../lib/api";

const fx = fixture as unknown as Record<string, unknown> & {
  facts: { analytic: { charge_mah: number; energy_wh: number; test_uid: string }; full: { test_uid: string; retention_pct: number } };
};

beforeAll(() => {
  globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} } as unknown as typeof ResizeObserver;
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  window.location.hash = "";
});

function serveFixture() {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const u = new URL(String(input), "http://x");
    let body = fx[u.pathname] as unknown;
    if (u.pathname === "/api/tests" && u.searchParams.get("battery_id")) {
      const all = (fx["/api/tests"] as { tests: BatteryTest[] }).tests;
      body = { tests: all.filter((t) => t.battery_id === u.searchParams.get("battery_id")) };
    }
    if (body === undefined) return new Response(JSON.stringify({ detail: "not in fixture" }), { status: 404 });
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  });
}

const compare = (fx["/api/batteries/compare"] as { batteries: CompareRow[] }).batteries;
const tests = (fx["/api/tests"] as { tests: BatteryTest[] }).tests;

describe("dashboard against simulation results", () => {
  it("the analytic run is exactly what physics says", () => {
    expect(fx.facts.analytic.charge_mah).toBeCloseTo(1000, 0);
    expect(fx.facts.analytic.energy_wh).toBeCloseTo(4.0, 2);
  });

  it("shows a SIMULATION MODE indicator whenever the simulation backend is displayed", async () => {
    serveFixture();
    render(<App />);
    await screen.findByText(/SIMULATION MODE — every value on this dashboard is synthetic/);
  });

  it("Batteries comparison matches simulated EFC and completed cycles, labelled SIMULATED", async () => {
    serveFixture();
    render(<Batteries selectBattery={() => {}} />);
    await screen.findAllByText("SIM-BAT-C-2500");
    expect(screen.getAllByText("SIMULATED").length).toBe(3);
    for (const r of compare) {
      const row = screen.getAllByText(r.battery_id).map((el) => el.closest("tr")).find(Boolean)!;
      expect(within(row).getByText(fmt(r.stats.equivalent_full_cycles, 3))).toBeTruthy();
      expect(within(row).getByText(`${fmt(r.rated_capacity_mah, 0)} mAh`)).toBeTruthy();
    }
    const c = compare.find((r) => r.battery_id === "SIM-BAT-C-2500")!;
    expect(c.stats.completed_cycles).toBe(2);
    expect(compare.find((r) => r.battery_id === "SIM-BAT-B-3000")!.stats.completed_cycles).toBe(0);  // partial only
  });

  it("Battery Analysis shows the simulated retention and throughput for SIM-BAT-C-2500", async () => {
    serveFixture();
    const all = (fx["/api/batteries"] as { batteries: BatterySummary[] }).batteries;
    const c = all.find((b) => b.battery_id === "SIM-BAT-C-2500")!;
    render(<Aging battery={c} batteries={all} selectBattery={() => {}} />);
    const agingC = fx["/api/aging/SIM-BAT-C-2500"] as AgingT;
    await screen.findByText("Quick Test comparison");
    await waitFor(() => expect(screen.getByText(fmt(agingC.totals.delivered_mah, 1))).toBeTruthy());
    expect(screen.getByText(new RegExp(`Latest retention: ${fmt(agingC.retention.latest_pct, 1)} %`))).toBeTruthy();
    expect(agingC.retention.latest_pct).toBeCloseTo(fx.facts.full.retention_pct, 6);
    expect(screen.getByText(/throughput ÷ 2500 mAh rating/)).toBeTruthy();
    expect(screen.getByText(/No valid Quick Test Cycles for SIM-BAT-C-2500 yet/)).toBeTruthy();   // supervised runs are not Quick Tests
  });

  it("Quick Tests history matches the simulated tests and CSV export is offered", async () => {
    serveFixture();
    render(<TestManager />);
    const analytic = tests.find((t) => t.test_uid === fx.facts.analytic.test_uid)!;
    const full = tests.find((t) => t.test_uid === fx.facts.full.test_uid)!;
    const row = (await screen.findByText(analytic.test_uid)).closest("tr")!;
    expect(within(row).getByText(`${fmt(analytic.results.delivered_mah, 1)} mAh`)).toBeTruthy();
    expect(within(row).getByText("1000.0 mAh")).toBeTruthy();
    expect(within(row).getByText("4.000 Wh")).toBeTruthy();
    expect(within(row).getByText(/supervised \(legacy\) · partial discharge \(not a full cycle\)/)).toBeTruthy();
    const fullRow = screen.getByText(full.test_uid).closest("tr")!;
    expect(within(fullRow).getByText(/full discharge \(valid for capacity/)).toBeTruthy();
    expect(screen.getByText("Export CSV").closest("a")!.getAttribute("href")).toBe("/api/tests/export.csv");
    expect(screen.getByText(/do not control hardware/)).toBeTruthy();
  });
});
