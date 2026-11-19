import { useEffect, useState } from "react";
import { CheckCircle2, ClipboardCheck, Download, Power, XCircle } from "lucide-react";
import { api, ApiError, type BatteryTest, type TestChecklist, type TestState } from "../lib/api";
import { usePoll } from "../lib/hooks";
import { fmt, fmtDuration, fmtTime } from "../lib/format";
import { Card, ErrorBox, Loading, Pill, Segmented, StatTile, Table } from "../components/ui";

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
        <p className="font-semibold">The Test Manager records experiments. It does not control hardware.</p>
        <p className="mt-0.5 text-ink-2">
          Starting, pausing, completing or aborting a test changes only the software record. Nothing connects, disconnects or
          switches the battery or the load resistor, and there is no automatic voltage cutoff or battery protection.{" "}
          <span className="font-semibold text-ink">Whenever a test stops or pauses, physically disconnect the discharge load.</span>
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

function Controls({ t, onDone }: { t: BatteryTest; onDone: (warning?: string) => void }) {
  const [reason, setReason] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const run = async (a: "start" | "pause" | "resume" | "complete" | "abort") => {
    setErr(null);
    try {
      const r = await api.testAction(t.test_uid, a, a === "abort" ? reason : undefined);
      onDone(r.warning);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : String(e));
    }
  };
  const btn = "rounded-lg border px-3 py-1.5 text-sm font-medium disabled:opacity-40";
  const s = t.state;
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <button className={`${btn} border-accent/50 text-accent`} disabled={s !== "ready"} onClick={() => run("start")}>Start test record</button>
        <button className={`${btn} border-warning/50 text-warning`} disabled={s !== "running"} onClick={() => run("pause")}>Pause</button>
        <button className={`${btn} border-accent/50 text-accent`} disabled={s !== "paused"} onClick={() => run("resume")}>Resume</button>
        <button className={`${btn} border-good/50 text-good`} disabled={s !== "running" && s !== "paused"} onClick={() => run("complete")}>Complete</button>
        <input aria-label="Abort reason" placeholder="abort reason" value={reason} onChange={(e) => setReason(e.target.value)}
               className="rounded-md border border-line bg-panel-2 px-2 py-1 text-sm text-ink" />
        <button className={`${btn} border-critical/50 text-critical`} disabled={s === "completed" || s === "aborted"} onClick={() => run("abort")}>Abort</button>
      </div>
      <p className="text-xs text-ink-3">These buttons change the test record only. They do not switch the load.</p>
      {err && <ErrorBox message={err} />}
    </div>
  );
}

