import { useMemo } from "react";
import type { PageProps } from "../App";
import { api, type QuickMetric, type QuickOverview } from "../lib/api";
import { usePoll } from "../lib/hooks";
import { END_REASON, fmt, fmtDuration, fmtTime } from "../lib/format";
import { CycleChart } from "../components/charts";
import { Card, Collapsible, EmptyState, ErrorBox, Loading, Notice, Pill, StatTile, Table } from "../components/ui";
import HistoryPage from "./History";

const S1 = "var(--color-series-1)";

const METRICS: { key: QuickMetric; label: string; unit: string; digits: number }[] = [
  { key: "mean_voltage_v", label: "Avg voltage", unit: "V", digits: 3 },
  { key: "mean_current_a", label: "Avg current", unit: "A", digits: 3 },
  { key: "mean_power_w", label: "Avg power", unit: "W", digits: 3 },
  { key: "max_temperature_c", label: "Max temperature", unit: "°C", digits: 1 },
  { key: "voltage_drop_v", label: "Voltage drop", unit: "V", digits: 3 },
  { key: "delivered_mah", label: "Discharged", unit: "mAh", digits: 1 },
  { key: "delivered_wh", label: "Delivered", unit: "Wh", digits: 3 },
];

/** A/B/C side by side: averages over each battery's 3 valid Quick Test Cycles. */
export function QuickComparison({ data }: { data: QuickOverview }) {
  const rows = data.comparison.rows;
  return (
    <Card title="Quick Test comparison" subtitle={data.comparison.note}>
      <Table head={["Battery", "Quick Test Cycles", ...METRICS.map((m) => `${m.label} (${m.unit})`)]}>
        {rows.map((r) => (
          <tr key={r.battery_id}>
            <td className="px-3 py-2 font-medium text-ink">{r.battery_id}</td>
            <td className="px-3 py-2">
              <Pill tone={r.count >= r.target ? "good" : "neutral"}>{`${Math.min(r.count, r.target)} / ${r.target}`}</Pill>
            </td>
            {METRICS.map((m) => {
              const v = r[m.key] as number | null;
              const diff = r[`${m.key}_diff_pct`] as number | null;
              const range = r[`${m.key}_range`] as number | null;
              return (
                <td key={m.key} className="px-3 py-2">
                  {v == null ? (
                    <span className="text-ink-3">—</span>
                  ) : (
                    <>
                      <span className="text-ink">{fmt(v, m.digits)}</span>
                      {r.count > 1 && range != null && <span className="text-ink-3"> ±{fmt(range / 2, m.digits)}</span>}
                      {diff != null && rows.filter((x) => x.count > 0).length > 1 && (
                        <span className="block text-[11px] text-ink-3">{diff >= 0 ? "+" : ""}{fmt(diff, 1)} % vs avg</span>
                      )}
                    </>
                  )}
                </td>
              );
            })}
          </tr>
        ))}
      </Table>
    </Card>
  );
}

function QuickTrends({ id, data }: { id: string; data: QuickOverview }) {
  const q = data.batteries.find((b) => b.battery_id === id);
  const series = useMemo(
    () => (q?.cycles ?? []).map((c) => ({ cycle: c.number, drop: c.voltage_drop_v, temp: c.max_temperature_c, mah: c.delivered_mah })),
    [q],
  );
  if (!q) return null;
  if (!q.cycles.length)
    return (
      <EmptyState title={`No valid Quick Test Cycles for ${id} yet`}>
        Run the 300-second Quick Tests on the Quick Tests page. Interrupted, incomplete or gap-affected tests are listed
        there but are not counted.
      </EmptyState>
    );
  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
      {[
        { key: "drop", title: "Voltage drop per test (V)", unit: "V", digits: 3 },
        { key: "temp", title: "Max temperature per test (°C)", unit: "°C", digits: 1 },
        { key: "mah", title: "Discharged per test (mAh)", unit: "mAh", digits: 1 },
      ].map((c) => (
        <Card key={c.key} title={c.title}>
          <CycleChart data={series} unit={c.unit} height={200} series={[{ key: c.key, label: c.title, color: S1, digits: c.digits, dots: true }]} />
        </Card>
      ))}
    </div>
  );
}

