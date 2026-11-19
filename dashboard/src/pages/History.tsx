import { useMemo, useState } from "react";
import { Download } from "lucide-react";
import type { PageProps } from "../App";
import { api } from "../lib/api";
import { usePoll } from "../lib/hooks";
import { CELL_STATE_LABEL, fmt, fmtDuration, fmtTime, FLAG_LABEL } from "../lib/format";
import { TimeChart } from "../components/charts";
import { minSpanDomain } from "./Live";
import { END_REASON } from "../components/session";
import { Card, EmptyState, ErrorBox, Loading, Pill, Segmented, Table } from "../components/ui";

type Preset = "1h" | "24h" | "7d" | "custom";
const PRESET_S: Record<Exclude<Preset, "custom">, number> = { "1h": 3600, "24h": 86400, "7d": 7 * 86400 };

// datetime-local works in local time without a zone; convert to ISO UTC.
const localToIso = (v: string) => (v ? new Date(v).toISOString() : undefined);
const isoToLocal = (d: Date) => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);

const PAGE = 50;

export default function HistoryPage({ battery }: PageProps) {
  const [preset, setPreset] = useState<Preset>("24h");
  const [start, setStart] = useState(() => isoToLocal(new Date(Date.now() - 86400000)));
  const [end, setEnd] = useState(() => isoToLocal(new Date()));
  const [page, setPage] = useState(0);
  const id = battery?.battery_id;
  const isReplay = battery?.source === "nasa_replay";

  const range = useMemo(() => {
    if (preset === "custom") return { start: localToIso(start), end: localToIso(end) };
    return { start: new Date(Date.now() - PRESET_S[preset] * 1000).toISOString(), end: undefined };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [preset, start, end, id]);

  const chart = usePoll(id && !isReplay ? () => api.history(id, { ...range, max_points: 800 }) : null, 0, [id, range.start, range.end]);
  const table = usePoll(id && !isReplay ? () => api.history(id, { ...range, limit: 2000 }) : null, 0, [id, range.start, range.end]);
  const cycles = usePoll(id ? () => api.cycles(id) : null, 0, [id]);
  const sessions = usePoll(id && !isReplay ? () => api.sessions(id) : null, 10000, [id]);

  const chartData = useMemo(() => (chart.data?.points ?? []).map((p) => ({ ...p, ts_ms: p.ts_ms ?? Date.parse(p.ts) })), [chart.data]);
  const rows = useMemo(() => [...(table.data?.points ?? [])].reverse(), [table.data]);
  const pageRows = rows.slice(page * PAGE, (page + 1) * PAGE);
  const pages = Math.max(1, Math.ceil(rows.length / PAGE));

  if (!battery) return <EmptyState title="No battery selected" />;

  return (
    <div className="space-y-6">
      <h1 className="text-lg font-semibold">
        History — {battery.label || battery.battery_id}
        {battery.rated_capacity_ah != null && (
          <span className="ml-2 text-sm font-normal text-ink-3">rated {fmt(battery.rated_capacity_ah * 1000, 0)} mAh</span>
        )}
      </h1>
      {!isReplay && (
        <div className="flex flex-wrap items-end gap-3">
          <Segmented
            label="Date range"
            value={preset}
            onChange={(p) => {
              setPreset(p);
              setPage(0);
            }}
            options={[
              { value: "1h", label: "Last hour" },
              { value: "24h", label: "Last 24 h" },
              { value: "7d", label: "Last 7 days" },
              { value: "custom", label: "Custom" },
            ]}
          />
          {preset === "custom" && (
            <>
              <label className="text-xs text-ink-3">
                From
                <input
                  type="datetime-local"
                  value={start}
                  onChange={(e) => setStart(e.target.value)}
                  className="ml-2 rounded-lg border border-line bg-panel-2 px-2 py-1 text-sm text-ink"
                />
              </label>
              <label className="text-xs text-ink-3">
                To
                <input
                  type="datetime-local"
                  value={end}
                  onChange={(e) => setEnd(e.target.value)}
                  className="ml-2 rounded-lg border border-line bg-panel-2 px-2 py-1 text-sm text-ink"
                />
              </label>
            </>
          )}
          <a
            href={id ? api.historyCsvUrl(id, { ...range, limit: 50000 }) : undefined}
            className="ml-auto inline-flex items-center gap-1.5 rounded-lg border border-accent/40 bg-accent/10 px-3 py-1.5 text-xs font-medium text-accent hover:bg-accent/20"
            download
          >
            <Download size={14} /> Download CSV
          </a>
        </div>
      )}

      {!isReplay && (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          {chart.error && <ErrorBox message={chart.error.message} />}
          {chart.loading ? (
            <Loading />
          ) : chartData.length === 0 ? (
            <div className="lg:col-span-2">
              <EmptyState title="No readings in this range" />
            </div>
          ) : (
            <>
              <Card
                title="Voltage (V)"
                subtitle={`Verified cell readings only${chart.data?.downsampled ? ` · averaged into ${chart.data.bucket_s?.toFixed(0)} s buckets` : ""}`}
              >
                <TimeChart data={chartData} unit="V" withDate series={[{ key: "voltage_v", label: "Voltage", color: "var(--color-series-1)", digits: 3 }]} />
              </Card>
              <Card title="Temperature (°C)" subtitle={chart.data?.downsampled ? `Averaged into ${chart.data.bucket_s?.toFixed(0)} s buckets` : undefined}>
                <TimeChart data={chartData} unit="°C" withDate domain={minSpanDomain(2)} series={[{ key: "temperature_c", label: "Temperature", color: "var(--color-series-1)", digits: 2 }]} />
              </Card>
            </>
          )}
        </div>
      )}

      {!isReplay && (
        <Card
          title="Discharge sessions"
          subtitle="Measured charge and energy per session (newest first). Each figure covers that session only, not the battery's capacity."
        >
          {sessions.error && <ErrorBox message={sessions.error.message} />}
          {sessions.loading ? (
            <Loading />
          ) : (
            <Table
              empty={!sessions.data?.sessions.length}
              head={["#", "Start", "Duration", "Discharged (mAh)", "Energy (Wh)", "Avg I (A)", "V start → min", "Gaps", "End", "Type"]}
            >
              {(sessions.data?.sessions ?? []).map((x) => (
                <tr key={x.id}>
                  <td className="px-3 py-1.5">{x.id}</td>
                  <td className="px-3 py-1.5">{fmtTime(x.start_ts, true)}</td>
                  <td className="px-3 py-1.5">{fmtDuration(x.duration_s)}</td>
                  <td className="px-3 py-1.5 text-ink">
                    {fmt(x.charge_mah, 1)}
                    {x.uncertainty_mah > 0.05 && <span className="text-ink-3"> (+≤{fmt(x.uncertainty_mah, 1)})</span>}
                  </td>
                  <td className="px-3 py-1.5">{fmt(x.energy_wh, 3)}</td>
                  <td className="px-3 py-1.5">{fmt(x.mean_current_a, 3)}</td>
                  <td className="px-3 py-1.5">
                    {fmt(x.start_voltage_v, 3)} → {fmt(x.min_voltage_v, 3)}
                  </td>
                  <td className="px-3 py-1.5">{x.n_gaps ? `${x.n_gaps} (${fmtDuration(x.gap_s)})` : "none"}</td>
                  <td className="px-3 py-1.5">{x.status === "active" ? <Pill tone="accent">In progress</Pill> : END_REASON[x.end_reason ?? ""]}</td>
                  <td className="px-3 py-1.5" title={x.interpretation}>
                    {x.kind === "partial" ? "Partial" : "Full range (unvalidated)"}
                  </td>
                </tr>
              ))}
            </Table>
          )}
        </Card>
      )}

      <Card title="Discharge cycles" subtitle="Segments detected from telemetry (or replayed NASA cycles). Only complete cycles are used by the model.">
        {cycles.error && <ErrorBox message={cycles.error.message} />}
        {cycles.loading ? (
          <Loading />
        ) : (
          <Table
            empty={!cycles.data?.cycles.length}
            head={["#", "Cycle no.", "Start", "Duration", "Readings", "V mean", "V min", "I mean (A, NASA sign)", "T rise (°C)", "Status"]}
          >
            {(cycles.data?.cycles ?? [])
              .slice(-200)
              .reverse()
              .map((c) => (
                <tr key={c.id}>
                  <td className="px-3 py-1.5">{c.segment_index}</td>
                  <td className="px-3 py-1.5">{c.cycle_number ?? "unknown"}</td>
                  <td className="px-3 py-1.5">{c.start_ts ? fmtTime(c.start_ts, true) : "—"}</td>
                  <td className="px-3 py-1.5">{fmtDuration(c.duration_s)}</td>
                  <td className="px-3 py-1.5">{c.n_readings ?? "—"}</td>
                  <td className="px-3 py-1.5">{fmt(c.voltage_mean, 3)}</td>
                  <td className="px-3 py-1.5">{fmt(c.voltage_min, 3)}</td>
                  <td className="px-3 py-1.5">{fmt(c.current_mean, 3)}</td>
                  <td className="px-3 py-1.5">{fmt(c.temperature_rise, 2)}</td>
                  <td className="px-3 py-1.5">
                    {c.complete ? (
                      <Pill tone="good">Complete</Pill>
                    ) : (
                      <span title={c.completeness_notes.join("; ")}>
                        <Pill tone="neutral">Incomplete</Pill>
                        <span className="ml-2 text-ink-3">{c.completeness_notes[0]}</span>
                      </span>
                    )}
                  </td>
                </tr>
              ))}
          </Table>
        )}
      </Card>

      {!isReplay && (
        <Card
          title="Sensor records"
          subtitle={
            table.data
              ? `${table.data.total.toLocaleString()} readings in range${table.data.truncated ? " — table shows the latest 2,000; use CSV for all" : ""}`
              : undefined
          }
          actions={
            pages > 1 && (
              <div className="flex items-center gap-2 text-xs text-ink-3">
                <button className="rounded border border-line px-2 py-0.5 disabled:opacity-40" disabled={page === 0} onClick={() => setPage(page - 1)}>
                  Newer
                </button>
                {page + 1} / {pages}
                <button className="rounded border border-line px-2 py-0.5 disabled:opacity-40" disabled={page >= pages - 1} onClick={() => setPage(page + 1)}>
                  Older
                </button>
              </div>
            )
          }
        >
          {table.error && <ErrorBox message={table.error.message} />}
          {table.loading ? (
            <Loading />
          ) : (
            <Table empty={!pageRows.length} head={["Time (local)", "Device", "Voltage (V)", "Current (A)", "Temp (°C)", "Power (W)", "Flags"]}>
              {pageRows.map((p, i) => (
                <tr key={`${p.ts}-${i}`}>
                  <td className="px-3 py-1.5">{fmtTime(p.ts, true)}</td>
                  <td className="px-3 py-1.5">{p.device_id}</td>
                  <td className="px-3 py-1.5">
                    {p.voltage_v != null ? (
                      fmt(p.voltage_v, 3)
                    ) : p.voltage_raw_v != null ? (
                      <span className="text-ink-3" title="Raw INA219 value, not a verified cell voltage">
                        ({fmt(p.voltage_raw_v, 3)}) {CELL_STATE_LABEL[p.cell_state ?? ""] ?? ""}
                      </span>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td className="px-3 py-1.5">{fmt(p.current_a, 4)}</td>
                  <td className="px-3 py-1.5">{fmt(p.temperature_c, 2)}</td>
                  <td className="px-3 py-1.5">{fmt(p.power_w, 3)}</td>
                  <td className="px-3 py-1.5 text-ink-3">{(p.flags ?? []).map((f) => FLAG_LABEL[f] ?? f).join(", ")}</td>
                </tr>
              ))}
            </Table>
          )}
        </Card>
      )}
    </div>
  );
}
