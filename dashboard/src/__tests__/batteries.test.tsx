import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import DeviceAndRecords from "../pages/Batteries";
import type { BatterySummary } from "../lib/api";

beforeAll(() => {
  globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} } as unknown as typeof ResizeObserver;
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const prof = (id: string, ah: number) => ({ battery_id: id, managed: 1, archived: false, source: "hardware", rated_capacity_ah: ah }) as unknown as BatterySummary;
const A = prof("BAT-A-3000", 3);
const hidden = [prof("BAT-B-3000", 3), prof("BAT-C-2500", 2.5)];
const assignment = { id: 1, device_id: "esp32-devkit-01", battery_id: "BAT-A-3000", start_ms: 0, start_ts: "2026-11-19T00:00:00Z", end_ms: null, end_ts: null, note: null };

function mockApi(calls: { url: string; init?: RequestInit }[], devices: unknown[] = [{ device_id: "esp32-devkit-01", last_seen_at: "2026-11-19T00:00:00Z", assignment }]) {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    calls.push({ url, init });
    let body: unknown = {};
    if (url.endsWith("/api/devices")) body = { devices };
    else if (url.includes("/assignment")) body = { changed: true, ended: null };
    else if (url.endsWith("/api/batteries"))
      body = { batteries: [A, ...hidden, { battery_id: "CELL-18650-01", source: "hardware", archived: true, n_readings: 1234, latest_reading: null }] };
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  });
}

const open = (title: string) => {
  const det = screen.getByText(title).closest("details")!;
  det.open = true;
  fireEvent(det, new Event("toggle"));
};

describe("Device, alerts and records (single-battery dashboard)", () => {
  it("is collapsed by default and offers only the focus battery for the device", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    mockApi(calls);
    render(<DeviceAndRecords battery={A} batteries={[A]} hiddenProfiles={hidden} selectBattery={() => {}} />);
    expect(screen.queryByLabelText("Connected battery")).toBeNull();
    expect(calls.length).toBe(0);                                                 // nothing polls while closed
    open("Connected battery (device assignment)");
    const sel = (await screen.findByLabelText("Connected battery")) as HTMLSelectElement;
    expect([...sel.options].map((o) => o.value)).toEqual(["", "BAT-A-3000"]);
    await waitFor(() => expect(sel.value).toBe("BAT-A-3000"));
  });

  it("switching still requires an explicit confirmation", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    mockApi(calls);
    render(<DeviceAndRecords battery={A} batteries={[A]} hiddenProfiles={hidden} />);
    open("Connected battery (device assignment)");
    const sel = (await screen.findByLabelText("Connected battery")) as HTMLSelectElement;
    await waitFor(() => expect(sel.value).toBe("BAT-A-3000"));
    fireEvent.change(sel, { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: /Switch/ }));
    expect(calls.some((c) => c.url.includes("/assignment"))).toBe(false);
    expect(screen.getByText(/Selecting a profile does not prove the cell is/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Confirm switch/ }));
    await waitFor(() => expect(calls.some((c) => c.url.includes("/api/devices/esp32-devkit-01/assignment") && c.init?.method === "PUT")).toBe(true));
  });

  it("warns when the selected device has never reported", async () => {
    mockApi([], []);
    render(<DeviceAndRecords battery={A} batteries={[A]} />);
    open("Connected battery (device assignment)");
    await screen.findByText(/has not sent any reading yet/);
  });

  it("lists archived records read-only and names the hidden profiles without offering them", async () => {
    mockApi([]);
    const select = vi.fn();
    render(<DeviceAndRecords battery={A} batteries={[A]} hiddenProfiles={hidden} selectBattery={select} />);
    open("Archived and hidden records (read-only)");
    await screen.findByText("CELL-18650-01");
    expect(screen.getByText(/Hidden profiles \(single-battery dashboard\): BAT-B-3000, BAT-C-2500/)).toBeTruthy();
    fireEvent.click(screen.getByText("View history (read-only)"));
    expect(select).toHaveBeenCalledWith("CELL-18650-01", "analysis");
  });
});
