import { useMemo, useState } from "react";
import { FlaskConical, ShieldAlert } from "lucide-react";
import { api, type MlFold, type RegMetrics } from "../lib/api";
import { usePoll } from "../lib/hooks";
import { fmt } from "../lib/format";
import { CycleChart } from "../components/charts";
import { Card, ErrorBox, Loading, Notice, Pill, Segmented, Table } from "../components/ui";

const ACTUAL = "var(--color-series-1)";
const MODEL = "var(--color-series-2)";
const BASE = "var(--color-series-3)";
type Cell = "B0005" | "B0006" | "B0018";

function Metric({ label, m, b, unit, digits = 2 }: { label: string; m: RegMetrics; b?: RegMetrics; unit: string; digits?: number }) {
  const better = b && m.mae != null && b.mae != null ? (m.mae < b.mae ? "model" : "baseline") : null;
  return (
    <div className="rounded-lg border border-line bg-panel-2 px-3 py-2.5">
      <div className="flex items-start justify-between gap-2">
        <p className="text-[11px] uppercase tracking-wider text-ink-3">{label}</p>
        {better && <Pill tone={better === "model" ? "good" : "warning"}>{better === "model" ? "beats baseline" : "baseline better"}</Pill>}
      </div>
      <p className="tabular mt-1 text-sm text-ink">
        MAE <span className="text-lg font-semibold">{fmt(m.mae, digits)}</span> · RMSE{" "}
        <span className="text-lg font-semibold">{fmt(m.rmse, digits)}</span> <span className="text-xs text-ink-2">{unit}</span>
      </p>
      {b && (
        <p className="tabular mt-0.5 text-xs text-ink-3">
          baseline MAE {fmt(b.mae, digits)} · RMSE {fmt(b.rmse, digits)} {unit}
        </p>
      )}
      <p className="mt-0.5 text-[11px] text-ink-3">n = {m.n} cycles</p>
    </div>
  );
}

function FoldPanel({ fold }: { fold: MlFold }) {
  const c = fold.rul_classifier;
  const eol = fold.eol;
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
        <div className="rounded-lg border border-line bg-panel-2 px-3 py-2.5 text-sm">
          <p className="text-[11px] uppercase tracking-wider text-ink-3">End of life (70 % SoH)</p>
          {eol.status === "observed" ? (
            <p className="mt-1">
              <span className="text-lg font-semibold text-ink">Cycle {eol.eol_cycle}</span>{" "}
              <Pill tone="info">observed</Pill>
            </p>
          ) : (
            <p className="mt-1">
              <Pill tone="warning">censored</Pill> <span className="text-xs text-ink-3">{eol.note}</span>
            </p>
          )}
          <p className="mt-1 text-xs text-ink-3">
            {fold.n_cycles} cycles recorded{eol.cycles_after_eol != null && `, ${eol.cycles_after_eol} after EOL`} · classifier first
            flags EOL at {eol.predicted_eol_cycle != null ? `cycle ${eol.predicted_eol_cycle}` : <span className="text-warning">never</span>}
          </p>
        </div>
        <Metric label="SoH regressor" m={fold.soh.model} b={fold.soh.baseline} unit="% pts" />
        <div className="rounded-lg border border-line bg-panel-2 px-3 py-2.5 text-sm">
          <p className="text-[11px] uppercase tracking-wider text-ink-3">RUL stage 1 — EOL classifier</p>
          <p className="tabular mt-1 text-ink">
            Acc {fmt(c.accuracy, 3)} · Prec {fmt(c.precision, 2)} · Recall{" "}
            <span className={c.recall === 0 ? "text-critical" : ""}>{fmt(c.recall, 2)}</span> · F1 {fmt(c.f1, 3)}
          </p>
          <p className="tabular mt-0.5 text-xs text-ink-3">
            TP {c.confusion.tp} · FN {c.confusion.fn} · FP {c.confusion.fp} · TN {c.confusion.tn}
          </p>
        </div>
      </div>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <Metric label="RUL stage 2 — regressor alone (pre-EOL cycles)" m={fold.rul_regressor_pre_eol.model}
                b={fold.rul_regressor_pre_eol.baseline} unit="cycles" digits={1} />
        <Metric label="RUL two-stage output (all cycles)" m={fold.rul_two_stage.model} b={fold.rul_two_stage.baseline}
                unit="cycles" digits={1} />
      </div>
      <p className="text-xs text-ink-3">
        Trained on {fold.train_cells.join(" + ")} only. Baselines: {fold.baseline_definition.soh}; {fold.baseline_definition.rul}.
      </p>
    </div>
  );
}