function TestDetail({ uid, onChanged }: { uid: string; onChanged: () => void }) {
  const d = usePoll(() => api.test(uid), 3000, [uid]);
  const [warning, setWarning] = useState<string | null>(null);
  useEffect(() => setWarning(null), [uid]);
  const t = d.data;
  if (d.loading) return <Loading />;
  if (d.error) return <ErrorBox message={d.error.message} />;
  if (!t) return null;
  const r = t.results;
  const refresh = () => {
    d.reload();
    onChanged();
  };

  return (
    <div className="space-y-4">
      {warning && (
        <div role="alert" className="rounded-lg border-2 border-critical bg-critical/15 px-4 py-3 text-base font-semibold text-ink">
          ⚠ {warning}
        </div>
      )}
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <div className="rounded-lg border border-line bg-panel-2 px-4 py-3">
          <p className="text-[11px] uppercase tracking-wider text-ink-3">Test state (software record)</p>
          <p className="mt-1 flex items-center gap-2">
            <Pill tone={STATE_TONE[t.state]}>{STATE_LABEL[t.state]}</Pill>
            <span className="text-sm text-ink">{t.test_uid}</span>
          </p>
          <p className="mt-1 text-xs text-ink-3">
            {t.battery_id}
            {t.device_id ? ` on ${t.device_id}` : ""} · created {fmtTime(t.created_at, true)}
            {t.started_at && ` · started ${fmtTime(t.started_at, true)}`}
            {t.ended_at && ` · ended ${fmtTime(t.ended_at, true)}`}
          </p>
        </div>
        <div className="rounded-lg border border-line bg-panel-2 px-4 py-3">
          <p className="text-[11px] uppercase tracking-wider text-ink-3">Hardware state (measured, not controlled)</p>
          <p className="mt-1 text-sm text-ink">{t.hardware_state.description}</p>
          <p className="mt-1 text-xs text-ink-3">
            {t.hardware_state.battery_selected_on ? `${t.battery_id} selected on ${t.hardware_state.battery_selected_on}` : `${t.battery_id} not selected on any device`}
            {t.hardware_state.last_reading_ts && ` · last reading ${fmtTime(t.hardware_state.last_reading_ts)}`}
          </p>
        </div>
      </div>

      {(t.state === "not_started" || t.state === "ready") && (
        <Card title="Pre-test checklist" subtitle="Each item is a record of what a person checked. All are required before the test can start.">
          <ChecklistForm t={t} onSaved={refresh} />
        </Card>
      )}

      <Controls t={t} onDone={(w) => { setWarning(w ?? null); refresh(); }} />

      {r.available ? (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <StatTile label="Delivered" value={fmt(r.delivered_mah, 1)} unit="mAh" foot={`${fmt(r.delivered_wh, 3)} Wh`} />
            <StatTile label="Elapsed (running)" value={fmtDuration(r.elapsed_s)} foot={`load on ${fmtDuration(r.load_on_s)}`} />
            <StatTile label="Voltage" value={`${fmt(r.initial_voltage_v, 3)} → ${fmt(r.final_voltage_v, 3)}`} unit="V" foot={`initial → final · min loaded ${fmt(r.min_loaded_voltage_v, 3)} V`} />
            <StatTile label="Current / power" value={fmt(r.mean_current_a, 3)} unit="A" foot={`peak ${fmt(r.peak_current_a, 3)} A · mean ${fmt(r.mean_power_w, 3)} W`} />
            <StatTile label="Temperature" value={fmt(r.max_temperature_c, 1)} unit="°C max" foot={`initial ${fmt(r.initial_temperature_c, 1)} · final ${fmt(r.final_temperature_c, 1)} °C`} />
          </div>
          <Card title="Data quality and validity" subtitle={t.validity.classification}>
            <div className="mb-3 flex flex-wrap gap-2 text-xs">
              <Pill tone={r.gaps?.length ? "warning" : "good"}>{r.gaps?.length ? `${r.gaps.length} telemetry gap(s)` : "no telemetry gaps"}</Pill>
              <Pill tone={r.missing_measurements?.readings_with_any_missing ? "warning" : "good"}>
                {r.missing_measurements?.readings_with_any_missing ?? 0} readings with missing values
              </Pill>
              {r.paused && <Pill tone="warning">paused{r.load_current_while_paused_readings ? " — load current measured while paused" : ""}</Pill>}
              {r.battery_stayed_selected === false && <Pill tone="critical">interrupted: battery selection changed</Pill>}
              <Pill tone={t.validity.valid_for_full_capacity ? "good" : "neutral"}>
                {t.validity.valid_for_full_capacity ? "valid for full-capacity / cycle analysis" : "not valid for full-capacity analysis"}
              </Pill>
            </div>
            <ul className="space-y-1 text-sm">
              {t.validity.checks.map((c) => (
                <li key={c.check} className="flex items-start gap-2">
                  {c.ok ? <CheckCircle2 size={15} className="mt-0.5 text-good" /> : <XCircle size={15} className="mt-0.5 text-critical" />}
                  <span className="text-ink">{c.check}</span>
                  <span className="text-ink-3">— {c.detail}</span>
                </li>
              ))}
            </ul>
            {!!r.gaps?.length && (
              <p className="mt-2 text-xs text-ink-3">
                Gaps: {r.gaps.map((g) => `${fmtTime(g.from)}–${fmtTime(g.to)} (${fmt(g.duration_s, 0)} s)`).join(", ")}. No current is assumed across them.
              </p>
            )}
            <p className="mt-2 text-xs text-ink-3">SoH / RUL: unavailable for physical batteries. </p>
            <a href={api.testReadingsCsvUrl(t.test_uid)} download className="mt-2 inline-flex items-center gap-1 text-xs text-accent hover:underline">
              <Download size={12} /> Download this test's readings (CSV)
            </a>
          </Card>
        </>
      ) : (
        <p className="text-sm text-ink-3">{r.reason}</p>
      )}
    </div>
  );
}

