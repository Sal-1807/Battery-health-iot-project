import { BatteryWarning, Cpu } from "lucide-react";
import type { PageProps } from "../App";
import type { BatterySummary, Prediction } from "../lib/api";
import { ageSeconds, fmt, fmtAge, fmtTime, FLAG_LABEL, SOURCE_LABEL, TIMESTAMP_LABEL } from "../lib/format";
import { Card, ConnectionPill, EmptyState, Loading, Pill, PredictionPill, Provenance, SourceBadge, StatTile } from "../components/ui";

function measuredKind(b: BatterySummary) {
  return b.source === "hardware" ? "measured" : "simulated";
}

function HealthTiles({ b }: { b: BatterySummary }) {
  const valid: Prediction | null = b.latest_valid_prediction;
  const latest: Prediction | null = b.latest_prediction;
  const unavailableReason =
    latest && latest.status === "unavailable" ? latest.reasons[0] : !latest ? "No complete discharge cycle has been aggregated yet." : null;

  const sohFoot = valid ? (
    <>
      Cycle {valid.cycle_number ?? "?"} · {fmtAge(ageSeconds(valid.created_at))}
      {valid.status === "experimental" && <span className="block text-warning">Not validated for this cell.</span>}
      {valid.status === "nasa_in_sample" && <span className="block">In-sample (model trained on this cell).</span>}
    </>
  ) : (
    <span>{unavailableReason}</span>
  );

  return (
    <>
      <StatTile
        label="State of health"
        value={valid?.soh_pct != null ? valid.soh_pct.toFixed(1) : "—"}
        unit={valid ? "%" : undefined}
        muted={!valid}
        tag={<PredictionPill status={valid?.status} />}
        foot={sohFoot}
      />
      <StatTile
        label="Remaining useful life"
        value={valid?.rul_cycles != null ? Math.round(valid.rul_cycles) : "—"}
        unit={valid ? "cycles" : undefined}
        muted={!valid}
        tag={<PredictionPill status={valid?.status} />}
        foot={
          valid ? (
            <>EOL probability {fmt((valid.eol_probability ?? 0) * 100, 0, "%")} · to NASA 70 % threshold</>
          ) : (
            "Needs a complete cycle inside the model's training range."
          )
        }
      />
    </>
  );
}

