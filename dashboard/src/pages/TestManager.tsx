import { useEffect, useState } from "react";
import { CheckCircle2, ClipboardCheck, Download, Power, Timer as TimerIcon, Unplug, XCircle } from "lucide-react";
import type { PageProps } from "../App";
import { api, ApiError, type BatteryTest, type QuickStatus, type TestChecklist, type TestState } from "../lib/api";
import { usePoll } from "../lib/hooks";
import { fmt, fmtDuration, fmtTime } from "../lib/format";
import { Card, EmptyState, ErrorBox, Loading, Pill, Segmented, StatTile, Table } from "../components/ui";

const STATE_LABEL: Record<TestState, string> = {
  not_started: "Not Started", ready: "Ready", running: "Running", paused: "Paused", completed: "Completed", aborted: "Aborted",
};
const STATE_TONE: Record<TestState, "neutral" | "info" | "accent" | "warning" | "good" | "critical"> = {
  not_started: "neutral", ready: "info", running: "accent", paused: "warning", completed: "good", aborted: "critical",
};
const ITEMS: [keyof TestChecklist, string][] = [
  ["battery_identity_confirmed", "Battery identity confirmed (label on the cell matches the selected profile)"],
  ["load_rating_verified", "Load resistor and its power rating verified"],
  ["supervisor_approval", "Supervisor has approved this test"],
  ["disconnect_procedure_confirmed", "Disconnect procedure agreed: who removes the load, and how, when the test stops"],
  ["voltage_cutoff_confirmed", "Voltage cutoff agreed and will be watched (the software does not cut off)"],
];

export function HardwareWarning() {
  return (
    <div role="alert" className="flex gap-3 rounded-lg border-2 border-critical/60 bg-critical/10 px-4 py-3 text-sm text-ink">
      <Power size={20} className="mt-0.5 shrink-0 text-critical" />
      <div>
        <p className="font-semibold">Quick Tests record measurements. They do not control hardware.</p>
        <p className="mt-0.5 text-ink-2">
          Starting, stopping or the 300-second timer ending changes only the software record. Nothing connects, disconnects
          or switches the battery or the load resistor, and there is no automatic voltage cutoff or battery protection.{" "}
          <span className="font-semibold text-ink">When a test ends, physically disconnect the discharge load.</span>
        </p>
      </div>
    </div>
  );
}

function ChecklistForm({ t, onSaved }: { t: BatteryTest; onSaved: () => void }) {
  const [c, setC] = useState<Partial<TestChecklist>>(t.checklist ?? {});
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  useEffect(() => setC(t.checklist ?? {}), [t.test_uid]);
  const need = c.load_resistance_ohm ? (4.2 * 4.2) / c.load_resistance_ohm : null;
  const num = (v: string) => (v === "" ? null : Number(v));

  const save = async () => {
    try {
      const r = await api.saveChecklist(t.test_uid, c);
      setMsg({ ok: r.checklist_problems.length === 0, text: r.checklist_problems.length ? r.checklist_problems.join("; ") : "Checklist complete — test is Ready." });
      onSaved();
    } catch (e) {
      setMsg({ ok: false, text: e instanceof ApiError ? e.message : String(e) });
    }
  };

  return (
    <div className="space-y-3">
      {ITEMS.map(([k, label]) => (
        <label key={k} className="flex items-start gap-2 text-sm text-ink-2">
          <input type="checkbox" className="mt-1" checked={!!c[k]} onChange={(e) => setC({ ...c, [k]: e.target.checked })} />
          {label}
        </label>
      ))}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <label className="text-xs text-ink-3">
          Supervisor name
          <input aria-label="Supervisor name" value={c.supervisor_name ?? ""} onChange={(e) => setC({ ...c, supervisor_name: e.target.value })}
                 className="mt-1 block w-full rounded-md border border-line bg-panel px-2 py-1 text-sm text-ink" />
        </label>
        <label className="text-xs text-ink-3">
          Voltage cutoff (V, 2.5–3.3)
          <input aria-label="Cutoff voltage" type="number" step={0.05} value={c.cutoff_v ?? ""} onChange={(e) => setC({ ...c, cutoff_v: num(e.target.value) })}
                 className="mt-1 block w-full rounded-md border border-line bg-panel px-2 py-1 text-sm text-ink" />
        </label>
        <label className="text-xs text-ink-3">
          Load resistance (Ω)
          <input aria-label="Load resistance" type="number" step={0.1} value={c.load_resistance_ohm ?? ""} onChange={(e) => setC({ ...c, load_resistance_ohm: num(e.target.value) })}
                 className="mt-1 block w-full rounded-md border border-line bg-panel px-2 py-1 text-sm text-ink" />
        </label>
        <label className="text-xs text-ink-3">
          Load power rating (W){need != null && <span className="text-ink-2"> — dissipates up to {fmt(need, 2)} W at 4.2 V</span>}
          <input aria-label="Load power rating" type="number" step={0.5} value={c.load_power_rating_w ?? ""} onChange={(e) => setC({ ...c, load_power_rating_w: num(e.target.value) })}
                 className="mt-1 block w-full rounded-md border border-line bg-panel px-2 py-1 text-sm text-ink" />
        </label>
      </div>
      <button onClick={save} className="inline-flex items-center gap-1.5 rounded-lg border border-accent/50 bg-accent/10 px-3 py-1.5 text-sm font-medium text-accent hover:bg-accent/20">
        <ClipboardCheck size={14} /> Save checklist
      </button>
      {msg && <p className={`text-sm ${msg.ok ? "text-good" : "text-warning"}`}>{msg.text}</p>}
    </div>
  );
}

