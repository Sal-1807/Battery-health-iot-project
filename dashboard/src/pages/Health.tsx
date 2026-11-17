import { useMemo, useState } from "react";
import type { PageProps } from "../App";
import { api, type NasaCycle, type Prediction } from "../lib/api";
import { usePoll } from "../lib/hooks";
import { fmt } from "../lib/format";
import { CycleChart } from "../components/charts";
import { Card, EmptyState, ErrorBox, Loading, Notice, PredictionPill, Segmented, Table } from "../components/ui";

const S1 = "var(--color-series-1)";
const S2 = "var(--color-series-2)";

function ModelLimitations() {
  const m = usePoll(() => api.models(), 0, []);
  if (!m.data) return null;
  return (
    <Notice title="Model scope and limitations">
      <ul className="list-disc space-y-0.5 pl-4">
        {m.data.limitations.map((l, i) => (
          <li key={i}>{l}</li>
        ))}
      </ul>
    </Notice>
  );
}

function OutOfRangeTable({ p }: { p: Prediction }) {
  if (!p.out_of_range.length) return null;
  return (
    <Table head={["Feature", "This cycle", "NASA training range", "Allowed (±10 % of range)"]}>
      {p.out_of_range.map((o) => (
        <tr key={o.feature}>
          <td className="px-3 py-1.5 text-ink">{o.feature}</td>
          <td className="px-3 py-1.5 text-serious">{o.value.toFixed(3)}</td>
          <td className="px-3 py-1.5">
            {o.train_min.toFixed(3)} … {o.train_max.toFixed(3)}
          </td>
          <td className="px-3 py-1.5">
            {o.allowed_min.toFixed(3)} … {o.allowed_max.toFixed(3)}
          </td>
        </tr>
      ))}
    </Table>
  );
}

function BatteryPredictions({ id }: { id: string }) {
  const preds = usePoll(() => api.predictions(id), 10000, [id]);
  const list = preds.data?.predictions ?? [];
  const valid = list.filter((p) => p.status !== "unavailable" && p.cycle_number != null);
  const hasRef = valid.some((p) => p.ref_soh != null);
  const latest = list[list.length - 1];

  const data = useMemo(
    () =>
      valid.map((p) => ({
        cycle: p.cycle_number as number,
        soh_pred: p.soh_pct,
        soh_ref: p.ref_soh ?? null,
        rul_pred: p.rul_cycles,
        rul_ref: p.ref_rul ?? null,
      })),
    [valid],
  );

  if (preds.loading) return <Loading />;
  if (preds.error) return <ErrorBox message={preds.error.message} />;
  if (!list.length)
    return (
      <EmptyState title="No cycle predictions yet">
        A prediction is attempted each time a complete discharge segment is aggregated from telemetry (or a NASA cycle is
        replayed).
      </EmptyState>
    );

  return (
    <div className="space-y-4">
      {valid.length === 0 && latest && (
        <Card title="Prediction unavailable" actions={<PredictionPill status="unavailable" />}>
          <ul className="mb-3 list-disc space-y-1 pl-5 text-sm text-ink-2">
            {latest.reasons.map((r, i) => (
              <li key={i}>{r}</li>
            ))}
          </ul>
          <OutOfRangeTable p={latest} />
        </Card>
      )}
      {valid.length > 0 && (
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
          <Card
            title="State of health by cycle (%)"
            subtitle={hasRef ? "Model output vs measured (capacity / 2.0 Ah)" : "Model output"}
            actions={<PredictionPill status={valid[valid.length - 1].status} />}
          >
            <CycleChart
              data={data}
              unit="%"
              threshold={{ y: 70, label: "EOL 70 % (NASA 2.0 Ah scale)" }}
              series={[
                ...(hasRef ? [{ key: "soh_ref", label: "Measured SoH", color: S1, digits: 2 }] : []),
                { key: "soh_pred", label: "Model SoH", color: hasRef ? S2 : S1, digits: 2, dots: valid.length < 40 },
              ]}
            />
          </Card>
          <Card title="Remaining useful life by cycle (cycles)" actions={<PredictionPill status={valid[valid.length - 1].status} />}>
            <CycleChart
              data={data}
              unit="cycles"
              series={[
                ...(hasRef ? [{ key: "rul_ref", label: "Actual RUL", color: S1, digits: 0 }] : []),
                { key: "rul_pred", label: "Model RUL", color: hasRef ? S2 : S1, digits: 1, dots: valid.length < 40 },
              ]}
            />
          </Card>
        </div>
      )}
      {valid[valid.length - 1]?.status === "nasa_in_sample" && (
        <Notice tone="info" title="These are in-sample outputs">
          The deployed model was trained on all three NASA cells, so its agreement with the measured curve here says nothing
          about accuracy on new cells. Use the held-out comparison below for that.
        </Notice>
      )}
    </div>
  );
}

