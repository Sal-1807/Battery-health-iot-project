import { useState } from "react";
import { Cpu, PlugZap } from "lucide-react";
import type { PageProps } from "../App";
import { api, ApiError, VALID_CELL_STATES, type BatterySummary, type Reading } from "../lib/api";
import { usePoll } from "../lib/hooks";
import Live from "./Live";
import { ageSeconds, ALERT_LABEL, CELL_STATE_LABEL, fmt, fmtAge, fmtTime, FLAG_LABEL, TIMESTAMP_LABEL } from "../lib/format";
import { Card, ConnectionPill, EmptyState, Loading, Pill, Provenance, SeverityPill, SourceBadge, StatTile } from "../components/ui";
import { DemoEstimateTiles } from "../components/demo";

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

function ActiveAlerts({ id }: { id: string }) {
  const al = usePoll(() => api.alerts({ battery_id: id, status: "active", limit: 20 }), 5000, [id]);
  const list = al.data?.alerts ?? [];
  if (!list.length) return null;
  return (
    <Card title="Active alerts" subtitle="Monitoring signals only: they do not disconnect anything">
      <ul className="space-y-2">
        {list.map((a) => (
          <li key={a.id} className="flex flex-wrap items-start gap-2 text-sm">
            <SeverityPill severity={a.severity} />
            <span className="text-ink">{ALERT_LABEL[a.type] ?? a.type}</span>
            <span className="min-w-0 flex-1 text-ink-3">{a.message}</span>
            {!a.acknowledged && (
              <button className="text-xs text-accent hover:underline" onClick={() => api.ackAlert(a.id).then(al.reload)}>
                Acknowledge
              </button>
            )}
          </li>
        ))}
      </ul>
    </Card>
  );
}

function sensorTone(st: string | null | undefined) {
  return st === "ok" ? "good" : st ? "critical" : "neutral";
}

export default function Overview({ battery: b, batteriesLoading }: PageProps) {
  if (batteriesLoading && !b) return <Loading />;
  if (!b)
    return (
      <Card title="No batteries registered yet">
        <EmptyState icon={<Cpu size={22} />} title="Waiting for the first reading">
          Select the connected battery under Battery Analysis → Device, alerts and records, and start the ESP32 bridge. Readings appear here as soon as
          they arrive.
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

      {b.managed ? (
        <p className="text-xs text-ink-2">
          Physical profile · rated {fmt((b.rated_capacity_ah ?? 0) * 1000, 0)} mAh (label) ·{" "}
          {b.assignment ? (
            <>
              selected as connected on <span className="text-ink">{b.assignment.device_id}</span> since{" "}
              {fmtTime(b.assignment.start_ts, true)}
            </>
          ) : (
            <span className="text-warning">not selected as connected: new readings are not recorded to this battery</span>
          )}
        </p>
      ) : null}

      {b.source === "nasa_replay" ? (
        <Card title="Telemetry" subtitle="Cycle-level NASA replay — no instantaneous sensor readings for this battery.">
          <p className="text-sm text-ink-2">
            {b.n_cycles} cycles replayed. See <a className="text-accent underline" href="#/ml">Battery Health Predictions</a>.
          </p>
        </Card>
      ) : !r ? (
        <EmptyState title="No readings for this battery yet">
          Select it as the connected battery under Battery Analysis → Device, alerts and records, then start the ESP32 / USB bridge.
        </EmptyState>
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
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <Pill tone={sensorTone(r.ina219_status)}>INA219 {r.ina219_status ?? "unknown"}</Pill>
            <Pill tone={sensorTone(r.ds18b20_status)}>DS18B20 {r.ds18b20_status ?? "unknown"}</Pill>
            <Pill tone={r.cell_state && VALID_CELL_STATES.includes(r.cell_state) ? "good" : "neutral"}>
              Cell: {CELL_STATE_LABEL[r.cell_state ?? ""] ?? "unknown"}
            </Pill>
          </div>
          <p className="text-xs text-ink-3">
            Last reading {fmtTime(r.ts, true)} ({fmtAge(age)}) · time from {TIMESTAMP_LABEL[r.timestamp_source] ?? r.timestamp_source}
            {r.transport === "usb_serial" ? " · via USB" : r.transport === "wifi" ? " · via Wi-Fi" : ""} ·
            device {r.device_id}
            {r.flags.length > 0 && <> · flags: {r.flags.map((f) => FLAG_LABEL[f] ?? f).join(", ")}</>}
          </p>
        </>
      )}

      {b.managed && !b.archived ? (
        <section className="space-y-2">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h2 className="text-base font-semibold">Battery health</h2>
            <p className="text-xs text-ink-3">
              Quick Test Cycles {Math.min(b.quick_test_cycles?.count ?? 0, b.quick_test_cycles?.target ?? 3)} / {b.quick_test_cycles?.target ?? 3}
            </p>
          </div>
          <DemoEstimateTiles e={b.quick_test_cycles?.demo_estimate} />
          {b.latest_valid_prediction?.status === "experimental" && (
            <p className="text-xs text-ink-2">
              Saved ML model (separate): Experimental ML Estimate {fmt(b.latest_valid_prediction.soh_pct, 1)} % SoH,{" "}
              {fmt(b.latest_valid_prediction.rul_cycles, 0)} cycles RUL.
            </p>
          )}
        </section>
      ) : null}

      {b.source !== "nasa_replay" && r && <Live battery={b} batteries={[b]} batteriesLoading={false} />}
      <ActiveAlerts id={b.battery_id} />
    </div>
  );
}