const QS_TONE: Record<QuickStatus, "neutral" | "info" | "accent" | "warning" | "good" | "critical"> = {
  not_started: "neutral", in_progress: "accent", counted: "good", incomplete: "warning", interrupted: "critical",
  telemetry_gap: "warning", invalid: "warning",
};

function Countdown({ t, fetchedAt }: { t: BatteryTest; fetchedAt: number }) {
  const q = t.quick_test!;
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (t.state !== "running") return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [t.state]);
  const running = t.state === "running";
  const elapsed = Math.min((q.elapsed_s ?? 0) + (running ? (now - fetchedAt) / 1000 : 0), q.duration_s);
  const remaining = Math.max(q.duration_s - elapsed, 0);
  const mmss = (x: number) => `${Math.floor(x / 60)}:${String(Math.floor(x % 60)).padStart(2, "0")}`;
  return (
    <div className="rounded-lg border border-line bg-panel-2 px-4 py-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="flex items-center gap-2 text-[11px] uppercase tracking-wider text-ink-3">
          <TimerIcon size={13} /> Software recording window
        </p>
        <p className="tabular text-sm text-ink-2">
          elapsed <span className="font-semibold text-ink">{mmss(elapsed)}</span> · remaining{" "}
          <span className="text-2xl font-semibold text-ink">{mmss(remaining)}</span>
        </p>
      </div>
      <div className="mt-2 h-2 overflow-hidden rounded-full bg-panel" role="progressbar" aria-valuemin={0} aria-valuemax={q.duration_s} aria-valuenow={Math.round(elapsed)}>
        <div className="h-full bg-accent transition-all" style={{ width: `${(100 * elapsed) / q.duration_s}%` }} />
      </div>
      <p className="mt-2 text-xs text-ink-3">
        At {q.duration_s / 60} minutes the software stops recording automatically. This does <span className="font-semibold text-ink">not</span>{" "}
        disconnect the battery or stop the discharge current.
      </p>
    </div>
  );
}

function DisconnectStep({ t, onDone }: { t: BatteryTest; onDone: () => void }) {
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const confirm = async () => {
    setErr(null);
    try {
      const r = await api.confirmDisconnect(t.test_uid);
      setNote(r.note);
      onDone();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : String(e));
    }
  };
  return (
    <div role="alert" className="rounded-lg border-2 border-critical bg-critical/15 px-4 py-3">
      <p className="flex items-center gap-2 text-base font-semibold text-ink">
        <Unplug size={18} className="text-critical" /> Test ended — physically disconnect the battery / load now.
      </p>
      <p className="mt-1 text-sm text-ink-2">
        {t.quick_test?.auto_ended ? "The 300-second recording window ended. " : "Recording has stopped. "}
        The resistor stays connected and current keeps flowing until you remove it. The next test is blocked until you confirm.
      </p>
      <button onClick={confirm} className="mt-3 rounded-lg bg-critical px-3 py-1.5 text-sm font-semibold text-white hover:opacity-90">
        I have physically disconnected the load
      </button>
      {err && <div className="mt-2"><ErrorBox message={err} /></div>}
      {note && <p className="mt-2 text-xs text-ink-2">{note}</p>}
    </div>
  );
}