function Throughput({ id }: { id: string }) {
  const d = usePoll(() => api.agingDetail(id), 15000, [id]);
  const a = d.data;
  if (d.loading) return <Loading />;
  if (d.error) return <ErrorBox message={d.error.message} />;
  if (!a) return null;
  return (
    <div className="space-y-4">
      <h3 className="text-sm font-semibold text-ink">
        Measured discharge throughput <span className="font-normal text-ink-3">— all measured sessions, including Quick Tests. Not the battery's capacity.</span>
      </h3>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile label="Discharge throughput" value={fmt(a.totals.delivered_mah, 1)} unit="mAh" foot={`${a.totals.n_sessions} measured sessions`} />
        <StatTile label="Energy throughput" value={fmt(a.totals.delivered_wh, 3)} unit="Wh" />
        <StatTile label="Time under load" value={fmtDuration(a.totals.discharge_duration_s)} />
        <StatTile label="Equivalent full cycles" value={fmt(a.totals.equivalent_full_cycles, 3)} foot={`throughput ÷ ${fmt(a.rated_capacity_mah, 0)} mAh rating`} />
      </div>
      <h3 className="pt-2 text-sm font-semibold text-ink">
        Measured full capacity <span className="font-normal text-ink-3">— only from complete discharges (rested full charge → cutoff)</span>
      </h3>
      {a.retention.status !== "ok" || a.capacity_series.length === 0 ? (
        <p className="rounded-lg border border-line bg-panel px-4 py-3 text-sm text-ink-2">
          <span className="font-medium text-ink">Insufficient data.</span> {a.retention.reason} Quick Tests are 5-minute
          measurements and are never used as the battery's total capacity.
        </p>
      ) : (
        <Card title="Measured full capacity per comparable full test (mAh)" subtitle="Retention is relative to this battery's own first test, not absolute SoH">
          <CycleChart
            data={a.capacity_series.map((x) => ({ cycle: x.index, capacity: x.capacity_mah }))}
            unit="mAh"
            series={[{ key: "capacity", label: "Measured capacity", color: S1, digits: 1, dots: true }]}
          />
          <p className="mt-2 text-xs text-ink-3">Latest retention: {fmt(a.retention.latest_pct, 1)} % of the first measured full test.</p>
        </Card>
      )}
      <Collapsible title={`All discharge sessions (${a.sessions?.length ?? 0})`}>
          <Table empty={!a.sessions?.length} head={["Start", "Charge", "Duration", "Avg I", "Mean T", "Type", "Ended", "Why not a full test"]}>
            {(a.sessions ?? []).map((x) => (
              <tr key={x.session_id}>
                <td className="px-3 py-1.5">{fmtTime(x.start_ts, true)}</td>
                <td className="px-3 py-1.5">{fmt(x.charge_mah, 1)} mAh</td>
                <td className="px-3 py-1.5">{fmtDuration(x.duration_s)}</td>
                <td className="px-3 py-1.5">{fmt(x.mean_current_a, 3)} A</td>
                <td className="px-3 py-1.5">{fmt(x.mean_temperature_c, 1)} °C</td>
                <td className="px-3 py-1.5">{x.full_test ? "full test" : "partial"}</td>
                <td className="px-3 py-1.5">{x.status === "active" ? "in progress" : END_REASON[x.end_reason ?? ""] ?? x.end_reason}</td>
                <td className="max-w-md whitespace-normal px-3 py-1.5 text-ink-3">{[...x.full_test_issues, ...x.comparability_issues].join("; ") || "—"}</td>
              </tr>
            ))}
          </Table>
      </Collapsible>
    </div>
  );
}

export default function Aging({ battery, batteries = [], selectBattery }: Partial<PageProps>) {
  const qt = usePoll(() => api.quickTests(), 10000, []);
  const id = battery?.battery_id;
  const isProfile = !!battery?.managed && !battery?.archived;

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold">Battery Analysis</h1>
      {qt.error && <ErrorBox message={qt.error.message} />}
      {qt.loading ? <Loading /> : qt.data && <QuickComparison data={qt.data} />}

      {battery && (
        <section className="space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-base font-semibold">{id}</h2>
            {isProfile ? <Pill tone="accent">Battery profile</Pill> : <Pill tone="warning">Archived (read-only)</Pill>}
            {selectBattery &&
              batteries
                .filter((b) => b.battery_id !== id)
                .map((b) => (
                  <button key={b.battery_id} className="text-xs text-accent hover:underline" onClick={() => selectBattery(b.battery_id)}>
                    {b.battery_id}
                  </button>
                ))}
          </div>
          {isProfile && qt.data && <QuickTrends id={id!} data={qt.data} />}
          {isProfile && <Throughput id={id!} />}
          {!isProfile && (
            <Notice tone="info" title="Archived record">
              Historical measurements are shown as recorded. They are not part of the three-battery Quick Test protocol.
            </Notice>
          )}
          <Collapsible key={id} title="Measurement history and CSV export" defaultOpen={!isProfile}>
            <HistoryPage battery={battery} batteries={batteries} batteriesLoading={false} />
          </Collapsible>
        </section>
      )}
    </div>
  );
}
