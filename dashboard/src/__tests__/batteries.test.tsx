import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import Batteries from "../pages/Batteries";
import type { CompareRow } from "../lib/api";

beforeAll(() => {
  globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} } as unknown as typeof ResizeObserver;
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const stats = (total: number, rated: number, completed = 0) => ({
  n_sessions: completed + 1, n_partial_sessions: 1, total_discharged_mah: total, total_energy_wh: total * 0.0037,
  rated_capacity_mah: rated, equivalent_full_cycles: total / rated, completed_cycles: completed,
  capacity_measurements: completed ? [{ session_id: 9, start_ts: "2026-11-19T00:00:00Z", charge_mah: 2410, uncertainty_mah: 1, energy_wh: 8.9 }] : [],
  last_session_start: null, notes: "",
});
const row = (id: string, rated: number, total: number, completed = 0, assigned = false): CompareRow => ({
  battery_id: id, label: id, managed: true, rated_capacity_mah: rated,
  assignment: assigned ? { id: 1, device_id: "esp32-devkit-01", battery_id: id, start_ms: 0, start_ts: "2026-11-19T00:00:00Z", end_ms: null, end_ts: null, note: null } : null,
  presence_evidence: null, last_reading_ts: null, last_cell_state: null, n_readings: 0, stats: stats(total, rated, completed), soh: "unavailable",
});
const rows = [row("BAT-A-3000", 3000, 300, 0, true), row("BAT-B-3000", 3000, 0), row("BAT-C-2500", 2500, 2500, 1)];

function mockApi(calls: { url: string; init?: RequestInit }[]) {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    calls.push({ url, init });
    let body: unknown = {};
    if (url.includes("/api/batteries/compare")) body = { batteries: rows, note: "" };
    else if (url.endsWith("/api/devices")) body = { devices: [{ device_id: "esp32-devkit-01", last_seen_at: null, assignment: rows[0].assignment }] };
    else if (url.includes("/assignment")) body = { changed: true, ended: { battery_id: "BAT-A-3000", session_id: 4 } };
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  });
}

describe("Batteries page", () => {
  it("compares the three profiles with their own rated capacity and no SoH", async () => {
    mockApi([]);
    render(<Batteries selectBattery={() => {}} />);
    await waitFor(() => expect(screen.getAllByText("BAT-C-2500").length).toBeGreaterThan(0));
    expect(screen.getByText("2500 mAh")).toBeTruthy();
    expect(screen.getAllByText("3000 mAh").length).toBe(2);
    expect(screen.getByText("0.100")).toBeTruthy();         // A: 300 / 3000 EFC
    expect(screen.getByText("1.000")).toBeTruthy();         // C: 2500 / 2500 EFC
    expect(screen.getAllByText("Unavailable").length).toBe(3);
    expect(screen.getByText("on esp32-devkit-01")).toBeTruthy();
  });

  it("switching requires an explicit confirmation and sends the selected battery", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    mockApi(calls);
    render(<Batteries selectBattery={() => {}} />);
    const select = (await screen.findByLabelText("Connected battery")) as HTMLSelectElement;
    await waitFor(() => expect(select.value).toBe("BAT-A-3000"));
    fireEvent.change(select, { target: { value: "BAT-C-2500" } });
    fireEvent.click(screen.getByRole("button", { name: /Switch/ }));
    expect(calls.some((c) => c.url.includes("/assignment"))).toBe(false);          // nothing sent yet
    expect(screen.getByText(/Selecting a profile does not prove the cell is/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Confirm switch/ }));
    await waitFor(() => expect(calls.some((c) => c.url.includes("/api/devices/esp32-devkit-01/assignment"))).toBe(true));
    const put = calls.find((c) => c.url.includes("/assignment"))!;
    expect(put.init?.method).toBe("PUT");
    expect(JSON.parse(String(put.init?.body)).battery_id).toBe("BAT-C-2500");
    await waitFor(() => expect(screen.getByText(/Recording for BAT-A-3000 was ended/)).toBeTruthy());
  });

  it("opens an individual battery's history", async () => {
    mockApi([]);
    const select = vi.fn();
    render(<Batteries selectBattery={select} />);
    await waitFor(() => expect(screen.getAllByText("History").length).toBe(3));
    fireEvent.click(screen.getAllByText("History")[2]);
    expect(select).toHaveBeenCalledWith("BAT-C-2500", "history");
  });
});