function TestDetail({ uid, onChanged }: { uid: string; onChanged: () => void }) {
  const d = usePoll(() => api.test(uid), 2000, [uid]);
  const [err, setErr] = useState<string | null>(null);
  const t = d.data;
  if (d.loading) return <Loading />;
  if (d.error) return <ErrorBox message={d.error.message} />;
  if (!t) return null;
  const q = t.quick_test;
  const r = t.results;
  const refresh = () => {
    d.reload();
    onChanged();
  };
  const run = async (a: "start" | "complete" | "abort") => {
    setErr(null);
    try {
      await api.testAction(t.test_uid, a, a === "abort" ? "aborted by operator" : undefined);
      refresh();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : String(e));
    }
  };
  const btn = "rounded-lg border px-3 py-1.5 text-sm font-medium disabled:opacity-40";
  const testNo = q?.number ?? null;

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <div className="rounded-lg border border-line bg-panel-2 px-4 py-3">
          <p className="text-[11px] uppercase tracking-wider text-ink-3">Test (software record)</p>
          <p className="mt-1 flex flex-wrap items-center gap-2">
            <Pill tone={STATE_TONE[t.state]}>{STATE_LABEL[t.state]}</Pill>
            {q && <Pill tone={QS_TONE[q.status]}>{q.status === "counted" ? `Valid Quick Test Cycle ${testNo}/${q.target}` : q.label}</Pill>}
            {!q && <Pill tone="neutral">Supervised test (legacy)</Pill>}
          </p>
          <p className="mt-1 text-xs text-ink-3">
            {t.test_uid} · {t.battery_id}
            {t.started_at && ` · started ${fmtTime(t.started_at, true)}`}
            {t.ended_at && ` · ended ${fmtTime(t.ended_at, true)}`}
          </p>
        </div>
        <div className="rounded-lg border border-line bg-panel-2 px-4 py-3">
          <p className="text-[11px] uppercase tracking-wider text-ink-3">Hardware (measured, not controlled)</p>
          <p className="mt-1 text-sm text-ink">{t.hardware_state.description}</p>
          <p className="mt-1 text-xs text-ink-3">
            {t.hardware_state.battery_selected_on ? `${t.battery_id} selected on ${t.hardware_state.battery_selected_on}` : `${t.battery_id} not selected on any device`}
          </p>
        </div>
      </div>

      {q && (t.state === "running" || t.state === "paused") && <Countdown t={t} fetchedAt={d.updatedAt ?? Date.now()} />}
      {q?.needs_disconnect_confirmation && <DisconnectStep t={t} onDone={refresh} />}

      {(t.state === "not_started" || t.state === "ready") && (
        <Card title="Pre-test checklist" subtitle="Required before the test can start">
          <ChecklistForm t={t} onSaved={refresh} />
        </Card>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <button className={`${btn} border-accent/50 text-accent`} disabled={t.state !== "ready"} onClick={() => run("start")}>
          Start recording
        </button>
        <button className={`${btn} border-line text-ink-2`} disabled={t.state !== "running"} onClick={() => run("complete")}>
          Stop early
        </button>
        <button className={`${btn} border-critical/50 text-critical`} disabled={t.state === "completed" || t.state === "aborted"} onClick={() => run("abort")}>
          Abort
        </button>
        <span className="text-xs text-ink-3">Buttons change the software record only. Stopping early makes the test incomplete.</span>
      </div>
      {err && <ErrorBox message={err} />}

      {r.available && (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <StatTile label="Discharged" value={fmt(r.delivered_mah, 1)} unit="mAh" foot={`${fmt(r.delivered_wh, 3)} Wh delivered`} />
            <StatTile label="Voltage" value={`${fmt(r.initial_voltage_v, 3)} → ${fmt(r.final_voltage_v, 3)}`} unit="V"
                      foot={`drop ${fmt(r.initial_voltage_v != null && r.final_voltage_v != null ? r.initial_voltage_v - r.final_voltage_v : null, 3)} V`} />
            <StatTile label="Avg current / power" value={fmt(r.mean_current_a, 3)} unit="A" foot={`${fmt(r.mean_power_w, 3)} W average`} />
            <StatTile label="Max temperature" value={fmt(r.max_temperature_c, 1)} unit="°C" foot={`duration ${fmtDuration(r.elapsed_s)}`} />
          </div>
          {q && q.status !== "in_progress" && (
            <Card title="Quick Test validity" subtitle="Only a complete 300 s window with valid telemetry counts as a Quick Test Cycle">
              <ul className="space-y-1 text-sm">
                {q.checks.map((c) => (
                  <li key={c.check} className="flex items-start gap-2">
                    {c.ok ? <CheckCircle2 size={15} className="mt-0.5 text-good" /> : <XCircle size={15} className="mt-0.5 text-critical" />}
                    <span className="text-ink">{c.check}</span>
                    <span className="text-ink-3">— {c.detail}</span>
                  </li>
                ))}
              </ul>
              <p className="mt-2 text-xs text-ink-3">
                A Quick Test Cycle is a 5-minute measurement, not a full charge–discharge cycle; it does not change EFC or the
                full-cycle count.
              </p>
              <a href={api.testReadingsCsvUrl(t.test_uid)} download className="mt-2 inline-flex items-center gap-1 text-xs text-accent hover:underline">
                <Download size={12} /> Readings of this test (CSV)
              </a>
            </Card>
          )}
        </>
      )}
    </div>
  );
}

