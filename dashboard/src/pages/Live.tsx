import { useMemo, useState } from "react";
import { WifiOff } from "lucide-react";
import type { PageProps } from "../App";
import { api } from "../lib/api";
import { usePoll } from "../lib/hooks";
import { fmtAge, fmtTime } from "../lib/format";
import { TimeChart } from "../components/charts";
import { Card, ConnectionPill, EmptyState, ErrorBox, Loading, Provenance, Segmented } from "../components/ui";

const WINDOWS = [
  { value: "300", label: "5 min" },
  { value: "900", label: "15 min" },
  { value: "3600", label: "1 h" },
  { value: "21600", label: "6 h" },
  { value: "86400", label: "24 h" },
] as const;
type Win = (typeof WINDOWS)[number]["value"];

const METRICS = [
  { key: "voltage_v", label: "Voltage", unit: "V", digits: 3 },
  { key: "current_a", label: "Current", unit: "A", digits: 3 },
  // DS18B20 resolution is 0.0625 °C; force at least a 2 °C span so quantisation
  // steps are not magnified into apparent oscillation.
  { key: "temperature_c", label: "Temperature", unit: "°C", digits: 2, minSpan: 2 },
  { key: "power_w", label: "Power", unit: "W", digits: 3 },
] as const;

export function minSpanDomain(span: number): [(lo: number) => number, (hi: number) => number] {
  // Recharts calls each side separately; widen symmetrically around the data.
  let lo0 = 0;
  return [
    (lo: number) => {
      lo0 = lo;
      return Math.floor(lo - span / 2);
    },
    (hi: number) => Math.ceil(Math.max(hi + span / 2, lo0 + span)),
  ];
}

export default function Live({ battery }: PageProps) {
  const [win, setWin] = useState<Win>("900");
  const id = battery?.battery_id;
  const isReplay = battery?.source === "nasa_replay";

  const hist = usePoll(
    id && !isReplay
      ? () => api.history(id, { start: new Date(Date.now() - Number(win) * 1000).toISOString(), max_points: 600, limit: 5000 })
      : null,
    3000,
    [id, win, isReplay],
  );

  const data = useMemo(
    () =>
      (hist.data?.points ?? []).map((p) => ({
        ...p,
        ts_ms: p.ts_ms ?? Date.parse(p.ts),
      })),
    [hist.data],
  );

  if (!battery) return <EmptyState title="No battery selected" />;
  if (isReplay)
    return (
      <EmptyState title="No live telemetry for NASA replay batteries">
        NASA replay data is cycle-level only. Select a hardware or simulation battery, or open Battery health.
      </EmptyState>
    );

  const kind = battery.source === "hardware" ? "measured" : "simulated";
  const offline = battery.connection.state === "offline" || battery.connection.state === "stale";

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <Segmented label="Time window" value={win} options={[...WINDOWS]} onChange={setWin} />
        <ConnectionPill state={battery.connection.state} age={battery.connection.age_s != null ? fmtAge(battery.connection.age_s) : undefined} />
        <Provenance kind={kind} />
        <span className="text-xs text-ink-3">
          Refreshing every 3 s
          {hist.updatedAt && <> · updated {fmtTime(hist.updatedAt)}</>}
          {hist.data?.downsampled && <> · averaged into {hist.data.bucket_s?.toFixed(0)} s buckets</>}
        </span>
      </div>

      {hist.error && <ErrorBox message={`Could not load telemetry: ${hist.error.message}`} />}
      {offline && (
        <div className="flex items-center gap-2 rounded-lg border border-warning/40 bg-warning/10 px-3 py-2 text-sm">
          <WifiOff size={16} className="text-warning" />
          Device is not sending data (last reading {fmtAge(battery.connection.age_s)}). Charts show the last data received.
        </div>
      )}

      {hist.loading ? (
        <Loading />
      ) : data.length === 0 ? (
        <EmptyState title="No readings in this time window">Pick a longer window, or check that the device is online.</EmptyState>
      ) : (
        <div className={`grid grid-cols-1 gap-4 lg:grid-cols-2 ${hist.refreshing && !hist.data ? "opacity-60" : ""}`}>
          {METRICS.map((m) => (
            <Card key={m.key} title={`${m.label} (${m.unit})`} subtitle={`${data.length} points`}>
              <TimeChart
                data={data}
                unit={m.unit}
                series={[{ key: m.key, label: m.label, color: "var(--color-series-1)", digits: m.digits }]}
                withDate={Number(win) > 3600}
                domain={"minSpan" in m ? minSpanDomain(m.minSpan) : undefined}
              />
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
