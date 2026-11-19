import { useMemo, useState } from "react";
import { TrendingDown } from "lucide-react";
import { api, type Aging as AgingT } from "../lib/api";
import { usePoll } from "../lib/hooks";
import { fmt, fmtDuration, fmtTime } from "../lib/format";
import { CycleChart, TimeChart } from "../components/charts";
import { Card, EmptyState, ErrorBox, Loading, Notice, Pill, Segmented, StatTile, Table } from "../components/ui";

type Bat = string;
const S1 = "var(--color-series-1)";

function Insufficient({ reason }: { reason: string | null }) {
  return (
    <EmptyState icon={<TrendingDown size={22} />} title="Insufficient data">
      {reason} A comparable test is a discharge from a rested full charge to the cutoff, ended by stopping the load,
      without telemetry gaps, at a similar load current and temperature.
    </EmptyState>
  );
}

function Comparison({ rows, onPick }: { rows: AgingT[]; onPick: (b: Bat) => void }) {
  return (
    <Table
      head={["Battery", "Rated (label)", "Sessions (partial / full)", "Throughput", "of which partial", "Energy",
             "Discharge time", "EFC (throughput ÷ rating)", "Measured full capacity", "Retention", "RUL"]}
    >
      {rows.map((a) => (
        <tr key={a.battery_id}>
          <td className="px-3 py-2">
            <button className="font-medium text-accent hover:underline" onClick={() => onPick(a.battery_id as Bat)}>
              {a.battery_id}
            </button>
            {a.source === "simulation" && <span className="ml-2"><Pill tone="warning">SIMULATED</Pill></span>}
          </td>
          <td className="px-3 py-2">{fmt(a.rated_capacity_mah, 0)} mAh</td>
          <td className="px-3 py-2">
            {a.totals.n_sessions} ({a.throughput?.n_partial_sessions ?? 0} / {a.throughput?.n_full_tests ?? a.n_full_tests})
          </td>
          <td className="px-3 py-2">{fmt(a.totals.delivered_mah, 1)} mAh</td>
          <td className="px-3 py-2">{fmt(a.throughput?.partial_mah, 1)} mAh</td>
          <td className="px-3 py-2">{fmt(a.totals.delivered_wh, 3)} Wh</td>
          <td className="px-3 py-2">{fmtDuration(a.totals.discharge_duration_s)}</td>
          <td className="px-3 py-2">{fmt(a.totals.equivalent_full_cycles, 3)}</td>
          <td className="px-3 py-2">
            {a.reference ? (
              <>
                {fmt(a.reference.capacity_mah, 0)} mAh
                <span className="text-ink-3"> @ {fmt(a.reference.c_rate, 2)} C</span>
              </>
            ) : (
              <span className="text-ink-3">not measured</span>
            )}
          </td>
          <td className="px-3 py-2">
            {a.retention.status === "ok" ? `${fmt(a.retention.latest_pct, 1)} %` : <Pill tone="neutral">Insufficient data</Pill>}
          </td>
          <td className="px-3 py-2">
            <Pill tone="neutral">Unavailable</Pill>
          </td>
        </tr>
      ))}
    </Table>
  );
}

