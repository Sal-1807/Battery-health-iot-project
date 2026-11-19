import { useEffect, useState } from "react";
import { ArrowRightLeft, Cable } from "lucide-react";
import { api, ApiError, type CompareRow } from "../lib/api";
import { usePoll } from "../lib/hooks";
import { CELL_STATE_LABEL, fmt, fmtTime } from "../lib/format";
import { Card, EmptyState, ErrorBox, Loading, Pill, Table } from "../components/ui";

const DEFAULT_DEVICE = "esp32-devkit-01";

/** Select which physical battery is on a device. Selecting ≠ proving the cell is connected. */
function ConnectedBattery({ profiles, onChanged }: { profiles: CompareRow[]; onChanged: () => void }) {
  const devices = usePoll(() => api.devices(), 5000, []);
  const list = devices.data?.devices ?? [];
  const [device, setDevice] = useState<string>("");
  const [target, setTarget] = useState<string>("");
  const [arming, setArming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const deviceId = device || list[0]?.device_id || DEFAULT_DEVICE;
  const dev = list.find((d) => d.device_id === deviceId);
  const current = dev?.assignment ?? null;
  useEffect(() => setTarget(current?.battery_id ?? ""), [current?.battery_id, deviceId]);

  const doSwitch = async () => {
    setBusy(true);
    setMsg(null);
    try {
      const r = await api.setAssignment(deviceId, target || null, "selected from dashboard");
      setMsg({
        ok: true,
        text: !r.changed
          ? "No change."
          : `Now recording ${deviceId} readings to ${target || "no battery (unassigned)"}.` +
            (r.ended ? ` Recording for ${r.ended.battery_id} was ended.` : ""),
      });
      devices.reload();
      onChanged();
    } catch (e) {
      setMsg({ ok: false, text: e instanceof ApiError ? e.message : String(e) });
    } finally {
      setBusy(false);
      setArming(false);
    }
  };

  return (
    <Card
      title={
        <span className="flex items-center gap-2">
          <Cable size={15} className="text-ink-3" /> Connected battery
        </span>
      }
      subtitle="Tell the system which physical cell is on the INA219 right now. Readings are stored under this battery from the moment you switch."
    >
      {devices.error && <ErrorBox message={devices.error.message} />}
      <div className="flex flex-wrap items-end gap-3">
        <label className="text-xs text-ink-3">
          Device
          <select
            aria-label="Device"
            value={deviceId}
            onChange={(e) => setDevice(e.target.value)}
            className="ml-2 rounded-lg border border-line bg-panel-2 px-2.5 py-1.5 text-sm text-ink"
          >
            {(list.length ? list.map((d) => d.device_id) : [DEFAULT_DEVICE]).map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
          </select>
        </label>
        <label className="text-xs text-ink-3">
          Physically connected battery
          <select
            aria-label="Connected battery"
            value={target}
            onChange={(e) => {
              setTarget(e.target.value);
              setArming(false);
            }}
            className="ml-2 rounded-lg border border-line bg-panel-2 px-2.5 py-1.5 text-sm text-ink"
          >
            <option value="">None (nothing connected)</option>
            {profiles.map((p) => (
              <option key={p.battery_id} value={p.battery_id}>
                {p.battery_id} — {fmt(p.rated_capacity_mah, 0)} mAh
              </option>
            ))}
          </select>
        </label>
        {!arming ? (
          <button
            disabled={busy || target === (current?.battery_id ?? "")}
            onClick={() => setArming(true)}
            className="inline-flex items-center gap-1.5 rounded-lg border border-accent/50 bg-accent/10 px-3 py-1.5 text-sm font-medium text-accent hover:bg-accent/20 disabled:opacity-40"
          >
            <ArrowRightLeft size={14} /> Switch
          </button>
        ) : (
          <button
            disabled={busy}
            onClick={doSwitch}
            className="rounded-lg bg-accent-strong px-3 py-1.5 text-sm font-medium text-page hover:bg-accent disabled:opacity-50"
          >
            Confirm switch
          </button>
        )}
      </div>
      <p className="mt-3 text-xs text-ink-2">
        Currently selected on {deviceId}:{" "}
        <span className="text-ink">{current ? `${current.battery_id} (since ${fmtTime(current.start_ts, true)})` : "none"}</span>
      </p>
      {!dev?.last_seen_at && (
        <p className="mt-2 rounded-lg border border-warning/40 bg-warning/5 px-3 py-2 text-xs text-ink-2">
          Device <span className="text-ink">{deviceId}</span> has not sent any reading yet. Check that DEVICE_ID in the
          firmware's config.h matches, or readings will not be recorded to the battery you select here.
        </p>
      )}
      {arming && (
        <p className="mt-2 rounded-lg border border-warning/40 bg-warning/5 px-3 py-2 text-xs leading-relaxed text-ink-2">
          Switching ends the current recording session{current ? ` for ${current.battery_id}` : ""} and starts recording to{" "}
          {target || "no battery"}. Swap the cell physically first. Selecting a profile does not prove the cell is
          connected: presence still has to be verified by load current or confirmed on the Overview page.
        </p>
      )}
      {msg && <p className={`mt-2 text-sm ${msg.ok ? "text-good" : "text-critical"}`}>{msg.text}</p>}
    </Card>
  );
}

export default function Batteries({ selectBattery }: { selectBattery: (id: string, page?: string) => void }) {
  const cmp = usePoll(() => api.compare(), 10000, []);
  const rows = cmp.data?.batteries ?? [];

  return (
    <div className="space-y-6">
      {cmp.error && <ErrorBox message={cmp.error.message} />}
      {cmp.loading ? <Loading /> : <ConnectedBattery profiles={rows} onChanged={cmp.reload} />}

      <Card
        title="Battery comparison"
        subtitle="Measured per battery. EFC = total discharged charge ÷ that battery's rated capacity (throughput, not a cycle count). Completed cycles = rested full charge to cutoff without gaps."
      >
        {cmp.loading ? (
          <Loading />
        ) : rows.length === 0 ? (
          <EmptyState title="No battery profiles" />
        ) : (
          <Table
            head={["Battery", "Rated", "Connected", "Presence", "Sessions", "Throughput (mAh)", "Energy (Wh)", "EFC",
                   "Completed cycles", "Latest capacity meas.", "SoH / RUL", ""]}
          >
            {rows.map((r) => {
              const last = r.stats.capacity_measurements[r.stats.capacity_measurements.length - 1];
              return (
                <tr key={r.battery_id}>
                  <td className="px-3 py-2 font-medium text-ink">
                    {r.battery_id}
                    {r.source === "simulation" && <span className="ml-2"><Pill tone="warning">SIMULATED</Pill></span>}
                  </td>
                  <td className="px-3 py-2">{fmt(r.rated_capacity_mah, 0)} mAh</td>
                  <td className="px-3 py-2">
                    {r.assignment ? <Pill tone="accent">on {r.assignment.device_id}</Pill> : <span className="text-ink-3">not selected</span>}
                  </td>
                  <td className="px-3 py-2">
                    {r.presence_evidence
                      ? r.presence_evidence === "operator" ? "confirmed" : "verified (current)"
                      : r.last_cell_state ? CELL_STATE_LABEL[r.last_cell_state] : "—"}
                  </td>
                  <td className="px-3 py-2">
                    {r.stats.n_sessions}
                    {r.stats.n_partial_sessions > 0 && <span className="text-ink-3"> ({r.stats.n_partial_sessions} partial)</span>}
                  </td>
                  <td className="px-3 py-2">{fmt(r.stats.total_discharged_mah, 1)}</td>
                  <td className="px-3 py-2">{fmt(r.stats.total_energy_wh, 3)}</td>
                  <td className="px-3 py-2">{fmt(r.stats.equivalent_full_cycles, 3)}</td>
                  <td className="px-3 py-2">{r.stats.completed_cycles}</td>
                  <td className="px-3 py-2" title="Charge of the latest completed experimental cycle (unvalidated protocol)">
                    {last ? `${fmt(last.charge_mah, 0)} mAh` : "not measured"}
                  </td>
                  <td className="px-3 py-2">
                    <Pill tone="neutral">Unavailable</Pill>
                  </td>
                  <td className="whitespace-nowrap px-3 py-2">
                    <button className="text-accent hover:underline" onClick={() => selectBattery(r.battery_id, "history")}>
                      History
                    </button>
                    <span className="text-ink-3"> · </span>
                    <button className="text-accent hover:underline" onClick={() => selectBattery(r.battery_id, "overview")}>
                      Overview
                    </button>
                  </td>
                </tr>
              );
            })}
          </Table>
        )}
        <p className="mt-3 text-xs text-ink-3">
          Measured capacities come from an unvalidated test protocol and are not compared to the label as a health figure.
          SoH/RUL stay unavailable for physical batteries until predictions are validated; the NASA demonstration is
          under NASA replay.
        </p>
      </Card>
    </div>
  );
}
