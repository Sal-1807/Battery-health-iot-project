import { FlaskConical } from "lucide-react";
import type { PageProps } from "../App";
import type { BatterySummary } from "../lib/api";
import { DemoEstimateTiles, ResultPill } from "../components/demo";
import { SAMPLE_MODE, SAMPLE_RESULT } from "../lib/sample";
import { fmt } from "../lib/format";
import { Collapsible, Pill, Table } from "../components/ui";

const INPUT_LABEL: [string, string, number][] = [
  ["initial_voltage_v", "Initial voltage (V)", 3], ["final_voltage_v", "Final voltage (V)", 3], ["voltage_drop_v", "Voltage drop (V)", 3],
  ["mean_current_a", "Average current (A)", 3], ["mean_power_w", "Average power (W)", 3], ["max_temperature_c", "Max temperature (°C)", 1],
  ["temperature_rise_c", "Temperature rise (°C)", 2], ["delivered_mah", "Discharged (mAh)", 1], ["delivered_wh", "Delivered (Wh)", 3],
  ["apparent_resistance_ohm", "Apparent resistance ΔV/I (Ω)", 3], ["thermal_k_per_w", "Thermal response ΔT/P (K/W)", 2],
];

/** Demo heuristic scores for the visible battery profiles. Never an ML output. */
function DemoEstimates({ batteries }: { batteries: BatterySummary[] }) {
  const rows = batteries.filter((b) => b.managed && !b.archived);
  const main = rows[0];
  if (!main) return null;
  const e = main.quick_test_cycles?.demo_estimate;
  return (
    <section className="space-y-4">
      <h2 className="text-base font-semibold">{main.battery_id}</h2>
      <DemoEstimateTiles e={e} />
      <p className="text-sm text-ink-2">
        The project uses battery-aging models trained on NASA battery data.
      </p>
      <Table head={["Battery", "Quick Tests", SAMPLE_MODE ? "SoH (%)" : "Demo SoH Estimate (%)", SAMPLE_MODE ? "RUL (cycles)" : "Demo RUL Estimate (cycles)", "Status"]}>
        {rows.map((b) => {
          const d = b.quick_test_cycles?.demo_estimate;
          const ok = SAMPLE_MODE || d?.status === "demo";
          const soh = SAMPLE_MODE ? SAMPLE_RESULT.soh_pct : d?.soh_pct;
          const rul = SAMPLE_MODE ? SAMPLE_RESULT.rul_cycles : d?.rul_cycles;
          return (
            <tr key={b.battery_id}>
              <td className="px-3 py-2 font-medium text-ink">{b.battery_id}</td>
              <td className="px-3 py-2">{Math.min(b.quick_test_cycles?.count ?? 0, b.quick_test_cycles?.target ?? 3)} / {b.quick_test_cycles?.target ?? 3}</td>
              <td className="px-3 py-2 text-base font-semibold text-ink">{ok ? fmt(soh, 1) : "—"}</td>
              <td className="px-3 py-2 text-base font-semibold text-ink">{ok ? rul : "—"}</td>
              <td className="px-3 py-2">{ok ? <ResultPill /> : <Pill tone="neutral">No estimate yet</Pill>}</td>
            </tr>
          );
        })}
      </Table>
      {!SAMPLE_MODE && e?.status === "demo" && e.inputs && (
        <Collapsible title="Method and inputs">
          <p className="text-sm text-ink-2">
            A documented arithmetic heuristic (<code className="font-mono text-xs">iot/backend/demo_estimate.py</code>), not a
            machine-learning model. Inputs are averages of the measured Quick Tests:
          </p>
          <p className="mt-2 font-mono text-xs text-ink">
            SoH = 100 − {e.constants?.soh_points_per_r_doubling} × max(0, R ÷ {e.constants?.r_ref_ohm} Ω − 1) − thermal penalty (≤ {e.constants?.thermal_cap_points}) ·
            RUL = {e.constants?.rated_cycle_life} × (SoH − {e.constants?.eol_soh_pct}) ÷ {100 - (e.constants?.eol_soh_pct ?? 80)}
          </p>
          <dl className="mt-3 grid grid-cols-1 gap-x-6 gap-y-1 text-sm sm:grid-cols-2">
            {INPUT_LABEL.map(([k, label, d]) => (
              <div key={k} className="flex justify-between gap-3 border-b border-line/50 py-1">
                <dt className="text-ink-3">{label}</dt>
                <dd className="tabular text-ink">{fmt(e.inputs![k], d)}</dd>
              </div>
            ))}
          </dl>
          <p className="mt-3 text-xs text-ink-3">
            Reference constants are illustrative assumptions, not calibrated values. Tests used: {e.tests_used.join(", ")}.
          </p>
        </Collapsible>
      )}
      <p className="text-xs text-ink-3">
        Saved ML model on {main.battery_id}:{" "}
        {main.latest_valid_prediction?.status === "experimental"
          ? `Experimental ML Estimate ${fmt(main.latest_valid_prediction.soh_pct, 1)} % SoH, ${fmt(main.latest_valid_prediction.rul_cycles, 0)} cycles RUL.`
          : "not applied (it needs a complete discharge cycle)."}
      </p>
    </section>
  );
}

export default function MLValidation({ batteries = [] }: Partial<PageProps>) {
  return (
    <div className="space-y-6">
      <h1 className="flex items-center gap-2 text-xl font-semibold">
        <FlaskConical size={18} className="text-ink-3" /> Battery Health Predictions
      </h1>
      <DemoEstimates batteries={batteries} />
    </div>
  );
}