function Detail({ id }: { id: Bat }) {
  const d = usePoll(() => api.agingDetail(id), 15000, [id]);
  const a = d.data;
  const capData = useMemo(
    () => (a?.capacity_series ?? []).map((x) => ({ cycle: x.index, capacity: x.capacity_mah, retention: x.retention_pct })),
    [a],
  );
  const sessData = useMemo(
    () => (a?.sessions ?? []).filter((x) => x.counted_in_throughput !== false).map((x, i) => ({ cycle: i + 1, charge: x.charge_mah })),
    [a],
  );
  const trend = useMemo(
    () =>
      (a?.trends ?? []).map((t) => ({
        ts_ms: t.start_ms ?? Date.parse(t.start_ts),
        temp: t.mean_temperature_c,
        volt: t.mean_loaded_voltage_v,
      })),
    [a],
  );

  if (d.loading) return <Loading />;
  if (d.error) return <ErrorBox message={d.error.message} />;
  if (!a) return null;

  return (
    <div className="space-y-4">
      <h3 className="text-sm font-semibold text-ink">
        Measured discharge throughput <span className="font-normal text-ink-3">— all sessions, partial ones included. Not the battery's capacity.</span>
      </h3>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile
          label="Discharge throughput"
          value={fmt(a.totals.delivered_mah, 1)}
          unit="mAh"
          foot={`${a.throughput?.n_partial_sessions ?? 0} partial (${fmt(a.throughput?.partial_mah, 1)} mAh) + ${a.throughput?.n_full_tests ?? 0} full (${fmt(a.throughput?.full_mah, 1)} mAh)`}
        />
        <StatTile label="Energy throughput" value={fmt(a.totals.delivered_wh, 3)} unit="Wh" />
        <StatTile label="Discharge duration" value={fmtDuration(a.totals.discharge_duration_s)} foot="total time under load" />
        <StatTile
          label="Equivalent full cycles"
          value={fmt(a.totals.equivalent_full_cycles, 3)}
          foot={`delivered ÷ ${fmt(a.rated_capacity_mah, 0)} mAh label rating`}
        />
      </div>
      <Card title="Discharge-session history (mAh per session)" subtitle="Every measured session, partial and full. Partial sessions add to throughput and EFC only.">
        {sessData.length === 0 ? (
          <EmptyState title="No discharge sessions recorded" />
        ) : (
          <CycleChart data={sessData} unit="mAh" series={[{ key: "charge", label: "Session discharge", color: S1, digits: 1, dots: true }]} />
        )}
        {(a.throughput?.overlapping_sessions_excluded.length ?? 0) > 0 && (
          <p className="mt-2 text-xs text-warning">
            {a.throughput!.overlapping_sessions_excluded.length} overlapping session record(s) excluded so no charge is counted twice.
          </p>
        )}
      </Card>

      <h3 className="pt-2 text-sm font-semibold text-ink">
        Measured full capacity <span className="font-normal text-ink-3">— full tests only (rested full charge → cutoff, uninterrupted, gap-free)</span>
      </h3>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <Card
          title="Measured full capacity per comparable full test (mAh)"
          subtitle={a.reference ? `Reference: test #1, ${fmtTime(a.reference.start_ts, true)}, ${fmt(a.reference.mean_current_a, 3)} A, ${fmt(a.reference.mean_temperature_c, 1)} °C` : undefined}
        >
          {a.capacity_series.length === 0 ? (
            <Insufficient reason={a.retention.reason} />
          ) : (
            <CycleChart data={capData} unit="mAh" series={[{ key: "capacity", label: "Measured capacity", color: S1, digits: 1, dots: true }]} />
          )}
        </Card>
        <Card title="Capacity retention vs first measured test (%)" subtitle="Relative to this battery's own reference test. Not absolute SoH.">
          {a.retention.status !== "ok" ? (
            <Insufficient reason={a.retention.reason} />
          ) : (
            <CycleChart data={capData} unit="%" series={[{ key: "retention", label: "Retention", color: S1, digits: 1, dots: true }]} />
          )}
        </Card>
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <Card title="Mean temperature per session (°C)" subtitle="Cell temperature while under load">
          {trend.length === 0 ? <EmptyState title="No sessions recorded" /> : (
            <TimeChart data={trend} unit="°C" withDate series={[{ key: "temp", label: "Mean temperature", color: S1, digits: 1, dots: true }]} />
          )}
        </Card>
        <Card title="Mean loaded voltage per session (V)" subtitle="Terminal voltage while the load was on">
          {trend.length === 0 ? <EmptyState title="No sessions recorded" /> : (
            <TimeChart data={trend} unit="V" withDate series={[{ key: "volt", label: "Mean loaded voltage", color: S1, digits: 3, dots: true }]} />
          )}
        </Card>
      </div>

      <Card title="Sessions and why they are (not) used" subtitle="Only full, uninterrupted, gap-free and comparable tests enter capacity and retention">
        <Table empty={!a.sessions?.length} head={["Start", "Charge", "Duration", "Avg I", "C-rate", "Mean T", "Type", "In throughput", "Used for full capacity", "Why not"]}>
          {(a.sessions ?? []).map((x) => (
            <tr key={x.session_id}>
              <td className="px-3 py-1.5">{fmtTime(x.start_ts, true)}</td>
              <td className="px-3 py-1.5">{fmt(x.charge_mah, 1)} mAh</td>
              <td className="px-3 py-1.5">{fmtDuration(x.duration_s)}</td>
              <td className="px-3 py-1.5">{fmt(x.mean_current_a, 3)} A</td>
              <td className="px-3 py-1.5">{fmt(x.c_rate, 2)} C</td>
              <td className="px-3 py-1.5">{fmt(x.mean_temperature_c, 1)} °C</td>
              <td className="px-3 py-1.5">{x.full_test ? "full test" : "partial"}</td>
              <td className="px-3 py-1.5">
                {x.counted_in_throughput === false ? <Pill tone="warning">no (overlap)</Pill> : <Pill tone="good">yes</Pill>}
              </td>
              <td className="px-3 py-1.5">
                {x.comparable ? <Pill tone="good">{x.is_reference ? "yes (reference)" : "yes"}</Pill> : <Pill tone="neutral">no</Pill>}
              </td>
              <td className="max-w-md whitespace-normal px-3 py-1.5 text-ink-3">
                {[...x.full_test_issues, ...x.comparability_issues].join("; ") || "—"}
              </td>
            </tr>
          ))}
        </Table>
      </Card>
    </div>
  );
}

export default function Aging() {
  const ov = usePoll(() => api.aging(), 15000, []);
  const rules = ov.data?.rules;
  const ids = (ov.data?.batteries ?? []).map((b) => b.battery_id);
  const [picked, setBat] = useState<Bat | null>(null);
  const bat = picked && ids.includes(picked) ? picked : ids[0];

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold">Aging analytics — physical batteries</h1>
      <Notice tone="info" title="Measured, not predicted">
        <p>
          Every figure comes from measured discharge sessions of that battery only. Capacity retention compares a battery
          with its own first comparable full test; it is not absolute SoH, and the label rating is not treated as a
          measured initial capacity. ML RUL is unavailable for these cells until a compatible model is validated.
        </p>
        {rules && (
          <p className="mt-1">
            Comparable = {rules.full_test}; load current within ±{fmt(rules.current_tolerance_frac * 100, 0)} %, mean
            temperature within ±{fmt(rules.temperature_tolerance_c, 0)} °C and the same cutoff as the reference; at least{" "}
            {rules.min_trend_points} tests for retention.
          </p>
        )}
      </Notice>

      <Card
        title="Comparison"
        subtitle="EFC and C-rate are normalised by each battery's own rating (3000 vs 2500 mAh); retention by its own reference test"
      >
        {ov.error && <ErrorBox message={ov.error.message} />}
        {ov.loading ? <Loading /> : <Comparison rows={ov.data?.batteries ?? []} onPick={setBat} />}
      </Card>

      {bat && (
        <>
          <div className="flex items-center gap-3">
            <Segmented label="Battery" value={bat} onChange={setBat} options={ids.map((b) => ({ value: b, label: b }))} />
          </div>
          <Detail id={bat} />
        </>
      )}
    </div>
  );
}
