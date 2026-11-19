import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import TestManager, { createBlockedReason } from "../pages/TestManager";
import type { BatterySummary, BatteryTest, QuickOverview, TestState } from "../lib/api";

beforeAll(() => {
  globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} } as unknown as typeof ResizeObserver;
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

type Q = NonNullable<BatteryTest["quick_test"]>;
const quick = (over: Partial<Q> = {}): Q => ({
  status: "in_progress", label: "In progress", checks: [], number: null, target: 3, duration_s: 300,
  elapsed_s: 120, remaining_s: 180, auto_ended: false, disconnect_confirmed_at: null, needs_disconnect_confirmation: false, ...over,
});
const mk = (uid: string, battery: string, state: TestState, over: Partial<BatteryTest> = {}): BatteryTest => ({
  test_uid: uid, battery_id: battery, device_id: "esp32-devkit-01", state, created_at: "2026-11-19T00:00:00Z",
  started_at: null, ended_at: null, notes: null, abort_reason: null, checklist: {}, checklist_problems: [],
  history: [], results: { available: false, reason: "test has not started" }, results_frozen: false,
  validity: { valid_for_full_capacity: false, classification: "not started", checks: [] },
  hardware_state: { description: "load current 0.370 A measured: a load appears to be connected", last_reading_ts: null, battery_selected_on: "esp32-devkit-01" },
  hardware_notice: "Software test state only.", disconnect_warning: "Physically disconnect the discharge load now.", soh: "unavailable", rul: "unavailable",
  kind: "quick", duration_s: 300, quick_test: quick(), ...over,
});
const counted = mk("T-20261119-BAT-C-2500-001", "BAT-C-2500", "completed", {
  results: { available: true, delivered_mah: 30.4, delivered_wh: 0.118, elapsed_s: 300, initial_voltage_v: 4.05, final_voltage_v: 3.92, mean_current_a: 0.365, mean_power_w: 1.42, max_temperature_c: 26.1 },
  quick_test: quick({ status: "counted", label: "Valid Quick Test Cycle", number: 1, elapsed_s: 300, remaining_s: 0, auto_ended: true, disconnect_confirmed_at: "2026-11-19T00:06:00Z",
    checks: [{ check: "full 300 s window", ok: true, detail: "300 s recorded" }] }),
});
const gap = mk("T-20261119-BAT-C-2500-002", "BAT-C-2500", "completed", {
  results: { available: true, delivered_mah: 22.0, delivered_wh: 0.085, elapsed_s: 300, initial_voltage_v: 4.0, final_voltage_v: 3.9 },
  quick_test: quick({ status: "telemetry_gap", label: "Telemetry gap — not counted", elapsed_s: 300, remaining_s: 0, disconnect_confirmed_at: "2026-11-19T00:16:00Z",
    checks: [{ check: "no telemetry gaps", ok: false, detail: "1 gap of 105 s" }] }),
});
const qb = (id: string, cycles: number) => ({
  battery_id: id, count: cycles, target: 3, duration_s: 300, next_test_number: cycles >= 3 ? null : cycles + 1, complete: cycles >= 3, definition: "",
  cycles: Array.from({ length: cycles }, (_, i) => ({ ...counted.results, number: i + 1, test_uid: `X${i}`, completed_at: "", duration_s: 300 })) as unknown as QuickOverview["batteries"][0]["cycles"],
  attempts: [], summary: {}, not_counted: id === "BAT-C-2500" ? { telemetry_gap: 1 } : {},
});
const overview = { batteries: [qb("BAT-A-3000", 3), qb("BAT-B-3000", 0), qb("BAT-C-2500", 1)], comparison: { rows: [], group_mean: {}, note: "" },
  model_status: { validated_model: false, message: "" }, protocol: { duration_s: 300, target_per_battery: 3 } };