/** SoH / RUL status for the three physical batteries. Only gate-passing cycles produce a value, labelled experimental. */
function PhysicalPredictions() {
  const b = usePoll(() => api.batteries(), 15000, []);
  const rows = (b.data?.batteries ?? []).filter((x) => !x.archived);
  return (
    <Card
      title="Physical batteries"
      subtitle="The saved NASA models need a complete discharge cycle whose features lie inside the NASA training range"
    >
      {b.loading ? <Loading /> : (
        <Table head={["Battery", "Predicted SoH", "Predicted RUL", "Status", "Reason"]}>
          {rows.map((x) => {
            const v = x.latest_valid_prediction;
            const exp = v?.status === "experimental";
            const reason = exp
              ? "Model inputs inside the NASA training range; not independently validated for this cell"
              : x.latest_prediction?.reasons?.[0] ??
                "No complete discharge cycle recorded. 300 s Quick Tests are not complete discharge records, so the cycle-level models cannot be applied.";
            return (
              <tr key={x.battery_id}>
                <td className="px-3 py-2 font-medium text-ink">{x.battery_id}</td>
                <td className="px-3 py-2">{exp ? `${fmt(v!.soh_pct, 1)} %` : "—"}</td>
                <td className="px-3 py-2">{exp ? `${fmt(v!.rul_cycles, 0)} cycles` : "—"}</td>
                <td className="px-3 py-2">
                  {exp ? <Pill tone="warning">Experimental ML Estimate</Pill> : <Pill tone="neutral">Not available</Pill>}
                </td>
                <td className="max-w-lg whitespace-normal px-3 py-2 text-xs text-ink-3">{reason}</td>
              </tr>
            );
          })}
        </Table>
      )}
      <p className="mt-3 text-xs text-ink-3">
        Quick-test measurements can be exported for experimental analysis:{" "}
        <a className="text-accent underline" href={api.quickFeaturesCsvUrl()} download>
          quick_test_features.csv
        </a>
        . No validated capacity, SoH or RUL model exists for them.
      </p>
    </Card>
  );
}

/** Saved models applied to a real NASA dataset cycle (never to BAT-A/B/C). */
function ModelDemo() {
  const [cell, setCell] = useState<Cell>("B0006");
  const [cycle, setCycle] = useState(50);
  const max = cell === "B0018" ? 132 : 168;
  const c = Math.min(cycle, max);
  const d = usePoll(() => api.mlDemo(cell, c), 0, [cell, c]);
  const x = d.data;
  return (
    <Card
      title="Model demonstration — NASA dataset example"
      subtitle="Choose a recorded NASA cycle: the saved models run on its real features. These values describe the NASA cell, not our batteries."
      actions={
        <Segmented label="Demo cell" value={cell} onChange={setCell}
                   options={[{ value: "B0005", label: "B0005" }, { value: "B0006", label: "B0006" }, { value: "B0018", label: "B0018" }]} />
      }
    >
      <label className="flex items-center gap-3 text-xs text-ink-3">
        Cycle {c}
        <input aria-label="Demo cycle" type="range" min={1} max={max} value={c} onChange={(e) => setCycle(Number(e.target.value))} className="w-64" />
      </label>
      {d.error && <ErrorBox message={d.error.message} />}
      {x && (
        <div className="mt-3">
          <Table head={["", "SoH (%)", "RUL (cycles)", "Note"]}>
            <tr>
              <td className="px-3 py-2 text-ink">Measured (dataset)</td>
              <td className="px-3 py-2">{fmt(x.actual.soh_pct, 1)}</td>
              <td className="px-3 py-2">{fmt(x.actual.rul_cycles, 0)}</td>
              <td className="px-3 py-2 text-ink-3">ground truth from capacity</td>
            </tr>
            <tr>
              <td className="px-3 py-2 text-ink">Saved model</td>
              <td className="px-3 py-2">{fmt(x.saved_model.soh_pct, 1)}</td>
              <td className="px-3 py-2">{fmt(x.saved_model.rul_cycles, 0)}</td>
              <td className="px-3 py-2 text-ink-3">{x.saved_model.note}</td>
            </tr>
            <tr>
              <td className="px-3 py-2 text-ink">Held-out model</td>
              <td className="px-3 py-2">{fmt(x.held_out.soh_pct, 1)}</td>
              <td className="px-3 py-2">{fmt(x.held_out.rul_cycles, 0)}</td>
              <td className="px-3 py-2 text-ink-3">trained on {x.held_out.trained_on.join(" + ")} only — the honest accuracy reference</td>
            </tr>
          </Table>
          <p className="mt-2 text-xs text-ink-3">Source: {x.source}.</p>
        </div>
      )}
    </Card>
  );
}

