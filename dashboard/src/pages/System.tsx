import { api } from "../lib/api";
import { usePoll } from "../lib/hooks";
import { CELL_STATE_LABEL, fmtAge, fmtTime, SOURCE_LABEL } from "../lib/format";
import { Card, ConnectionPill, EmptyState, ErrorBox, Loading, Pill, Table } from "../components/ui";

function sensorTone(s: string | undefined) {
  return s === "ok" ? "good" : s ? "critical" : "neutral";
}

export default function SystemPage() {
  const st = usePoll(() => api.system(), 5000, []);
  if (st.loading) return <Loading />;
  if (st.error && !st.data) return <ErrorBox message={`API unreachable: ${st.error.message}`} />;
  const s = st.data!;
  const m = s.model;

  return (
    <div className="space-y-6">
      {st.error && <ErrorBox message={`Last refresh failed: ${st.error.message}`} />}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        <Card title="API">
          <dl className="space-y-2 text-sm">
            <div className="flex justify-between">
              <dt className="text-ink-3">Status</dt>
              <dd>
                <Pill tone={s.api.status === "ok" ? "good" : "warning"}>{s.api.status}</Pill>
              </dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-ink-3">Version</dt>
              <dd>{s.api.api_version}</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-ink-3">Server time (local)</dt>
              <dd className="tabular">{fmtTime(s.api.server_time, true)}</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-ink-3">Database</dt>
              <dd>
                <Pill tone={s.api.database.ok ? "good" : "critical"}>{s.api.database.ok ? "SQLite OK" : "error"}</Pill>
              </dd>
            </div>
          </dl>
        </Card>
        <Card title="Telemetry">
          <dl className="space-y-2 text-sm">
            <div className="flex justify-between gap-2">
              <dt className="text-ink-3">Last telemetry timestamp</dt>
              <dd className="tabular text-right">{s.last_telemetry_ts ? fmtTime(s.last_telemetry_ts, true) : "none"}</dd>
            </div>
            <div className="flex justify-between gap-2">
              <dt className="text-ink-3">Last received</dt>
              <dd className="text-right">{s.last_received_at ? fmtAge((Date.now() - Date.parse(s.last_received_at)) / 1000) : "never"}</dd>
            </div>
            {Object.entries(s.readings_by_source).map(([k, v]) => (
              <div key={k} className="flex justify-between">
                <dt className="text-ink-3">Readings — {SOURCE_LABEL[k] ?? k}</dt>
                <dd className="tabular">{v.toLocaleString()}</dd>
              </div>
            ))}
            <div className="flex justify-between">
              <dt className="text-ink-3">Offline after</dt>
              <dd>{s.offline_after_s} s</dd>
            </div>
          </dl>
        </Card>
        <Card title="ML models">
          <dl className="space-y-2 text-sm">
            <div className="flex justify-between">
              <dt className="text-ink-3">Status</dt>
              <dd>
                <Pill tone={m.loaded ? "good" : "critical"}>{m.loaded ? "Loaded" : "Not loaded"}</Pill>
              </dd>
            </div>
            <div className="flex justify-between gap-2">
              <dt className="text-ink-3">Version</dt>
              <dd className="font-mono text-xs">{m.model_version ?? "—"}</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-ink-3">scikit-learn</dt>
              <dd>{m.sklearn_version}</dd>
            </div>
            <div className="flex justify-between gap-2">
              <dt className="text-ink-3">Trained on</dt>
              <dd className="text-right">
                {m.training_batteries?.join(", ")} ({m.n_training_samples} cycles)
              </dd>
            </div>
          </dl>
          {m.error && <p className="mt-3 text-xs text-critical">{m.error}</p>}
          {m.load_warnings.length > 0 && <p className="mt-3 text-xs text-warning">{m.load_warnings.join(" ")}</p>}
        </Card>
      </div>

      <Card title="Devices" subtitle="ESP32 boards (and simulators) that have posted telemetry.">
        {s.devices.length === 0 ? (
          <EmptyState title="No device has connected yet" />
        ) : (
          <Table head={["Device", "Battery", "State", "Last seen", "Data origin", "INA219", "DS18B20", "Cell state (backend)", "RSSI", "Firmware"]}>
            {s.devices.map((d) => (
              <tr key={d.device_id}>
                <td className="px-3 py-2 text-ink">{d.device_id}</td>
                <td className="px-3 py-2">{d.battery_id}</td>
                <td className="px-3 py-2">
                  <ConnectionPill state={d.state} />
                </td>
                <td className="px-3 py-2">{fmtAge(d.age_s)}</td>
                <td className="px-3 py-2">{d.data_origin ? SOURCE_LABEL[d.data_origin] : "—"}</td>
                <td className="px-3 py-2">
                  <Pill tone={sensorTone(d.last_sensor_status?.ina219)}>{d.last_sensor_status?.ina219 ?? "unknown"}</Pill>
                </td>
                <td className="px-3 py-2">
                  <Pill tone={sensorTone(d.last_sensor_status?.ds18b20)}>{d.last_sensor_status?.ds18b20 ?? "unknown"}</Pill>
                </td>
                <td className="px-3 py-2">{d.cell_state ? CELL_STATE_LABEL[d.cell_state] ?? d.cell_state : "—"}</td>
                <td className="px-3 py-2">{d.last_rssi_dbm != null ? `${d.last_rssi_dbm} dBm` : "—"}</td>
                <td className="px-3 py-2">{d.firmware_version ?? "—"}</td>
              </tr>
            ))}
          </Table>
        )}
      </Card>

      <Card
        title="Model input envelope"
        subtitle={`Live cycles whose features fall outside the NASA training range (±${(m.envelope_tolerance * 100).toFixed(0)} % of range) get no prediction.`}
      >
        <Table head={["Feature (training order)", "Training min", "Training max"]}>
          {m.features.map((f) => (
            <tr key={f}>
              <td className="px-3 py-1.5 text-ink">{f}</td>
              <td className="px-3 py-1.5">{m.feature_envelope[f]?.min.toFixed(3)}</td>
              <td className="px-3 py-1.5">{m.feature_envelope[f]?.max.toFixed(3)}</td>
            </tr>
          ))}
        </Table>
      </Card>
    </div>
  );
}