/** Why "New 5-minute Quick Test" is disabled, or null when a test can be created. */
export function createBlockedReason(o: {
  options: string[]; selected: string; profilesLoading: boolean; quickError: string | null; pendingUid: string | null; busy: boolean;
}): string | null {
  if (!o.options.length) {
    if (o.profilesLoading) return "Loading battery profiles…";
    if (o.quickError)
      return `Battery profiles could not be loaded (${o.quickError}). If the backend was started before this update, restart it.`;
    return "No physical battery profiles found. Check the Batteries page.";
  }
  if (!o.selected) return "Choose a battery.";
  if (o.pendingUid)
    return `Safety step pending: confirm that the load was physically disconnected after ${o.pendingUid} (open it below) before starting another test.`;
  if (o.busy) return "Creating test…";
  return null;
}

export default function TestManager({ battery, batteries = [], batteriesLoading = false }: Partial<PageProps>) {
  const qt = usePoll(() => api.quickTests(), 5000, []);
  const [filter, setFilter] = useState<string>("all");
  const list = usePoll(() => api.tests(filter === "all" ? undefined : filter), 5000, [filter]);
  // Physical profiles come from the same battery list as the global selector; the quick-test
  // overview is only a fallback, so a failing statistics endpoint cannot empty the selector.
  const profileIds = batteries.filter((b) => b.managed && !b.archived).map((b) => b.battery_id);
  const BATS = profileIds.length ? profileIds : (qt.data?.batteries ?? []).map((b) => b.battery_id);
  const [picked, setNewBat] = useState<string | null>(null);
  const viewing = battery && BATS.includes(battery.battery_id) ? battery.battery_id : null;
  const newBat = picked && BATS.includes(picked) ? picked : viewing ?? BATS[0] ?? "";
  // Unconfirmed physical disconnect for the chosen battery (enforced by the backend too).
  const forNew = usePoll(newBat ? () => api.tests(newBat) : null, 5000, [newBat]);
  const pending = (forNew.data?.tests ?? []).find((t) => t.quick_test?.needs_disconnect_confirmation) ?? null;
  const [sel, setSel] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const tests = list.data?.tests ?? [];
  const current = sel ?? tests.find((t) => !["completed", "aborted"].includes(t.state) || t.quick_test?.needs_disconnect_confirmation)?.test_uid ?? tests[0]?.test_uid ?? null;
  const blocked = createBlockedReason({
    options: BATS, selected: newBat, profilesLoading: batteriesLoading || (qt.loading && !profileIds.length),
    quickError: qt.error?.message ?? null, pendingUid: pending?.test_uid ?? null, busy,
  });

  const create = async () => {
    setErr(null);
    setBusy(true);
    try {
      const r = await api.createTest(newBat);
      setSel(r.test_uid);
      list.reload();
      qt.reload();
      forNew.reload();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold">Quick Tests</h1>
      <HardwareWarning />

      <Card title="Protocol progress" subtitle="3 valid Quick Test Cycles of 300 s per battery · 9 tests in total">
        {qt.error && <ErrorBox message={`Quick Test overview unavailable: ${qt.error.message}`} />}
        {qt.loading ? <Loading /> : (
          <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
            {(qt.data?.batteries ?? []).map((b) => (
              <div key={b.battery_id} className="rounded-lg border border-line bg-panel-2 px-4 py-3">
                <div className="flex items-center justify-between">
                  <p className="font-medium text-ink">{b.battery_id}</p>
                  <Pill tone={b.complete ? "good" : "neutral"}>{b.complete ? "3 / 3 done" : `Next: Test ${b.next_test_number}/3`}</Pill>
                </div>
                <div className="mt-2 flex gap-2">
                  {[0, 1, 2].map((k) => {
                    const c = b.cycles[k];
                    return (
                      <div key={k} className={`flex-1 rounded-md border px-2 py-1.5 text-center text-xs ${c ? "border-good/50 bg-good/10 text-ink" : "border-line text-ink-3"}`}>
                        Test {k + 1}/3
                        <span className="block tabular">{c ? `${fmt(c.delivered_mah, 1)} mAh` : "—"}</span>
                      </div>
                    );
                  })}
                </div>
                {Object.values(b.not_counted).some((n) => n > 0) && (
                  <p className="mt-2 text-[11px] text-ink-3">
                    Not counted: {Object.entries(b.not_counted).filter(([, n]) => n > 0).map(([k, n]) => `${n} ${k.replace("_", " ")}`).join(", ")}
                  </p>
                )}
              </div>
            ))}
          </div>
        )}
        <div className="mt-4 flex flex-wrap items-end gap-3">
          <label className="text-xs text-ink-3">
            Battery
            <select aria-label="New test battery" value={newBat} onChange={(e) => setNewBat(e.target.value)}
                    className="ml-2 rounded-lg border border-line bg-panel-2 px-2.5 py-1.5 text-sm text-ink">
              {!BATS.length && <option value="">—</option>}
              {BATS.map((b) => <option key={b} value={b}>{b}</option>)}
            </select>
          </label>
          <button onClick={create} disabled={blocked != null} aria-describedby="create-reason"
                  className="rounded-lg bg-accent-strong px-3 py-1.5 text-sm font-medium text-page hover:bg-accent disabled:opacity-40">
            New 5-minute Quick Test
          </button>
          {pending && (
            <button onClick={() => setSel(pending.test_uid)} className="text-xs text-accent hover:underline">
              Open {pending.test_uid}
            </button>
          )}
        </div>
        {blocked && <p id="create-reason" role="status" className="mt-2 text-xs text-warning">{blocked}</p>}
        {err && <div className="mt-2"><ErrorBox message={err} /></div>}
      </Card>

      {current ? (
        <Card title="Current test">
          <TestDetail uid={current} onChanged={() => { list.reload(); qt.reload(); }} />
        </Card>
      ) : (
        <EmptyState title="No tests yet">Select the connected battery on the Batteries page, then create a Quick Test.</EmptyState>
      )}

      <Card
        title="Test history"
        subtitle="Every test is kept. Only valid 300 s tests count as Quick Test Cycles."
        actions={
          <div className="flex items-center gap-2">
            <Segmented label="Battery filter" value={filter} onChange={setFilter}
                       options={[{ value: "all", label: "All" }, ...BATS.map((b) => ({ value: b, label: b }))]} />
            <a href={api.testsCsvUrl(filter === "all" ? undefined : filter)} download
               className="inline-flex items-center gap-1 rounded-lg border border-accent/40 bg-accent/10 px-2.5 py-1 text-xs font-medium text-accent">
              <Download size={12} /> Export CSV
            </a>
          </div>
        }
      >
        {list.error && <ErrorBox message={list.error.message} />}
        <Table empty={!tests.length} head={["Test", "Battery", "Result", "Discharged", "Delivered", "Duration", "V drop", ""]}>
          {tests.map((t) => (
            <tr key={t.test_uid} className={t.test_uid === current ? "bg-accent/5" : ""}>
              <td className="px-3 py-1.5 text-ink">{t.test_uid}</td>
              <td className="px-3 py-1.5">{t.battery_id}</td>
              <td className="px-3 py-1.5">
                {t.quick_test ? (
                  <Pill tone={QS_TONE[t.quick_test.status]}>
                    {t.quick_test.status === "counted" ? `Valid ${t.quick_test.number}/${t.quick_test.target}` : t.quick_test.label}
                  </Pill>
                ) : (
                  <span className="text-ink-3">supervised (legacy) · {t.validity.classification}</span>
                )}
              </td>
              <td className="px-3 py-1.5">{t.results.available ? `${fmt(t.results.delivered_mah, 1)} mAh` : "—"}</td>
              <td className="px-3 py-1.5">{t.results.available ? `${fmt(t.results.delivered_wh, 3)} Wh` : "—"}</td>
              <td className="px-3 py-1.5">{t.results.available ? fmtDuration(t.results.elapsed_s) : "—"}</td>
              <td className="px-3 py-1.5">
                {t.results.available && t.results.initial_voltage_v != null && t.results.final_voltage_v != null
                  ? `${fmt(t.results.initial_voltage_v - t.results.final_voltage_v, 3)} V` : "—"}
              </td>
              <td className="px-3 py-1.5"><button className="text-accent hover:underline" onClick={() => setSel(t.test_uid)}>Open</button></td>
            </tr>
          ))}
        </Table>
      </Card>
    </div>
  );
}