export default function Overview({ battery: b, batteriesLoading }: PageProps) {
  if (batteriesLoading && !b) return <Loading />;
  if (!b)
    return (
      <Card title="No batteries registered yet">
        <EmptyState icon={<Cpu size={22} />} title="Waiting for the first reading">
          A battery appears here as soon as the ESP32 (or the simulator) posts its first reading to{" "}
          <code className="font-mono">POST /api/battery/reading</code>. For a demo without hardware, run{" "}
          <code className="font-mono">python -m iot.simulator.simulate_esp32 telemetry</code> or{" "}
          <code className="font-mono">nasa-replay</code>.
        </EmptyState>
      </Card>
    );

  const r = b.latest_reading;
  const kind = measuredKind(b);
  const age = ageSeconds(r?.received_at);
  const stale = b.connection.state !== "online";
  const critical = b.active_alerts.critical ?? 0;
  const warning = b.active_alerts.warning ?? 0;

  const status = critical
    ? { tone: "critical" as const, text: `${critical} critical alert${critical > 1 ? "s" : ""} active` }
    : warning
      ? { tone: "warning" as const, text: `${warning} warning${warning > 1 ? "s" : ""} active` }
      : { tone: "good" as const, text: "No active alerts" };

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-semibold">{b.label || b.battery_id}</h1>
        <SourceBadge source={b.source} />
        <ConnectionPill state={b.connection.state} age={b.connection.age_s != null ? fmtAge(b.connection.age_s) : undefined} />
        <Pill tone={status.tone}>{status.text}</Pill>
      </div>

      {b.source === "nasa_replay" ? (
        <Card title="Telemetry" subtitle="Cycle-level NASA replay — no instantaneous sensor readings for this battery.">
          <p className="text-sm text-ink-2">
            {b.n_cycles} cycles replayed. See <a className="text-accent underline" href="#/health">Battery health</a> for SoH and RUL trends.
          </p>
        </Card>
      ) : !r ? (
        <EmptyState title="No readings yet" />
      ) : (
        <>
          <div className={`grid grid-cols-2 gap-3 lg:grid-cols-4 ${stale ? "opacity-70" : ""}`}>
            <StatTile
              label="Voltage"
              value={r.battery_detected === false ? "No cell" : fmt(r.voltage_v, 3)}
              unit={r.battery_detected === false ? undefined : "V"}
              muted={r.voltage_v == null || r.battery_detected === false}
              tag={<Provenance kind={r.voltage_v == null || r.battery_detected === false ? "unavailable" : kind} />}
              foot={
                r.battery_detected === false
                  ? `INA219 input floating (raw ${fmt(r.voltage_v, 3, "V")}) — not a battery voltage`
                  : "INA219 load voltage (VIN+ to GND)"
              }
            />
            <StatTile
              label="Current"
              value={fmt(r.current_a, 3)}
              unit="A"
              muted={r.current_a == null}
              tag={<Provenance kind={r.current_a == null ? "unavailable" : kind} />}
              foot="Positive = discharging"
            />
            <StatTile
              label="Temperature"
              value={fmt(r.temperature_c, 1)}
              unit="°C"
              muted={r.temperature_c == null}
              tag={<Provenance kind={r.temperature_c == null ? "unavailable" : kind} />}
              foot={r.temperature_c == null ? `DS18B20: ${r.ds18b20_status ?? "no value"}` : "DS18B20 cell surface"}
            />
            <StatTile
              label="Power"
              value={fmt(r.power_w, 3)}
              unit="W"
              muted={r.power_w == null}
              tag={<Provenance kind={r.power_w == null ? "unavailable" : r.power_source === "computed" ? "derived" : kind} />}
              foot={r.power_source === "computed" ? "Computed as V × |I| by the backend" : "Reported by the device (INA219 power register)"}
            />
          </div>
          <p className="text-xs text-ink-3">
            Last reading {fmtTime(r.ts, true)} ({fmtAge(age)}) · time from {TIMESTAMP_LABEL[r.timestamp_source] ?? r.timestamp_source}
            {r.transport === "usb_serial" ? " · via USB" : r.transport === "wifi" ? " · via Wi-Fi" : ""} ·
            device {r.device_id}
            {r.flags.length > 0 && <> · flags: {r.flags.map((f) => FLAG_LABEL[f] ?? f).join(", ")}</>}
          </p>
        </>
      )}

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <HealthTiles b={b} />
        <Card title="Cycles" className="lg:col-span-2">
          <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm">
            <dt className="text-ink-3">Discharge segments detected</dt>
            <dd className="tabular text-right">{b.n_cycles}</dd>
            <dt className="text-ink-3">Complete cycles</dt>
            <dd className="tabular text-right">{b.n_complete_cycles}</dd>
            <dt className="text-ink-3">Rated capacity</dt>
            <dd className="tabular text-right">{b.rated_capacity_ah != null ? `${b.rated_capacity_ah} Ah` : "not set"}</dd>
            <dt className="text-ink-3">Prior cycle count</dt>
            <dd className="tabular text-right">{b.source === "nasa_replay" ? "from dataset" : b.cycle_count_offset ?? "unknown"}</dd>
            <dt className="text-ink-3">Readings stored</dt>
            <dd className="tabular text-right">{b.n_readings.toLocaleString()}</dd>
            <dt className="text-ink-3">Data origin</dt>
            <dd className="text-right">{SOURCE_LABEL[b.source]}</dd>
          </dl>
        </Card>
      </div>

      {b.latest_prediction?.status === "unavailable" && b.latest_prediction.reasons.length > 0 && (
        <Card
          title={
            <span className="flex items-center gap-2">
              <BatteryWarning size={16} className="text-ink-3" /> Why no health estimate
            </span>
          }
          subtitle="The model is only run when its inputs are trustworthy. Latest decision:"
        >
          <ul className="list-disc space-y-1 pl-5 text-sm text-ink-2">
            {b.latest_prediction.reasons.map((x, i) => (
              <li key={i}>{x}</li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}
