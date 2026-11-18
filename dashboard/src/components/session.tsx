import { Gauge, Timer, Zap } from "lucide-react";
import type { BatterySummary, DischargeSession } from "../lib/api";
import { fmt, fmtDuration, fmtTime } from "../lib/format";
import { Card, Pill } from "./ui";

export const END_REASON: Record<string, string> = {
  load_stopped: "load stopped",
  telemetry_gap: "ended by telemetry gap",
  telemetry_lost: "device stopped reporting",
};

function Stat({ label, value, unit, foot }: { label: string; value: string; unit?: string; foot?: string }) {
  return (
    <div className="min-w-0 rounded-lg border border-line bg-panel-2 px-3 py-2.5">
      <p className="text-[11px] uppercase tracking-wider text-ink-3">{label}</p>
      <p className="tabular mt-1 flex items-baseline gap-1 text-ink">
        <span className="text-xl font-semibold">{value}</span>
        {unit && <span className="text-xs text-ink-2">{unit}</span>}
      </p>
      {foot && <p className="mt-1 text-[11px] leading-snug text-ink-3">{foot}</p>}
    </div>
  );
}

/** Live/last measured discharge session. Never presented as capacity, SoC or SoH. */
export function SessionPanel({ b }: { b: BatterySummary }) {
  const sess: DischargeSession | null = b.active_session ?? b.last_session;
  const rated = b.rated_capacity_ah != null ? `${(b.rated_capacity_ah * 1000).toFixed(0)} mAh` : "not set";

  return (
    <Card
      title={
        <span className="flex items-center gap-2">
          <Zap size={15} className="text-ink-3" /> Discharge session
        </span>
      }
      subtitle="Measured by integrating INA219 current and power over time"
      actions={
        sess ? (
          sess.status === "active" ? (
            <Pill tone="accent">In progress</Pill>
          ) : (
            <Pill tone="neutral">Last session · {END_REASON[sess.end_reason ?? ""] ?? "ended"}</Pill>
          )
        ) : undefined
      }
    >
      {!sess ? (
        <p className="text-sm text-ink-3">
          No discharge recorded. A session starts automatically when at least 20 mA of discharge current is measured on two
          consecutive readings.
        </p>
      ) : (
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Stat
              label={sess.status === "active" ? "Discharged so far" : "Discharged"}
              value={fmt(sess.charge_mah, 1)}
              unit="mAh"
              foot={sess.uncertainty_mah > 0.05 ? `up to +${fmt(sess.uncertainty_mah, 1)} mAh in load on/off intervals` : "this session only"}
            />
            <Stat
              label="Energy delivered"
              value={fmt(sess.energy_wh, 3)}
              unit="Wh"
              foot={sess.energy_missing_s > 0 ? `${fmtDuration(sess.energy_missing_s)} without power data, not counted` : undefined}
            />
            <Stat
              label="Duration"
              value={fmtDuration(sess.duration_s)}
              foot={`${fmtTime(sess.start_ts)} → ${sess.status === "active" ? "now" : fmtTime(sess.end_ts)}`}
            />
            <Stat
              label="Average current"
              value={fmt(sess.mean_current_a, 3)}
              unit="A"
              foot={sess.peak_current_a != null ? `peak ${fmt(sess.peak_current_a, 3)} A` : undefined}
            />
          </div>

          <div className="flex flex-wrap gap-x-5 gap-y-1 text-xs text-ink-3">
            <span className="flex items-center gap-1">
              <Gauge size={12} /> Voltage {fmt(sess.start_voltage_v, 3)} → {fmt(sess.end_voltage_v, 3)} V (min{" "}
              {fmt(sess.min_voltage_v, 3)} V, under load)
            </span>
            {sess.max_temperature_c != null && <span>Max temperature {fmt(sess.max_temperature_c, 1)} °C</span>}
            <span className="flex items-center gap-1">
              <Timer size={12} />
              {sess.n_gaps > 0
                ? `${sess.n_gaps} telemetry gap(s), ${fmtDuration(sess.gap_s)} not counted`
                : "no telemetry gaps"}
            </span>
          </div>

          <div className="rounded-lg border border-line px-3 py-2.5 text-xs leading-relaxed text-ink-2">
            <p>
              <span className="font-medium text-ink">Discharged charge ≠ battery capacity.</span>{" "}
              {sess.kind === "partial"
                ? "This figure is only the charge that flowed during this session. The session did not start from a rested full charge and run to the cutoff without gaps, so it says nothing about how much the full cell holds."
                : sess.interpretation}
            </p>
            <p className="mt-1">
              Label rating: <span className="text-ink">{rated}</span> (manufacturer rating, not measured) · Full capacity:{" "}
              <span className="text-ink">not measured</span> · SoH / RUL: not derived from sessions.
            </p>
          </div>
        </div>
      )}
    </Card>
  );
}
