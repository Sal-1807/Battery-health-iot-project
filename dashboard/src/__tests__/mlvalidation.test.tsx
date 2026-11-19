import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import MLValidation from "../pages/MLValidation";
import type { BatterySummary, DemoEstimate, MlFold } from "../lib/api";

beforeAll(() => {
  globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} } as unknown as typeof ResizeObserver;
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  physicalRows = [];
});

const m = (mae: number, rmse: number, n = 100) => ({ n, mae, rmse });
const fold = (cell: string, train: string[], eolCycle: number | null, modelMae: number, baseMae: number, recall: number): MlFold => ({
  cell, train_cells: train, n_cycles: 168,
  eol: eolCycle == null
    ? { status: "censored", eol_cycle: null, last_cycle: 168, cycles_after_eol: null, predicted_eol_cycle: null, note: "EOL not observed, RUL undefined." }
    : { status: "observed", eol_cycle: eolCycle, last_cycle: 168, cycles_after_eol: 168 - eolCycle, predicted_eol_cycle: recall ? eolCycle - 10 : null },
  soh: { model: m(2.34, 3.35), baseline: m(3.2, 3.61) },
  rul_classifier: { n: 168, accuracy: 0.9, precision: 0.73, recall, f1: 0.85, confusion: { tp: 44, tn: 108, fp: 16, fn: 0 } },
  rul_regressor_pre_eol: { model: m(15.5, 17.0, 124), baseline: m(14.0, 16.0, 124) },
  rul_two_stage: { model: m(modelMae, 14.9), baseline: m(baseMae, 17.8) },
  rul_by_life_stage: [{ stage: "early life (first third)", n: 41, model: m(25, 26), baseline: m(20, 21) }],
  baseline_definition: { soh: "SoH = line fit", rul: "RUL = max(103.0 - cycle, 0)" },
});
const folds: Record<string, MlFold> = {
  B0005: fold("B0005", ["B0006", "B0018"], 125, 11.9, 14.9, 1),
  B0006: fold("B0006", ["B0005", "B0018"], 109, 8.8, 1.3, 1),
  B0018: fold("B0018", ["B0005", "B0006"], null, 23.9, 16.1, 0),
};
const summary = {
  cells: ["B0005", "B0006", "B0018"], protocol: "Leave-one-cell-out: ...", eol_threshold_soh_pct: 70,
  disclaimer: "NASA demonstration only. They are NOT validated for the project's physical 2500 mAh and 3000 mAh batteries.",
  eol_definition: "First discharge cycle with capacity <= 1.4 Ah.", production_models_note: "models/*.pkl ... not used for these metrics.",
  rul_difficulty: ["RUL depends on degradation that has not happened yet."], folds,
  aggregate: { soh_mae: { mean: 3.7, std: 1.2 } },
  reproduction: { checked: true, matches_committed_lobo_predictions: true, file: "results/lobo_predictions.csv" },
};

const pred = (status: "experimental" | "unavailable", soh: number | null, rul: number | null, reasons: string[] = []) => ({
  id: 1, battery_id: "x", cycle_id: 1, cycle_number: 1, created_at: "", status, model_version: "v", data_source: "hardware",
  soh_pct: soh, eol_probability: null, eol_predicted: null, rul_cycles: rul, reasons, warnings: [], out_of_range: [], input_features: null,
});
const physical = [
  { battery_id: "BAT-A-3000", archived: false, latest_valid_prediction: pred("experimental", 91.24, 87.6), latest_prediction: pred("experimental", 91.24, 87.6) },
  { battery_id: "BAT-B-3000", archived: false, latest_valid_prediction: null, latest_prediction: pred("unavailable", null, null, ["features outside NASA training range: Current_mean"]) },
  { battery_id: "BAT-C-2500", archived: false, latest_valid_prediction: null, latest_prediction: null },
  { battery_id: "CELL-18650-01", archived: true, latest_valid_prediction: null, latest_prediction: null },
];

let physicalRows: unknown[] = [];