function NasaValidation() {
  const [bat, setBat] = useState<"B0005" | "B0006" | "B0018">("B0005");
  const cycles = usePoll(() => api.nasaCycles(bat), 0, [bat]);
  const models = usePoll(() => api.models(), 0, []);
  const rows: NasaCycle[] = cycles.data?.cycles ?? [];
  const data = useMemo(
    () =>
      rows.map((r) => ({
        cycle: r.Cycle,
        soh: r.SoH,
        soh_lobo: r.SoH_lobo_pred ?? null,
        rul: r.RUL,
        rul_lobo: r.RUL_lobo_pred ?? null,
      })),
    [rows],
  );
  const fold = models.data?.lobo.per_fold?.find((f) => f.test_battery === bat);

  return (
    <Card
      title="Validated NASA performance — leave-one-battery-out"
      subtitle="Each curve is predicted by a model that never saw this battery (trained on the other two)."
      actions={
        <Segmented
          label="NASA battery"
          value={bat}
          onChange={setBat}
          options={[
            { value: "B0005", label: "B0005" },
            { value: "B0006", label: "B0006" },
            { value: "B0018", label: "B0018" },
          ]}
        />
      }
    >
      {cycles.error && <ErrorBox message={cycles.error.message} />}
      {cycles.loading ? (
        <Loading />
      ) : (
        <div className="space-y-5">
          <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
            <div>
              <p className="mb-2 text-xs font-medium text-ink-2">SoH (%)</p>
              <CycleChart
                data={data}
                unit="%"
                threshold={{ y: 70, label: "EOL 70 %" }}
                series={[
                  { key: "soh", label: "Measured", color: S1, digits: 2 },
                  { key: "soh_lobo", label: "Held-out prediction", color: S2, digits: 2 },
                ]}
              />
            </div>
            <div>
              <p className="mb-2 text-xs font-medium text-ink-2">RUL (cycles)</p>
              <CycleChart
                data={data}
                unit="cycles"
                series={[
                  { key: "rul", label: "Actual", color: S1, digits: 0 },
                  { key: "rul_lobo", label: "Held-out prediction", color: S2, digits: 1 },
                ]}
              />
            </div>
          </div>
          {fold && (
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              {[
                ["SoH RMSE", fmt(fold.soh_RMSE, 2, "pp")],
                ["SoH R²", fmt(fold.soh_R2, 3)],
                ["EOL recall", fmt(fold.eol_Recall, 2)],
                ["RUL RMSE", fmt(fold.rul_RMSE, 1, "cycles")],
              ].map(([k, v]) => (
                <div key={k} className="rounded-lg border border-line bg-panel-2 px-3 py-2">
                  <p className="text-[11px] uppercase tracking-wider text-ink-3">{k}</p>
                  <p className="tabular mt-1 text-lg font-semibold">{v}</p>
                </div>
              ))}
            </div>
          )}
          {models.data?.lobo.per_fold && (
            <Table head={["Held-out", "n", "SoH RMSE", "SoH MAE", "SoH R²", "EOL acc.", "EOL recall", "EOL F1", "RUL RMSE", "RUL MAE", "RUL R²"]}>
              {models.data.lobo.per_fold.map((f) => (
                <tr key={f.test_battery} className={f.test_battery === bat ? "bg-accent/5" : ""}>
                  <td className="px-3 py-1.5 font-medium text-ink">{f.test_battery}</td>
                  <td className="px-3 py-1.5">{f.n_test}</td>
                  <td className="px-3 py-1.5">{f.soh_RMSE.toFixed(2)}</td>
                  <td className="px-3 py-1.5">{f.soh_MAE.toFixed(2)}</td>
                  <td className="px-3 py-1.5">{f.soh_R2.toFixed(3)}</td>
                  <td className="px-3 py-1.5">{f.eol_Accuracy.toFixed(3)}</td>
                  <td className="px-3 py-1.5">{f.eol_Recall.toFixed(2)}</td>
                  <td className="px-3 py-1.5">{f.eol_F1.toFixed(3)}</td>
                  <td className="px-3 py-1.5">{f.rul_RMSE.toFixed(1)}</td>
                  <td className="px-3 py-1.5">{f.rul_MAE.toFixed(1)}</td>
                  <td className="px-3 py-1.5">{f.rul_R2.toFixed(3)}</td>
                </tr>
              ))}
            </Table>
          )}
          <p className="text-xs text-ink-3">
            Source: results/lobo_cv_results.csv and results/lobo_predictions.csv. B0018 is a known domain-shift case
            (see results/analysis/b0018_comparison): its held-out EOL recall is 0.
          </p>
        </div>
      )}
    </Card>
  );
}

export default function Health({ battery }: PageProps) {
  return (
    <div className="space-y-6">
      <ModelLimitations />
      {battery ? (
        <section className="space-y-3">
          <h2 className="text-base font-semibold">
            {battery.label || battery.battery_id} <span className="text-sm font-normal text-ink-3">— predictions on aggregated cycles</span>
          </h2>
          <BatteryPredictions id={battery.battery_id} />
        </section>
      ) : (
        <EmptyState title="No battery selected" />
      )}
      <NasaValidation />
    </div>
  );
}
