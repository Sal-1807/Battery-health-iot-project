import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import MLValidation from "../pages/MLValidation";
import type { MlFold } from "../lib/api";

beforeAll(() => {
  globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} } as unknown as typeof ResizeObserver;
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  assessments = [empty("BAT-A-3000"), empty("BAT-B-3000"), empty("BAT-C-2500")];
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

const inputKinds: [string, "missing" | "different_quantity" | "out_of_regime", string | null][] = [
  ["Voltage_mean", "different_quantity", "mean_voltage_v"], ["Voltage_min", "different_quantity", "final_voltage_v"],
  ["Voltage_max", "different_quantity", "initial_voltage_v"], ["Voltage_range", "different_quantity", "voltage_drop_v"],
  ["Current_mean", "out_of_regime", "mean_current_a"], ["Current_std", "missing", null], ["Temperature_mean", "missing", null],
  ["Temperature_max", "different_quantity", "max_temperature_c"], ["Temperature_rise", "different_quantity", "temperature_rise_c"],
  ["Cycle_number", "missing", null],
];
const qt = (n: number) => ({ number: n, test_uid: `T-20261119-BAT-A-3000-00${n}`, completed_at: "2026-11-19T10:00:00Z", duration_s: 300,
  initial_voltage_v: 4.02 - n * 0.01, final_voltage_v: 3.93 - n * 0.01, voltage_drop_v: 0.09, mean_voltage_v: 3.96, mean_current_a: 0.372,
  mean_power_w: 1.47, max_temperature_c: 26.4, temperature_rise_c: 0.8, delivered_mah: 31.0 + n / 10, delivered_wh: 0.123 });
const assessA = {
  battery_id: "BAT-A-3000",
  quick_tests: { count: 3, target: 3, window_s: 300, measured: [qt(1), qt(2), qt(3)] },
  saved_model: { usable: false, reason: "The saved NASA models need statistics of one complete discharge plus the true cycle count.",
    inputs: inputKinds.map(([f, kind, key]) => ({ model_input: f, kind, quick_metric: key, quick_values: key ? [0.372, 0.371, 0.373] : [],
      nasa_training_range: f === "Current_mean" ? { min: 1.81, max: 2.0 } : { min: 1, max: 2 }, inside_nasa_range: kind === "out_of_regime" ? false : null,
      usable_by_saved_model: false, reason: `${f} reason` })) },
  short_test_model: { defensible_short_test_model: false, implemented: false,
    conclusion: "Not defensible with the available data: SoH and RUL remain unavailable for this battery.",
    checks: [{ requirement: "Operating regime overlaps the NASA training data (load current)", met: false, detail: "0.372 A vs NASA 1.81-2.00 A." },
             { requirement: "Ground-truth SoH for this battery to evaluate against (a measured full-capacity discharge)", met: false, detail: "0 measured." }] },
  soh: { status: "unavailable", value: null }, rul: { status: "unavailable", value: null }, note: "Quick Test values above are genuine measurements.",
};
const empty = (id: string) => ({ ...assessA, battery_id: id, quick_tests: { count: 0, target: 3, window_s: 300, measured: [] } });
let assessments: unknown[] = [empty("BAT-A-3000"), empty("BAT-B-3000"), empty("BAT-C-2500")];
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
      : url.includes("/api/ml/quick-test-assessment")
      ? { batteries: assessments }
      : url.endsWith("/api/batteries")
      ? { batteries: physicalRows.length ? physicalRows : physical }
      : cell
      ? { cell, fold: folds[cell], disclaimer: summary.disclaimer,
          cycles: [1, 2, 3].map((c) => ({ Cycle: c, SoH: 90 - c, SoH_pred: 89, SoH_baseline: 88, RUL: 100 - c, RUL_pred: 90, RUL_reg_pred: 90, RUL_baseline: 95, Reached_EOL: 0, EOL_pred: 0, EOL_prob: 0.1 })) }
      : summary;
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  });
}

describe("Battery Health Predictions: physical batteries and model demo", () => {
  it("labels a gate-passing estimate as Experimental ML Estimate and never invents one otherwise", async () => {
    mockApi([]);
    render(<MLValidation />);
    expect(screen.getByRole("heading", { name: "Battery Health Predictions" })).toBeTruthy();
    await screen.findByText("BAT-A-3000");
    const a = screen.getByText("BAT-A-3000").closest("tr")!;
    expect(a.textContent).toContain("91.2 %");
    expect(a.textContent).toContain("88 cycles");
    expect(a.textContent).toContain("Experimental ML Estimate");
    const b = screen.getByText("BAT-B-3000").closest("tr")!;
    expect(b.textContent).toContain("Not available");
    expect(b.textContent).toContain("outside NASA training range");
    expect(b.textContent).not.toMatch(/%/);
    expect(screen.getByText("BAT-C-2500").closest("tr")!.textContent).toMatch(/Quick Tests are not complete discharge records/);
    expect(screen.queryByText("CELL-18650-01")).toBeNull();          // archived records are not predicted
  });

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
  });
});

describe("Battery A after 3/3 Quick Tests (regression: unexplained 'unavailable')", () => {
  it("shows the 3 measured tests, why SoH/RUL stay unavailable, and no fabricated value", async () => {
    assessments = [assessA, empty("BAT-B-3000"), empty("BAT-C-2500")];
    physicalRows = physical.map((p) => p.battery_id === "BAT-A-3000"
      ? { ...p, latest_valid_prediction: null, latest_prediction: pred("unavailable", null, null, ["incomplete cycle"]) } : p);
    mockApi([]);
    render(<MLValidation />);
    const row = (await screen.findByText("3 / 3")).closest("tr")!;
    expect(row.textContent).toContain("BAT-A-3000");
    expect(row.textContent).toMatch(/3 measured 300 s Quick Tests\. They are partial discharges/);
    expect(row.textContent).toContain("Not available");
    expect(row.textContent).not.toMatch(/%|cycles\b(?! the)/);
    await screen.findByText(/Why Quick Tests give no SoH \/ RUL — BAT-A-3000/);
    expect(screen.getByText("Test 3/3")).toBeTruthy();
    expect(screen.getByText("31.3")).toBeTruthy();                                 // genuine mAh of test 3
    expect(screen.getByText(/Saved NASA models: not applicable/)).toBeTruthy();
    expect(screen.getByText(/Not defensible with the available data/)).toBeTruthy();
    expect(screen.getByText(/Operating regime overlaps the NASA training data/)).toBeTruthy();
    // the working saved-model predictions are shown separately, on NASA data, not as Battery A
    expect(screen.getByText(/Saved-model predictions — NASA dataset example/)).toBeTruthy();
    expect(screen.getByText(/not BAT-A, BAT-B or BAT-C/)).toBeTruthy();
  });

  it("lists all 10 model inputs with their incompatibility when expanded", async () => {
    assessments = [assessA, empty("BAT-B-3000"), empty("BAT-C-2500")];
    mockApi([]);
    render(<MLValidation />);
    const det = (await screen.findByText("Model inputs vs. Quick Test measurements (10 inputs)")).closest("details")!;
    det.open = true;
    fireEvent(det, new Event("toggle"));
    await screen.findByText("Cycle_number");
    expect(screen.getAllByText("not measured").length).toBe(3);
    expect(screen.getAllByText("different quantity").length).toBe(6);
    expect(screen.getAllByText("outside NASA regime").length).toBe(1);
  });
});

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
