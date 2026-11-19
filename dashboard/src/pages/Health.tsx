import { useMemo } from "react";
import type { PageProps } from "../App";
import { api, type Prediction } from "../lib/api";
import { usePoll } from "../lib/hooks";
import { CycleChart } from "../components/charts";
import { Card, EmptyState, ErrorBox, Loading, Notice, PredictionPill, Table } from "../components/ui";

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
      <Card title="NASA model validation">
        <p className="text-sm text-ink-2">
          Held-out (leave-one-cell-out) accuracy of the NASA-trained models, with baselines and EOL analysis, is on the{" "}
          <a className="text-accent underline" href="#/ml">
            ML validation
          </a>{" "}
          page.
        </p>
      </Card>
    </div>
  );
}