function mockApi(state: { current: BatteryTest }, calls: { url: string; init?: RequestInit }[] = [], refuseDisconnect = false) {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    calls.push({ url, init });
    let body: unknown;
    let status = 200;
    if (url.includes("/api/quick-tests")) body = overview;
    else if (url.endsWith("/confirm-disconnect")) {
      if (refuseDisconnect) { status = 409; body = { detail: "Load current 0.36 A is still being measured. Disconnect the load first." }; }
      else {
        state.current = { ...state.current, quick_test: { ...state.current.quick_test!, needs_disconnect_confirmation: false, disconnect_confirmed_at: "now" } };
        body = { ok: true, note: "Recorded. This is your confirmation, not a hardware action." };
      }
    } else if (url.match(/\/api\/tests\/[^/]+\/(start|complete|abort)$/)) {
      const action = url.split("/").pop()!;
      const target = { start: "running", complete: "completed", abort: "aborted" }[action] as TestState;
      state.current = { ...state.current, state: target };
      body = { state: target };
    } else if (url.match(/\/api\/tests\/T-[^/?]+$/)) {
      body = [counted, gap].find((t) => url.endsWith(t.test_uid)) ?? state.current;
    } else if (url.match(/\/api\/tests(\?|$)/) && init?.method === "POST") {
      body = { test_uid: "T-NEW" };
    } else {
      const all = [state.current, counted, gap];
      const f = new URL(url, "http://x").searchParams.get("battery_id");
      body = { tests: f ? all.filter((t) => t.battery_id === f) : all };
    }
    return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
  });
}