export default function TestManager() {
  const profiles = usePoll(() => api.compare(), 30000, []);
  const BATS = (profiles.data?.batteries ?? []).map((b) => b.battery_id);
  const [filter, setFilter] = useState<string>("all");
  const [picked, setNewBat] = useState<string | null>(null);
  const newBat = picked && BATS.includes(picked) ? picked : BATS[0] ?? "";
  const [notes, setNotes] = useState("");
  const [sel, setSel] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const list = usePoll(() => api.tests(filter === "all" ? undefined : filter), 5000, [filter]);
  const tests = list.data?.tests ?? [];
  const current = sel ?? tests.find((t) => !["completed", "aborted"].includes(t.state))?.test_uid ?? tests[0]?.test_uid ?? null;

  const create = async () => {
    setErr(null);
    try {
      const r = await api.createTest(newBat, notes);
      setSel(r.test_uid);
      setNotes("");
      list.reload();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : String(e));
    }
  };

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold">Test Manager</h1>
      <HardwareWarning />

      <Card title="New test" subtitle="Creates a uniquely identified test record for one physical battery">
        <div className="flex flex-wrap items-end gap-3">
          <label className="text-xs text-ink-3">
            Battery
            <select aria-label="New test battery" value={newBat} onChange={(e) => setNewBat(e.target.value)}
                    className="ml-2 rounded-lg border border-line bg-panel-2 px-2.5 py-1.5 text-sm text-ink">
              {BATS.map((b) => <option key={b} value={b}>{b}</option>)}
            </select>
          </label>
          <input aria-label="Notes" placeholder="notes (optional)" value={notes} onChange={(e) => setNotes(e.target.value)}
                 className="rounded-md border border-line bg-panel-2 px-2 py-1.5 text-sm text-ink" />
          <button onClick={create} disabled={!newBat} className="rounded-lg bg-accent-strong px-3 py-1.5 text-sm font-medium text-page hover:bg-accent">
            Create test
          </button>
        </div>
        {err && <div className="mt-2"><ErrorBox message={err} /></div>}
      </Card>

      {current && (
        <Card title="Selected test">
          <TestDetail uid={current} onChanged={list.reload} />
        </Card>
      )}

      <Card
        title="Test history"
        subtitle="All tests are kept, including aborted ones"
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
        <Table empty={!tests.length} head={["Test", "Battery", "State", "Result", "Delivered", "Energy", "Elapsed", "V start → end", "Gaps", ""]}>
          {tests.map((t) => (
            <tr key={t.test_uid} className={t.test_uid === current ? "bg-accent/5" : ""}>
              <td className="px-3 py-1.5 text-ink">{t.test_uid}</td>
              <td className="px-3 py-1.5">{t.battery_id}</td>
              <td className="px-3 py-1.5"><Pill tone={STATE_TONE[t.state]}>{STATE_LABEL[t.state]}</Pill></td>
              <td className="px-3 py-1.5">{t.validity.classification}</td>
              <td className="px-3 py-1.5">{t.results.available ? `${fmt(t.results.delivered_mah, 1)} mAh` : "—"}</td>
              <td className="px-3 py-1.5">{t.results.available ? `${fmt(t.results.delivered_wh, 3)} Wh` : "—"}</td>
              <td className="px-3 py-1.5">{t.results.available ? fmtDuration(t.results.elapsed_s) : "—"}</td>
              <td className="px-3 py-1.5">{t.results.available ? `${fmt(t.results.initial_voltage_v, 2)} → ${fmt(t.results.final_voltage_v, 2)}` : "—"}</td>
              <td className="px-3 py-1.5">{t.results.available ? t.results.gaps?.length ?? 0 : "—"}</td>
              <td className="px-3 py-1.5"><button className="text-accent hover:underline" onClick={() => setSel(t.test_uid)}>Open</button></td>
            </tr>
          ))}
        </Table>
      </Card>
    </div>
  );
}