function mockApi(urls: string[]) {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = String(input);
    urls.push(url);
    const cell = url.match(/ml-validation\/(B\d+)/)?.[1];
    const demo = url.match(/ml-validation\/(B\d+)\/demo\?cycle=(\d+)/);
    const body = demo
      ? { cell: demo[1], cycle: Number(demo[2]), source: "NASA PCoE battery aging dataset (processed features)", features: {},
          actual: { soh_pct: 84.6, rul_cycles: 59, reached_eol: 0 },
          saved_model: { status: "nasa_in_sample", soh_pct: 84.1, rul_cycles: 61, eol_probability: 0.05, model_version: "v", note: "Saved production models were trained on B0005, B0006 and B0018: in-sample." },
          held_out: { soh_pct: 82.0, rul_cycles: 70, trained_on: ["B0005", "B0018"], note: "" }, disclaimer: summary.disclaimer }
      : url.endsWith("/api/batteries")
      ? { batteries: physicalRows.length ? physicalRows : physical }
      : cell
      ? { cell, fold: folds[cell], disclaimer: summary.disclaimer,
          cycles: [1, 2, 3].map((c) => ({ Cycle: c, SoH: 90 - c, SoH_pred: 89, SoH_baseline: 88, RUL: 100 - c, RUL_pred: 90, RUL_reg_pred: 90, RUL_baseline: 95, Reached_EOL: 0, EOL_pred: 0, EOL_prob: 0.1 })) }
      : summary;
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  });
}

const demoEst = (soh: number | null, rul: number | null, ok = true): DemoEstimate => ({
  status: ok ? "demo" : "no_estimate", label: "Experimental Demo Estimate", method: "demo-heuristic-v1",
  disclaimer: "Not a machine-learning prediction.", reason: ok ? null : "1 of 3 valid Quick Tests completed", required_tests: 3,
  tests_used: ok ? ["T-1", "T-2", "T-3"] : [], soh_pct: soh, rul_cycles: rul,
  inputs: ok ? { initial_voltage_v: 4.01, final_voltage_v: 3.92, voltage_drop_v: 0.09, mean_current_a: 0.372, mean_power_w: 1.47,
                 max_temperature_c: 26.4, temperature_rise_c: 0.8, delivered_mah: 31.0, delivered_wh: 0.123,
                 apparent_resistance_ohm: 0.242, thermal_k_per_w: 0.54 } : null,
  constants: { r_ref_ohm: 0.15, soh_points_per_r_doubling: 20, theta_ref_k_per_w: 2, soh_points_per_k_per_w: 2, thermal_cap_points: 5, eol_soh_pct: 80, rated_cycle_life: 500 },
});
const batA = (e: DemoEstimate, extra: Partial<BatterySummary> = {}) =>
  ({ battery_id: "BAT-A-3000", managed: 1, archived: false, quick_test_cycles: { count: e.status === "demo" ? 3 : 1, target: 3, last: null, demo_estimate: e },
     latest_valid_prediction: null, latest_prediction: null, ...extra }) as unknown as BatterySummary;

