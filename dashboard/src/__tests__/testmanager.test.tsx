import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import TestManager from "../pages/TestManager";
import type { BatteryTest, TestState } from "../lib/api";

beforeAll(() => {
  globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} } as unknown as typeof ResizeObserver;
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const mk = (uid: string, battery: string, state: TestState, over: Partial<BatteryTest> = {}): BatteryTest => ({
  test_uid: uid, battery_id: battery, device_id: "esp32-devkit-01", state, created_at: "2026-11-19T00:00:00Z",
  started_at: null, ended_at: null, notes: null, abort_reason: null, checklist: {}, checklist_problems: ["supervisor name missing"],
  history: [], results: { available: false, reason: "test has not started" }, results_frozen: false,
  validity: { valid_for_full_capacity: false, classification: "not started", checks: [] },
  hardware_state: { description: "load current 0.370 A measured: a load appears to be connected", last_reading_ts: null, battery_selected_on: "esp32-devkit-01" },
  hardware_notice: "Software test state only.", disconnect_warning: "Physically disconnect the discharge load now.", soh: "unavailable", rul: "unavailable",
  ...over,
});
const done = mk("T-20261119-BAT-C-2500-001", "BAT-C-2500", "completed", {
  results: { available: true, delivered_mah: 83.3, delivered_wh: 0.295, elapsed_s: 610, load_on_s: 600, initial_voltage_v: 4.18,
             final_voltage_v: 3.4, min_loaded_voltage_v: 3.6, mean_current_a: 0.5, gaps: [{ from: "2026-11-19T00:03:00Z", to: "2026-11-19T00:04:45Z", duration_s: 105 }],
             missing_measurements: { voltage_v: 0, current_a: 0, temperature_c: 0, readings_with_any_missing: 0 }, paused: false, battery_stayed_selected: true },
  validity: { valid_for_full_capacity: false, classification: "partial discharge (not a full cycle)",
              checks: [{ check: "reached cutoff", ok: false, detail: "lowest loaded voltage 3.6 V vs cutoff 3.0 V" }] },
});

function mockApi(state: { running: BatteryTest }, calls: { url: string; init?: RequestInit }[]) {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    calls.push({ url, init });
    let body: unknown;
    if (url.match(/\/api\/tests\/[^/]+\/(start|pause|resume|complete|abort)$/)) {
      const action = url.split("/").pop()!;
      const target = { start: "running", pause: "paused", resume: "running", complete: "completed", abort: "aborted" }[action] as TestState;
      state.running = { ...state.running, state: target };
      body = { state: target, hardware_notice: "Software test state only.", ...(action !== "start" && action !== "resume" ? { warning: "Physically disconnect the discharge load now. The software cannot do it." } : {}) };
    } else if (url.match(/\/api\/tests\/T-[^/]+$/)) {
      body = url.endsWith(done.test_uid) ? done : state.running;
    } else {
      const all = [state.running, done];
      const f = new URL(url, "http://x").searchParams.get("battery_id");
      body = { tests: f ? all.filter((t) => t.battery_id === f) : all };
    }
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  });
}

describe("Test Manager page", () => {
  it("separates software test state from measured hardware state and always warns", async () => {
    mockApi({ running: mk("T-20261119-BAT-A-3000-001", "BAT-A-3000", "running") }, []);
    render(<TestManager />);
    expect(screen.getByText(/does not control hardware/)).toBeTruthy();
    expect(screen.getByText(/physically disconnect the discharge load/i)).toBeTruthy();
    await screen.findByText("Test state (software record)");
    expect(screen.getByText("Hardware state (measured, not controlled)")).toBeTruthy();
    expect(screen.getByText(/a load appears to be connected/)).toBeTruthy();
    expect(screen.getAllByText("Running").length).toBeGreaterThan(0);
  });

  it("only enables valid transitions and shows the disconnect warning after pausing", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    mockApi({ running: mk("T-20261119-BAT-A-3000-001", "BAT-A-3000", "running") }, calls);
    render(<TestManager />);
    await screen.findByText("Test state (software record)");
    expect((screen.getByRole("button", { name: "Start test record" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "Resume" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Pause" }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/pause"))).toBe(true));
    await screen.findByText(/⚠ Physically disconnect the discharge load now/);
  });

  it("shows a partial discharge as not a full cycle, with gaps, in the history", async () => {
    mockApi({ running: mk("T-20261119-BAT-A-3000-001", "BAT-A-3000", "ready") }, []);
    render(<TestManager />);
    await screen.findByText(done.test_uid);
    expect(screen.getByText("partial discharge (not a full cycle)")).toBeTruthy();
    fireEvent.click(screen.getAllByRole("button", { name: "Open" })[1]);
    await screen.findByText("1 telemetry gap(s)");
    expect(screen.getByText("not valid for full-capacity analysis")).toBeTruthy();
    expect(screen.getByText(/SoH \/ RUL: unavailable/)).toBeTruthy();
  });

  it("shows the checklist for a not-started test and filters history per battery", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    mockApi({ running: mk("T-20261119-BAT-A-3000-001", "BAT-A-3000", "not_started") }, calls);
    render(<TestManager />);
    await screen.findByText("Pre-test checklist");
    expect(screen.getByText(/the software does not cut off/)).toBeTruthy();
    fireEvent.click(screen.getByRole("radio", { name: "BAT-C-2500" }));
    await waitFor(() => expect(calls.some((c) => c.url.includes("/api/tests?battery_id=BAT-C-2500"))).toBe(true));
    expect(screen.getByText("Export CSV").closest("a")!.getAttribute("href")).toContain("battery_id=BAT-C-2500");
  });
});