export default function MLValidation() {
  const [cell, setCell] = useState<Cell>("B0005");
  const sum = usePoll(() => api.mlValidation(), 0, []);
  const det = usePoll(() => api.mlValidationCell(cell), 0, [cell]);

  const data = useMemo(
    () =>
      (det.data?.cycles ?? []).map((r) => ({
        cycle: r.Cycle,
        soh: r.SoH,
        soh_pred: r.SoH_pred,
        soh_base: r.SoH_baseline,
        rul: r.RUL,
        rul_pred: r.RUL_pred,
        rul_base: r.RUL_baseline,
      })),
    [det.data],
  );
  const fold = det.data?.fold;
  const eolX = fold?.eol.eol_cycle != null ? { x: fold.eol.eol_cycle, label: `EOL observed (cycle ${fold.eol.eol_cycle})` } : undefined;

  return (
    <div className="space-y-6">
      <h1 className="flex items-center gap-2 text-xl font-semibold">
        <FlaskConical size={18} className="text-ink-3" /> Battery Health Predictions
      </h1>

      <PhysicalPredictions />
      <ModelDemo />

      <div className="flex flex-wrap items-center gap-3 pt-2">
        <h2 className="text-base font-semibold">Model validation on the NASA dataset</h2>
        <Pill tone="info">NASA dataset · not live hardware</Pill>
      </div>

      <div role="note" className="flex gap-3 rounded-lg border border-critical/40 bg-critical/5 px-4 py-3 text-sm text-ink-2">
        <ShieldAlert size={18} className="mt-0.5 shrink-0 text-critical" />
        <p>
          <span className="font-medium text-ink">Not validated for our batteries. </span>
          {sum.data?.disclaimer ??
            "NASA-trained predictions are not validated for the project's physical 2500 mAh and 3000 mAh batteries."}
        </p>
      </div>

      {sum.error && <ErrorBox message={sum.error.message} />}
      {sum.data && (
        <Notice tone="info" title="How these results were produced (leakage-free)">
          <p>{sum.data.protocol}</p>
          <p className="mt-1">{sum.data.production_models_note}</p>
          {sum.data.reproduction.checked && (
            <p className="mt-1">
              Held-out predictions{" "}
              {sum.data.reproduction.matches_committed_lobo_predictions ? "reproduce" : "do NOT reproduce"} the committed{" "}
              {sum.data.reproduction.file}.
            </p>
          )}
          <p className="mt-1">EOL: {sum.data.eol_definition}</p>
        </Notice>
      )}

      <Card
        title={`Held-out cell ${cell}`}
        subtitle="Every value below is a prediction for a cell the models never saw during training"
        actions={
          <Segmented
            label="NASA cell"
            value={cell}
            onChange={setCell}
            options={[
              { value: "B0005", label: "B0005" },
              { value: "B0006", label: "B0006" },
              { value: "B0018", label: "B0018" },
            ]}
          />
        }
      >
        {det.error && <ErrorBox message={det.error.message} />}
        {det.loading || !fold ? (
          <Loading label="Training held-out evaluation models…" />
        ) : (
          <div className="space-y-6">
            <FoldPanel fold={fold} />
            <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
              <div>
                <p className="mb-2 text-xs font-medium text-ink-2">SoH (%) vs cycle</p>
                <CycleChart
                  data={data}
                  unit="%"
                  threshold={{ y: 70, label: "EOL threshold 70 %" }}
                  markerX={eolX}
                  series={[
                    { key: "soh", label: "Actual", color: ACTUAL, digits: 2 },
                    { key: "soh_pred", label: "Predicted (held-out)", color: MODEL, digits: 2 },
                    { key: "soh_base", label: "Baseline (line fit)", color: BASE, digits: 2 },
                  ]}
                />
              </div>
              <div>
                <p className="mb-2 text-xs font-medium text-ink-2">RUL (cycles) vs cycle</p>
                <CycleChart
                  data={data}
                  unit="cycles"
                  markerX={eolX}
                  series={[
                    { key: "rul", label: "Actual", color: ACTUAL, digits: 0 },
                    { key: "rul_pred", label: "Predicted two-stage (held-out)", color: MODEL, digits: 1 },
                    { key: "rul_base", label: "Baseline (mean EOL − cycle)", color: BASE, digits: 1 },
                  ]}
                />
              </div>
            </div>
            {fold.rul_by_life_stage.length > 0 && (
              <div>
                <p className="mb-2 text-xs font-medium text-ink-2">RUL error by life stage (pre-EOL cycles)</p>
                <Table head={["Stage", "Cycles", "Model MAE", "Baseline MAE"]}>
                  {fold.rul_by_life_stage.map((s) => (
                    <tr key={s.stage}>
                      <td className="px-3 py-1.5 text-ink">{s.stage}</td>
                      <td className="px-3 py-1.5">{s.n}</td>
                      <td className="px-3 py-1.5">{fmt(s.model.mae, 1)}</td>
                      <td className="px-3 py-1.5">{fmt(s.baseline.mae, 1)}</td>
                    </tr>
                  ))}
                </Table>
              </div>
            )}
          </div>
        )}
      </Card>

      {sum.data && (
        <Card title="All held-out cells" subtitle="Model vs baseline per cell. Mean ± std across the 3 folds at the bottom.">
          <Table head={["Held-out", "Trained on", "EOL", "SoH MAE / RMSE", "Baseline", "EOL recall", "RUL reg. MAE (pre-EOL)", "RUL two-stage MAE / RMSE", "Baseline"]}>
            {sum.data.cells.map((c) => {
              const f = sum.data!.folds[c];
              return (
                <tr key={c} className={c === cell ? "bg-accent/5" : ""}>
                  <td className="px-3 py-1.5 font-medium text-ink">{c}</td>
                  <td className="px-3 py-1.5">{f.train_cells.join(", ")}</td>
                  <td className="px-3 py-1.5">{f.eol.status === "observed" ? `cycle ${f.eol.eol_cycle}` : "censored"}</td>
                  <td className="px-3 py-1.5">{fmt(f.soh.model.mae, 2)} / {fmt(f.soh.model.rmse, 2)}</td>
                  <td className="px-3 py-1.5">{fmt(f.soh.baseline.mae, 2)} / {fmt(f.soh.baseline.rmse, 2)}</td>
                  <td className="px-3 py-1.5">{fmt(f.rul_classifier.recall, 2)}</td>
                  <td className="px-3 py-1.5">{fmt(f.rul_regressor_pre_eol.model.mae, 1)}</td>
                  <td className="px-3 py-1.5">{fmt(f.rul_two_stage.model.mae, 1)} / {fmt(f.rul_two_stage.model.rmse, 1)}</td>
                  <td className="px-3 py-1.5">{fmt(f.rul_two_stage.baseline.mae, 1)} / {fmt(f.rul_two_stage.baseline.rmse, 1)}</td>
                </tr>
              );
            })}
            {(() => {
              const a = sum.data!.aggregate;
              const ms = (k: string, d: number) => (a[k] ? `${fmt(a[k]!.mean, d)} ± ${fmt(a[k]!.std, d)}` : "—");
              return (
                <tr className="text-ink">
                  <td className="px-3 py-1.5 font-medium">Mean ± std</td>
                  <td className="px-3 py-1.5" colSpan={2} />
                  <td className="px-3 py-1.5">{ms("soh_mae", 2)} / {ms("soh_rmse", 2)}</td>
                  <td className="px-3 py-1.5">{ms("soh_baseline_mae", 2)}</td>
                  <td className="px-3 py-1.5">{ms("eol_recall", 2)}</td>
                  <td className="px-3 py-1.5">{ms("rul_reg_mae", 1)}</td>
                  <td className="px-3 py-1.5">{ms("rul_mae", 1)} / {ms("rul_rmse", 1)}</td>
                  <td className="px-3 py-1.5">{ms("rul_baseline_mae", 1)}</td>
                </tr>
              );
            })()}
          </Table>
          <p className="mt-3 text-xs text-ink-3">
            With only three cells the spread is large and a simple baseline can beat the model on a given cell. These are
            three examples, not a statistically robust benchmark.
          </p>
        </Card>
      )}

      {sum.data && (
        <Card title="Why RUL is inherently hard to predict here">
          <ul className="list-disc space-y-1 pl-5 text-sm text-ink-2">
            {sum.data.rul_difficulty.map((t, i) => (
              <li key={i}>{t}</li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}