describe("Battery Health Predictions: Experimental Demo Estimate for BAT-A-3000", () => {
  it("shows the demo SoH and RUL prominently with the renamed columns and labels", async () => {
    mockApi([]);
    render(<MLValidation batteries={[batA(demoEst(87.7, 193))]} />);
    expect(screen.getByRole("heading", { name: "Battery Health Predictions" })).toBeTruthy();
    expect(screen.getByText("Demo SoH Estimate (%)")).toBeTruthy();
    expect(screen.getByText("Demo RUL Estimate (cycles)")).toBeTruthy();
    expect(screen.getAllByText("87.7").length).toBe(2);                // tile + table
    expect(screen.getAllByText("193").length).toBe(2);
    expect(screen.getAllByText("Experimental Demo Estimate").length).toBe(3);   // 2 tiles + table status
    expect(screen.queryByText(/Why Quick Tests give no SoH/)).toBeNull();        // diagnostic panel removed
    expect(screen.queryByText(/Model inputs vs\. Quick Test measurements/)).toBeNull();
    expect(screen.getByText(/not applied \(it needs a complete discharge cycle\)/)).toBeTruthy();   // ML kept separate
    expect(screen.getByText(/Saved-model predictions — NASA dataset example/)).toBeTruthy();
  });

  it("explains the heuristic in a concise method view and never calls it ML", async () => {
    mockApi([]);
    render(<MLValidation batteries={[batA(demoEst(87.7, 193))]} />);
    const det = screen.getByText("Method and inputs").closest("details")!;
    det.open = true;
    fireEvent(det, new Event("toggle"));
    await screen.findByText(/not a\s+machine-learning model/);
    expect(screen.getByText("Apparent resistance ΔV/I (Ω)")).toBeTruthy();
    expect(screen.getByText("0.242")).toBeTruthy();
    expect(screen.getByText(/Tests used: T-1, T-2, T-3/)).toBeTruthy();
  });

  it("shows 'No estimate yet' without valid tests, never a number", () => {
    mockApi([]);
    render(<MLValidation batteries={[batA(demoEst(null, null, false))]} />);
    expect(screen.getAllByText("No estimate yet").length).toBe(3);
    const row = screen.getByText("BAT-A-3000", { selector: "td" }).closest("tr")!;
    expect(row.textContent).toBe("BAT-A-30001 / 3——No estimate yet");
  });

  it("updates the displayed numbers when the estimate changes", () => {
    mockApi([]);
    const { rerender } = render(<MLValidation batteries={[batA(demoEst(87.7, 193))]} />);
    rerender(<MLValidation batteries={[batA(demoEst(74.7, 0))]} />);
    expect(screen.getAllByText("74.7").length).toBe(2);
    expect(screen.queryByText("87.7")).toBeNull();
  });
});

describe("saved ML model demonstration (NASA data, separate from the demo heuristic)", () => {
  it("runs the saved model on a chosen NASA dataset cycle, beside the held-out model", async () => {
    const urls: string[] = [];
    mockApi(urls);
    render(<MLValidation />);
    await screen.findByText("Measured (dataset)");
    expect(urls.some((u) => u.includes("/api/ml-validation/B0006/demo?cycle=50"))).toBe(true);
    expect(screen.getByText("84.1")).toBeTruthy();
    expect(screen.getByText(/in-sample/)).toBeTruthy();
    expect(screen.getByText(/trained on B0005 \+ B0018 only/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Demo cycle"), { target: { value: "120" } });
    await waitFor(() => expect(urls.some((u) => u.includes("/demo?cycle=120"))).toBe(true));
  });});

describe("ML validation page", () => {
  it("shows the disclaimer, protocol and observed EOL for the held-out cell", async () => {
    mockApi([]);
    render(<MLValidation />);
    expect(screen.getByText(/Not validated for our batteries/)).toBeTruthy();
    await waitFor(() => expect(screen.getByText("Cycle 125")).toBeTruthy());
    expect(screen.getByText("observed")).toBeTruthy();
    expect(screen.getByText(/2500 mAh and 3000 mAh/)).toBeTruthy();
    expect(screen.getByText(/reproduce/)).toBeTruthy();
    expect(screen.getByText(/Trained on B0006 \+ B0018 only/)).toBeTruthy();
  });

  it("reports classifier and regressor separately and admits when the baseline wins", async () => {
    const urls: string[] = [];
    mockApi(urls);
    render(<MLValidation />);
    await screen.findByText("Cycle 125");
    fireEvent.click(within(screen.getByRole("radiogroup", { name: "NASA cell" })).getByRole("radio", { name: "B0006" }));
    await waitFor(() => expect(urls.some((u) => u.endsWith("/api/ml-validation/B0006"))).toBe(true));
    await screen.findByText("Cycle 109");
    expect(screen.getByText(/RUL stage 1 — EOL classifier/)).toBeTruthy();
    expect(screen.getByText(/RUL stage 2 — regressor alone/)).toBeTruthy();
    expect(screen.getAllByText("baseline better").length).toBeGreaterThanOrEqual(1);   // two-stage 8.8 vs baseline 1.3
  });

  it("marks a censored cell and a classifier that never detects EOL", async () => {
    mockApi([]);
    render(<MLValidation />);
    await screen.findByText("Cycle 125");
    fireEvent.click(within(screen.getByRole("radiogroup", { name: "NASA cell" })).getByRole("radio", { name: "B0018" }));
    await screen.findByText("censored", { selector: "span" });
    expect(screen.getByText("never")).toBeTruthy();
  });
});