describe("Quick Tests page", () => {
  it("always states that software cannot disconnect the load, and shows 3-test progress per battery", async () => {
    mockApi({ current: mk("T-20261119-BAT-A-3000-001", "BAT-B-3000", "running") });
    render(<TestManager />);
    expect(screen.getByText(/They do not control hardware/)).toBeTruthy();
    expect(screen.getByText(/physically disconnect the discharge load/i)).toBeTruthy();
    await screen.findByText("3 / 3 done");
    expect(screen.getByText("Next: Test 1/3")).toBeTruthy();       // BAT-B
    expect(screen.getByText("Next: Test 2/3")).toBeTruthy();       // BAT-C
    expect(screen.getByText("Not counted: 1 telemetry gap")).toBeTruthy();
    await screen.findByText("Hardware (measured, not controlled)");
  });

  it("counts down the 300 s window and says the timer does not stop the current", async () => {
    mockApi({ current: mk("T-20261119-BAT-B-3000-001", "BAT-B-3000", "running") });
    render(<TestManager />);
    await screen.findByText("Software recording window");
    expect(screen.getByText(/does/).closest("p")!.textContent).toMatch(/does not\s+disconnect the battery or stop the discharge current/);
    const bar = screen.getByRole("progressbar");
    expect(Number(bar.getAttribute("aria-valuenow"))).toBeGreaterThanOrEqual(120);
    expect(bar.getAttribute("aria-valuemax")).toBe("300");
    await waitFor(() => expect(Number(bar.getAttribute("aria-valuenow"))).toBeGreaterThanOrEqual(121), { timeout: 2500 });   // ticks locally
    expect((screen.getByRole("button", { name: "Start recording" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "Stop early" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("after the window ends, requires a physical-disconnect confirmation before the next test", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    const ended = mk("T-20261119-BAT-B-3000-001", "BAT-B-3000", "completed", {
      quick_test: quick({ status: "counted", label: "Valid", number: 1, elapsed_s: 300, remaining_s: 0, auto_ended: true, needs_disconnect_confirmation: true }),
    });
    mockApi({ current: ended }, calls);
    render(<TestManager />);
    await screen.findByText(/Test ended — physically disconnect the battery \/ load now/);
    expect(screen.getByText(/The 300-second recording window ended/)).toBeTruthy();
    expect(screen.queryByText("Software recording window")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "I have physically disconnected the load" }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/confirm-disconnect") && c.init?.method === "POST")).toBe(true));
    await waitFor(() => expect(screen.queryByText(/Test ended — physically disconnect/)).toBeNull());
  });

  it("shows the backend's refusal when current is still measured", async () => {
    const ended = mk("T-20261119-BAT-B-3000-001", "BAT-B-3000", "completed", {
      quick_test: quick({ status: "counted", label: "Valid", number: 1, elapsed_s: 300, remaining_s: 0, auto_ended: true, needs_disconnect_confirmation: true }),
    });
    mockApi({ current: ended }, [], true);
    render(<TestManager />);
    fireEvent.click(await screen.findByRole("button", { name: "I have physically disconnected the load" }));
    await screen.findByText(/is still being measured/);
    expect(screen.getByText(/Test ended — physically disconnect/)).toBeTruthy();
  });

  it("lists every test with valid and not-counted results, and opens validity checks", async () => {
    mockApi({ current: mk("T-20261119-BAT-A-3000-009", "BAT-A-3000", "not_started", { quick_test: quick({ status: "not_started", label: "Not started", elapsed_s: null }) }) });
    render(<TestManager />);
    await screen.findByText(counted.test_uid);
    expect(screen.getByText("Valid 1/3")).toBeTruthy();
    expect(screen.getAllByText("Telemetry gap — not counted").length).toBeGreaterThan(0);
    expect(screen.getByText("Pre-test checklist")).toBeTruthy();
    fireEvent.click(screen.getAllByRole("button", { name: "Open" })[2]);
    await screen.findByText("Quick Test validity");
    expect(screen.getByText("no telemetry gaps")).toBeTruthy();
    expect(screen.getByText(/not a full charge–discharge cycle/)).toBeTruthy();
  });

  it("creates a 5-minute Quick Test for the chosen battery and filters history", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    mockApi({ current: mk("T-20261119-BAT-A-3000-001", "BAT-A-3000", "completed", { quick_test: quick({ status: "counted", label: "Valid", number: 3 }) }) }, calls);
    render(<TestManager />);
    const sel = (await screen.findByLabelText("New test battery")) as HTMLSelectElement;
    await waitFor(() => expect(sel.options.length).toBe(3));
    fireEvent.change(sel, { target: { value: "BAT-C-2500" } });
    fireEvent.click(screen.getByRole("button", { name: "New 5-minute Quick Test" }));
    await waitFor(() => expect(calls.some((c) => c.init?.method === "POST" && /\/api\/tests$/.test(c.url))).toBe(true));
    const post = calls.find((c) => c.init?.method === "POST" && /\/api\/tests$/.test(c.url))!;
    expect(JSON.parse(String(post.init!.body))).toMatchObject({ battery_id: "BAT-C-2500", kind: "quick" });
    fireEvent.click(screen.getByRole("radio", { name: "BAT-C-2500" }));
    await waitFor(() => expect(calls.some((c) => c.url.includes("/api/tests?battery_id=BAT-C-2500"))).toBe(true));
    expect(screen.getByText("Export CSV").closest("a")!.getAttribute("href")).toContain("battery_id=BAT-C-2500");
  });
});

describe("New 5-minute Quick Test button (regression: blank selector, disabled button)", () => {
  const prof = (id: string, extra: Partial<BatterySummary> = {}) => ({ battery_id: id, managed: 1, archived: false, source: "hardware", ...extra }) as unknown as BatterySummary;
  const profiles = [prof("BAT-A-3000"), prof("BAT-B-3000"), prof("BAT-C-2500")];
  const legacyAborted = mk("T-20261119-BAT-A-3000-001", "BAT-A-3000", "aborted", {
    kind: undefined, duration_s: null, quick_test: null, started_at: "2026-11-19T00:00:00Z", abort_reason: "aborted by operator",
  });

  // A backend started before /api/quick-tests existed answers 404 for it.
  function mockOldBackend(calls: { url: string; init?: RequestInit }[], tests: BatteryTest[] = [legacyAborted]) {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      calls.push({ url, init });
      if (url.includes("/api/quick-tests")) return new Response(JSON.stringify({ detail: "Not Found" }), { status: 404 });
      if (init?.method === "POST") return new Response(JSON.stringify({ test_uid: "T-NEW", state: "not_started" }), { status: 200 });
      if (url.match(/\/api\/tests\/T-[^/?]+$/)) return new Response(JSON.stringify(tests[0] ?? legacyAborted), { status: 200 });
      const f = new URL(url, "http://x").searchParams.get("battery_id");
      return new Response(JSON.stringify({ tests: f ? tests.filter((t) => t.battery_id === f) : tests }), { status: 200 });
    });
  }

  it("lists the three profiles, preselects the viewed battery and is enabled despite an aborted legacy test", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    mockOldBackend(calls);
    render(<TestManager battery={profiles[0]} batteries={[...profiles, prof("CELL-18650-01", { managed: 0, archived: true })]} batteriesLoading={false} />);
    const sel = screen.getByLabelText("New test battery") as HTMLSelectElement;
    expect([...sel.options].map((o) => o.value)).toEqual(["BAT-A-3000", "BAT-B-3000", "BAT-C-2500"]);   // archived ID excluded
    expect(sel.value).toBe("BAT-A-3000");
    await screen.findByText(/Quick Test overview unavailable/);                 // the failure is visible, not silent
    const btn = screen.getByRole("button", { name: "New 5-minute Quick Test" }) as HTMLButtonElement;
    await waitFor(() => expect(calls.some((c) => c.url.includes("/api/tests?battery_id=BAT-A-3000"))).toBe(true));
    expect(btn.disabled).toBe(false);
    fireEvent.click(btn);
    await waitFor(() => expect(calls.some((c) => c.init?.method === "POST")).toBe(true));
    expect(JSON.parse(String(calls.find((c) => c.init?.method === "POST")!.init!.body))).toMatchObject({ battery_id: "BAT-A-3000", kind: "quick" });
  });

  it("explains why it is disabled when no profiles can be loaded", async () => {
    mockOldBackend([]);
    render(<TestManager batteries={[]} batteriesLoading={false} />);
    await screen.findByText(/Battery profiles could not be loaded .*restart it/);
    expect((screen.getByRole("button", { name: "New 5-minute Quick Test" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("stays disabled, with the reason, while a physical-disconnect confirmation is pending", async () => {
    const waiting = mk("T-20261119-BAT-B-3000-002", "BAT-B-3000", "aborted", {
      quick_test: quick({ status: "interrupted", label: "Interrupted", needs_disconnect_confirmation: true }),
    });
    mockOldBackend([], [waiting]);
    render(<TestManager battery={profiles[1]} batteries={profiles} batteriesLoading={false} />);
    await screen.findByText(/Safety step pending: confirm that the load was physically disconnected after T-20261119-BAT-B-3000-002/);
    expect((screen.getByRole("button", { name: "New 5-minute Quick Test" }) as HTMLButtonElement).disabled).toBe(true);
    // another battery is not blocked by it
    fireEvent.change(screen.getByLabelText("New test battery"), { target: { value: "BAT-C-2500" } });
    await waitFor(() => expect((screen.getByRole("button", { name: "New 5-minute Quick Test" }) as HTMLButtonElement).disabled).toBe(false));
  });

  it("reason helper: aborted tests never block, pending disconnect does", () => {
    const base = { options: ["BAT-A-3000"], selected: "BAT-A-3000", profilesLoading: false, quickError: null, pendingUid: null, busy: false };
    expect(createBlockedReason(base)).toBeNull();
    expect(createBlockedReason({ ...base, pendingUid: "T-1" })).toMatch(/physically disconnected after T-1/);
    expect(createBlockedReason({ ...base, options: [], selected: "", quickError: "Not Found" })).toMatch(/restart it/);
  });
});

describe("Quick Tests in single-battery mode", () => {
  it("shows only BAT-A-3000's progress and history, without battery filters", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    mockApi({ current: counted }, calls);
    const A = { battery_id: "BAT-A-3000", managed: 1, archived: false, source: "hardware" } as unknown as BatterySummary;
    render(<TestManager battery={A} batteries={[A]} batteriesLoading={false} />);
    await screen.findByText("3 / 3 done");
    expect(screen.queryByText("Next: Test 1/3")).toBeNull();                      // BAT-B card hidden
    expect(screen.queryByRole("radiogroup", { name: "Battery filter" })).toBeNull();
    expect((screen.getByLabelText("New test battery") as HTMLSelectElement).value).toBe("BAT-A-3000");
    await waitFor(() => expect(calls.some((c) => c.url.includes("/api/tests?battery_id=BAT-A-3000"))).toBe(true));
    expect(calls.some((c) => /\/api\/tests$/.test(c.url) && !c.init?.method)).toBe(false);   // never the all-battery list
    expect(screen.getByText("Export CSV").closest("a")!.getAttribute("href")).toContain("battery_id=BAT-A-3000");
  });
});
