import { useEffect, useState } from "react";
import { BellOff, Check, FlaskConical } from "lucide-react";
import type { PageProps } from "../App";
import { api, ApiError, type Thresholds } from "../lib/api";
import { usePoll } from "../lib/hooks";
import { ALERT_LABEL, fmtTime } from "../lib/format";
import { Card, EmptyState, ErrorBox, Loading, Pill, Segmented, SeverityPill, Table } from "../components/ui";

const FIELDS: { key: keyof Thresholds; label: string; unit: string; step: number }[] = [
  { key: "temp_high_warn_c", label: "High temperature — warning", unit: "°C", step: 0.5 },
  { key: "temp_high_crit_c", label: "High temperature — critical", unit: "°C", step: 0.5 },
  { key: "temp_low_warn_c", label: "Low temperature — warning", unit: "°C", step: 0.5 },
  { key: "voltage_low_warn_v", label: "Low voltage — warning", unit: "V", step: 0.01 },
  { key: "voltage_low_crit_v", label: "Low voltage — critical", unit: "V", step: 0.01 },
  { key: "voltage_high_warn_v", label: "High voltage — warning", unit: "V", step: 0.01 },
  { key: "voltage_high_crit_v", label: "High voltage — critical", unit: "V", step: 0.01 },
  { key: "current_high_warn_a", label: "|Current| — warning", unit: "A", step: 0.01 },
  { key: "soh_low_warn_pct", label: "Predicted SoH — warning", unit: "%", step: 1 },
  { key: "soh_low_crit_pct", label: "Predicted SoH — critical", unit: "%", step: 1 },
  { key: "offline_after_s", label: "Device offline after", unit: "s", step: 1 },
];

function ThresholdEditor() {
  const t = usePoll(() => api.thresholds(), 0, []);
  const [draft, setDraft] = useState<Thresholds | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    if (t.data) setDraft(t.data);
  }, [t.data]);

  if (t.loading || !draft) return <Loading />;
  const save = async () => {
    setSaving(true);
    setMsg(null);
    try {
      const saved = await api.saveThresholds(draft);
      setDraft(saved);
      setMsg({ ok: true, text: "Saved. New thresholds apply to readings received from now on." });
    } catch (e) {
      setMsg({ ok: false, text: e instanceof ApiError ? e.message : String(e) });
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {FIELDS.map((f) => (
          <label key={f.key} className="flex items-center justify-between gap-3 rounded-lg border border-line bg-panel-2 px-3 py-2 text-sm">
            <span className="text-ink-2">{f.label}</span>
            <span className="flex items-center gap-1.5">
              <input
                type="number"
                step={f.step}
                value={draft[f.key]}
                onChange={(e) => setDraft({ ...draft, [f.key]: e.target.value === "" ? 0 : Number(e.target.value) })}
                className="tabular w-24 rounded-md border border-line bg-panel px-2 py-1 text-right text-ink focus-visible:outline-2 focus-visible:outline-accent"
              />
              <span className="w-6 text-xs text-ink-3">{f.unit}</span>
            </span>
          </label>
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <button
          onClick={save}
          disabled={saving}
          className="rounded-lg bg-accent-strong px-4 py-1.5 text-sm font-medium text-page hover:bg-accent disabled:opacity-50"
        >
          {saving ? "Saving…" : "Save thresholds"}
        </button>
        <button onClick={() => t.data && setDraft(t.data)} className="text-sm text-ink-3 hover:text-ink-2">
          Reset
        </button>
        {msg && <span className={`text-sm ${msg.ok ? "text-good" : "text-critical"}`}>{msg.text}</span>}
      </div>
      <p className="text-xs text-ink-3">
        Alerts are monitoring signals. They do not disconnect a load or stop charging — use a protected cell / BMS / cutoff
        circuit for that.
      </p>
    </div>
  );
}

export default function Alerts({ battery }: PageProps) {
  const [scope, setScope] = useState<"battery" | "all">("battery");
  const [status, setStatus] = useState<"active" | "resolved" | "any">("active");
  const id = scope === "battery" ? battery?.battery_id : undefined;
  const alerts = usePoll(() => api.alerts({ battery_id: id, status: status === "any" ? undefined : status, limit: 500 }), 5000, [id, status]);

  const ack = async (alertId: number) => {
    await api.ackAlert(alertId);
    alerts.reload();
  };
  const list = alerts.data?.alerts ?? [];

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-3">
        <Segmented
          label="Scope"
          value={scope}
          onChange={setScope}
          options={[
            { value: "battery", label: battery ? `This battery` : "This battery" },
            { value: "all", label: "All batteries" },
          ]}
        />
        <Segmented
          label="Status"
          value={status}
          onChange={setStatus}
          options={[
            { value: "active", label: "Active" },
            { value: "resolved", label: "Resolved" },
            { value: "any", label: "All" },
          ]}
        />
      </div>

      <Card title="Alerts" subtitle="One entry per condition; repeated violations increase the count until the condition clears.">
        {alerts.error && <ErrorBox message={alerts.error.message} />}
        {alerts.loading ? (
          <Loading />
        ) : list.length === 0 ? (
          <EmptyState icon={<BellOff size={22} />} title={status === "active" ? "No active alerts" : "No alerts"} />
        ) : (
          <Table head={["Severity", "Type", "Battery", "Message", "Value", "First seen", "Last seen", "Count", "Status", ""]}>
            {list.map((a) => (
              <tr key={a.id} className={a.acknowledged ? "opacity-60" : ""}>
                <td className="px-3 py-2">
                  <SeverityPill severity={a.severity} />
                </td>
                <td className="whitespace-nowrap px-3 py-2 text-ink">
                  {ALERT_LABEL[a.type] ?? a.type}
                  {a.experimental ? (
                    <span className="ml-1.5 inline-flex align-middle" title="Based on an experimental/in-sample model output">
                      <FlaskConical size={12} className="text-warning" />
                    </span>
                  ) : null}
                </td>
                <td className="px-3 py-2">{a.battery_id}</td>
                <td className="min-w-[14rem] max-w-sm whitespace-normal px-3 py-2">{a.message}</td>
                <td className="whitespace-nowrap px-3 py-2">
                  {a.value != null ? `${a.value.toFixed(a.unit === "V" ? 3 : 1)} ${a.unit ?? ""}` : "—"}
                </td>
                <td className="whitespace-nowrap px-3 py-2">{fmtTime(a.first_seen, true)}</td>
                <td className="whitespace-nowrap px-3 py-2">{fmtTime(a.last_seen, true)}</td>
                <td className="px-3 py-2">{a.occurrences}</td>
                <td className="px-3 py-2">
                  {a.status === "active" ? <Pill tone="serious">Active</Pill> : <Pill tone="neutral">Resolved</Pill>}
                </td>
                <td className="px-3 py-2">
                  {!a.acknowledged ? (
                    <button
                      onClick={() => ack(a.id)}
                      aria-label={`Acknowledge alert ${a.id}`}
                      title="Acknowledge"
                      className="inline-flex items-center gap-1 rounded border border-line px-2 py-0.5 text-xs text-ink-2 hover:text-ink"
                    >
                      <Check size={12} /> Ack
                    </button>
                  ) : (
                    <span className="text-xs text-ink-3">Acknowledged</span>
                  )}
                </td>
              </tr>
            ))}
          </Table>
        )}
      </Card>

      <Card title="Alert thresholds" subtitle="Applied by the backend to every incoming reading and prediction.">
        <ThresholdEditor />
      </Card>
    </div>
  );
}
