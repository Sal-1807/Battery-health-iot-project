import { useState } from "react";
import { BatteryWarning, Cpu, PlugZap } from "lucide-react";
import type { PageProps } from "../App";
import { api, ApiError, VALID_CELL_STATES, type BatterySummary, type Prediction, type Reading } from "../lib/api";
import { ageSeconds, fmt, fmtAge, fmtTime, FLAG_LABEL, SOURCE_LABEL, TIMESTAMP_LABEL } from "../lib/format";
import { Card, ConnectionPill, EmptyState, Loading, Pill, PredictionPill, Provenance, SourceBadge, StatTile } from "../components/ui";

function measuredKind(b: BatterySummary) {
  return b.source === "hardware" ? "measured" : "simulated";
}

/** Voltage is shown as a cell voltage only when presence is verified. */
function VoltageTile({ r, b, kind }: { r: Reading; b: BatterySummary; kind: "measured" | "simulated" }) {
  const state = r.cell_state;
  const raw = fmt(r.voltage_v, 3, "V");
  if (r.voltage_v == null || state === "unknown")
    return <StatTile label="Voltage" value="—" muted tag={<Provenance kind="unavailable" />} foot="No voltage from the INA219" />;
  if (state === "no_cell")
    return (
      <StatTile
        label="Voltage"
        value="No cell"
        muted
        tag={<Provenance kind="unavailable" />}
        foot={`Input reads ${raw}, outside the Li-ion range, with no current: floating input (no cell) or a deeply discharged cell. Not a battery voltage.`}
      />
    );
  if (state === "unverified")
    return (
      <StatTile
        label="Voltage"
        value={fmt(r.voltage_v, 3)}
        unit="V"
        muted
        tag={<Provenance kind="unverified" />}
        foot="In the Li-ion range, but at ~0 A the INA219 cannot prove a cell is connected. Confirm below or apply a load."
      />
    );
  // verified_current | confirmed (or readings stored before validity tracking)
  return (
    <StatTile
      label="Voltage"
      value={fmt(r.voltage_v, 3)}
      unit="V"
      tag={<Provenance kind={kind} />}
      foot={
        state === "confirmed"
          ? `Cell confirmed by operator${b.presence_since ? ` at ${fmtTime(b.presence_since)}` : ""}`
          : state === "verified_current"
            ? "Cell verified: load current observed"
            : "INA219 load voltage (VIN+ to GND)"
      }
    />
  );
}

/** Explains the presence state and lets the operator confirm a resting cell. */
function CellPresencePanel({ b, r }: { b: BatterySummary; r: Reading }) {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const state = r.cell_state;
  const verified = state != null && VALID_CELL_STATES.includes(state);

  const send = async (confirmed: boolean) => {
    setBusy(true);
    setMsg(null);
    try {
      await api.setPresence(b.battery_id, confirmed, confirmed ? "confirmed from dashboard" : "withdrawn from dashboard");
      setMsg({ ok: true, text: confirmed ? "Confirmed. Applies from the next reading." : "Confirmation withdrawn." });
    } catch (e) {
      setMsg({ ok: false, text: e instanceof ApiError ? e.message : String(e) });
    } finally {
      setBusy(false);
    }
  };

  if (state !== "unverified" && !(verified && b.presence_evidence === "operator") && state !== "no_cell") return null;
  return (
    <div className="flex flex-wrap items-start gap-3 rounded-lg border border-line bg-panel px-4 py-3 text-xs leading-relaxed text-ink-2">
      <PlugZap size={16} className="mt-0.5 shrink-0 text-ink-3" />
      <div className="min-w-0 flex-1">
        {state === "no_cell" && (
          <p>
            <span className="font-medium text-ink">No cell measurement.</span> With nothing connected, the INA219 input
            floats (0.7–1.1 V was observed on this board). Voltage alerts are suppressed until a cell is verified.
          </p>
        )}
        {state === "unverified" && (
          <p>
            <span className="font-medium text-ink">Cell presence unverified.</span> A resting cell and a floating input
            both read ~0 A, so the voltage alone cannot prove a cell is connected. Voltage alerts stay off until presence
            is verified by load current or by you. Only confirm after checking the cell with a multimeter.
          </p>
        )}
        {verified && b.presence_evidence === "operator" && (
          <p>
            <span className="font-medium text-ink">Cell confirmed by an operator.</span> The confirmation lapses
            automatically if the voltage leaves the Li-ion range or readings stop for 10 min.
          </p>
        )}
        {msg && <p className={`mt-1 ${msg.ok ? "text-good" : "text-critical"}`}>{msg.text}</p>}
      </div>
      {state === "unverified" && (
        <button
          disabled={busy}
          onClick={() => send(true)}
          className="rounded-lg border border-accent/50 bg-accent/10 px-3 py-1.5 font-medium text-accent hover:bg-accent/20 disabled:opacity-50"
        >
          Confirm cell connected
        </button>
      )}
      {verified && b.presence_evidence === "operator" && (
        <button
          disabled={busy}
          onClick={() => send(false)}
          className="rounded-lg border border-line px-3 py-1.5 text-ink-2 hover:text-ink disabled:opacity-50"
        >
          Withdraw confirmation
        </button>
      )}
    </div>
  );
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
            <VoltageTile r={r} b={b} kind={kind} />
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
          {b.source === "hardware" && <CellPresencePanel b={b} r={r} />}
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
