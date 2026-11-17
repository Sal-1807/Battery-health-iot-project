import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { fmtTime } from "../lib/format";

const AXIS = { stroke: "var(--color-axis)", tick: { fill: "var(--color-ink-3)", fontSize: 11 }, tickLine: false };

export interface Series {
  key: string;
  label: string;
  color: string;
  digits?: number;
  /** draw markers (for sparse, per-cycle data) */
  dots?: boolean;
}

function TooltipBox({
  active,
  payload,
  label,
  series,
  unit,
  xLabel,
}: {
  active?: boolean;
  payload?: ReadonlyArray<{ dataKey?: unknown; value?: unknown }>;
  label?: unknown;
  series: Series[];
  unit: string;
  xLabel: (x: number) => string;
}) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-lg border border-line bg-panel-2/95 px-3 py-2 text-xs shadow-lg backdrop-blur">
      <div className="mb-1 text-ink-3">{xLabel(Number(label))}</div>
      {series.map((s) => {
        const p = payload.find((x) => x.dataKey === s.key);
        const v = p?.value as number | null | undefined;
        return (
          <div key={s.key} className="flex items-center gap-2">
            <span className="inline-block h-0.5 w-3 rounded" style={{ background: s.color }} />
            <span className="tabular font-semibold text-ink">
              {v === null || v === undefined ? "—" : `${v.toFixed(s.digits ?? 3)} ${unit}`}
            </span>
            <span className="text-ink-3">{s.label}</span>
          </div>
        );
      })}
    </div>
  );
}

export function Legend({ series }: { series: Series[] }) {
  if (series.length < 2) return null;
  return (
    <ul className="mb-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-2">
      {series.map((s) => (
        <li key={s.key} className="flex items-center gap-1.5">
          <span className="inline-block h-0.5 w-4 rounded" style={{ background: s.color }} />
          {s.label}
        </li>
      ))}
    </ul>
  );
}

/** Time-series line chart with one y-axis. */
export function TimeChart({
  data,
  series,
  unit,
  height = 220,
  domain,
  withDate = false,
}: {
  data: Record<string, unknown>[];
  series: Series[];
  unit: string;
  height?: number;
  domain?: [number | "auto" | ((dataMin: number) => number), number | "auto" | ((dataMax: number) => number)];
  withDate?: boolean;
}) {
  const xLabel = (x: number) => fmtTime(x, withDate);
  const ts = data.map((d) => d.ts_ms as number).filter((v) => typeof v === "number");
  const span = ts.length > 1 ? ts[ts.length - 1] - ts[0] : 0;
  const tick = (x: number) => {
    const d = new Date(x);
    if (span > 2 * 86400000) return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
    if (span > 30 * 60000) return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
    return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  };
  return (
    <div>
      <Legend series={series} />
      <div style={{ height }} role="img" aria-label={`${series.map((s) => s.label).join(", ")} over time (${unit})`}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
            <CartesianGrid stroke="var(--color-grid)" vertical={false} />
            <XAxis
              dataKey="ts_ms"
              type="number"
              scale="time"
              domain={["dataMin", "dataMax"]}
              tickFormatter={tick}
              minTickGap={56}
              {...AXIS}
            />
            <YAxis
              width={52}
              domain={domain ?? ["auto", "auto"]}
              tickFormatter={(v: number) => (Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(2))}
              {...AXIS}
              axisLine={false}
            />
            <Tooltip
              cursor={{ stroke: "var(--color-ink-3)", strokeWidth: 1 }}
              content={(p) => <TooltipBox {...p} series={series} unit={unit} xLabel={xLabel} />}
            />
            {series.map((s) => (
              <Line
                key={s.key}
                type="monotone"
                dataKey={s.key}
                stroke={s.color}
                strokeWidth={2}
                dot={false}
                activeDot={{ r: 4, stroke: "var(--color-panel)", strokeWidth: 2 }}
                isAnimationActive={false}
                connectNulls={false}
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

/** Per-cycle chart (x = cycle number) with an optional threshold line. */
export function CycleChart({
  data,
  series,
  unit,
  height = 260,
  threshold,
  yDomain,
}: {
  data: Record<string, unknown>[];
  series: Series[];
  unit: string;
  height?: number;
  threshold?: { y: number; label: string };
  yDomain?: [number | "auto", number | "auto"];
}) {
  return (
    <div>
      <Legend series={series} />
      <div style={{ height }} role="img" aria-label={`${series.map((s) => s.label).join(", ")} by cycle (${unit})`}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 8, right: 12, bottom: 4, left: 0 }}>
            <CartesianGrid stroke="var(--color-grid)" vertical={false} />
            <XAxis dataKey="cycle" type="number" domain={["dataMin", "dataMax"]} allowDecimals={false} {...AXIS} />
            <YAxis width={44} domain={yDomain ?? ["auto", "auto"]} {...AXIS} axisLine={false} />
            {threshold && (
              <ReferenceLine
                y={threshold.y}
                stroke="var(--color-critical)"
                strokeDasharray="5 4"
                strokeWidth={1.5}
                label={{ value: threshold.label, position: "insideBottomLeft", fill: "var(--color-ink-2)", fontSize: 11 }}
              />
            )}
            <Tooltip
              cursor={{ stroke: "var(--color-ink-3)", strokeWidth: 1 }}
              content={(p) => <TooltipBox {...p} series={series} unit={unit} xLabel={(x) => `Cycle ${x}`} />}
            />
            {series.map((s) => (
              <Line
                key={s.key}
                type="linear"
                dataKey={s.key}
                stroke={s.color}
                strokeWidth={2}
                dot={s.dots ? { r: 2.5, fill: s.color, stroke: "none" } : false}
                activeDot={{ r: 4.5, stroke: "var(--color-panel)", strokeWidth: 2 }}
                isAnimationActive={false}
                connectNulls
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
