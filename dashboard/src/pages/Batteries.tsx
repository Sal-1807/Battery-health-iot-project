import { useEffect, useState } from "react";
import { ArrowRightLeft, Cable } from "lucide-react";
import { api, ApiError } from "../lib/api";
import { usePoll } from "../lib/hooks";
import { fmt, fmtTime, SOURCE_LABEL } from "../lib/format";
import type { PageProps } from "../App";
import Alerts from "./Alerts";
import SystemPage from "./System";
import { Card, Collapsible, ErrorBox, Table } from "../components/ui";

const DEFAULT_DEVICE = "esp32-devkit-01";

/** Select which physical battery is on a device. Selecting ≠ proving the cell is connected. */
type Profile = { battery_id: string; rated_capacity_mah: number | null };

function ConnectedBattery({ profiles, onChanged }: { profiles: Profile[]; onChanged?: () => void }) {
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
      onChanged?.();
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

function Archive({ selectBattery }: { selectBattery?: (id: string, page?: string) => void }) {
  const all = usePoll(() => api.batteries(), 30000, []);
  const archived = (all.data?.batteries ?? []).filter((b) => b.archived);
  if (!archived.length) return <p className="text-sm text-ink-3">No archived records.</p>;
  return (
    <Table head={["Record", "Origin", "Readings", "Last reading", ""]}>
      {archived.map((b) => (
        <tr key={b.battery_id}>
          <td className="px-3 py-2 text-ink">{b.battery_id}</td>
          <td className="px-3 py-2">{SOURCE_LABEL[b.source] ?? b.source}</td>
          <td className="px-3 py-2">{b.n_readings.toLocaleString()}</td>
          <td className="px-3 py-2">{b.latest_reading ? fmtTime(b.latest_reading.ts, true) : "—"}</td>
          <td className="px-3 py-2">
            {selectBattery && (
              <button className="text-accent hover:underline" onClick={() => selectBattery(b.battery_id, "analysis")}>
                View history (read-only)
              </button>
            )}
          </td>
        </tr>
      ))}
    </Table>
  );
}

/** Device assignment, alerts, system status and read-only records (collapsed by default). */
export default function DeviceAndRecords({ battery, batteries = [], hiddenProfiles = [], selectBattery }: Partial<PageProps>) {
  const profiles: Profile[] = batteries
    .filter((b) => b.managed && !b.archived)
    .map((b) => ({ battery_id: b.battery_id, rated_capacity_mah: b.rated_capacity_ah != null ? b.rated_capacity_ah * 1000 : null }));
  return (
    <div className="space-y-3">
      <Collapsible title="Connected battery (device assignment)">
        <ConnectedBattery profiles={profiles} />
      </Collapsible>
      <Collapsible title="Alerts and thresholds">
        <Alerts battery={battery} batteries={[]} batteriesLoading={false} />
      </Collapsible>
      <Collapsible title="System status (API, devices, model)">
        <SystemPage />
      </Collapsible>
      <Collapsible title="Archived and hidden records (read-only)">
        <p className="mb-3 text-xs text-ink-3">
          Kept unchanged in the database. Retired IDs such as CELL-18650-01, unassigned readings and older demo data
          are never merged into a profile.
          {hiddenProfiles.length > 0 &&
            ` Hidden profiles (single-battery dashboard): ${hiddenProfiles.map((b) => b.battery_id).join(", ")}.`}
        </p>
        <Archive selectBattery={selectBattery} />
      </Collapsible>
    </div>
  );
}
