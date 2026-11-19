import { useEffect, useMemo, useState } from "react";
import { BrainCircuit, ClipboardList, Gauge, Layers, LineChart as LineChartIcon, Zap } from "lucide-react";
import { api, type BatterySummary } from "./lib/api";
import { useLocalStorage, usePoll } from "./lib/hooks";
import { Pill } from "./components/ui";
import Overview from "./pages/Overview";
import Batteries from "./pages/Batteries";
import MLValidation from "./pages/MLValidation";
import Aging from "./pages/Aging";
import TestManager from "./pages/TestManager";

const PAGES = [
  { id: "overview", label: "Overview", icon: Gauge },
  { id: "batteries", label: "Batteries", icon: Layers },
  { id: "tests", label: "Quick Tests", icon: ClipboardList },
  { id: "analysis", label: "Battery Analysis", icon: LineChartIcon },
  { id: "ml", label: "Battery Health Predictions", icon: BrainCircuit },
] as const;
type PageId = (typeof PAGES)[number]["id"];

// Sections merged in the final layout keep working as links.
const ALIASES: Record<string, PageId> = {
  live: "overview", alerts: "overview", system: "batteries", history: "analysis", aging: "analysis", health: "ml",
};

function usePage(): [PageId, (p: string) => void] {
  const read = () => {
    const h = window.location.hash.replace("#/", "").replace("#", "");
    return (PAGES.some((p) => p.id === h) ? h : ALIASES[h] ?? "overview") as PageId;
  };
  const [page, setPage] = useState<PageId>(read);
  useEffect(() => {
    const on = () => setPage(read());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return [page, (p) => (window.location.hash = `#/${ALIASES[p] ?? p}`)];
}

export interface PageProps {
  battery: BatterySummary | undefined;
  batteries: BatterySummary[];
  batteriesLoading: boolean;
  selectBattery?: (id: string, page?: string) => void;
}

export default function App() {
  const [page, go] = usePage();
  const batteries = usePoll(() => api.batteries(), 5000, []);
  const health = usePoll(() => api.health(), 10000, []);
  const [selected, setSelected] = useLocalStorage("bhm.battery", "");

  const all = batteries.data?.batteries ?? [];
  const active = all.filter((b) => !b.archived);
  const archived = all.filter((b) => b.archived);
  const battery = useMemo(
    () => all.find((b) => b.battery_id === selected) ?? active[0] ?? all[0],
    [all, active, selected],
  );
  const alertTotal = active.reduce((n, b) => n + (b.active_alerts.critical ?? 0) + (b.active_alerts.warning ?? 0), 0);

  const apiUp = !health.error && health.data;
  const selectBattery = (id: string, p?: string) => {
    setSelected(id);
    if (p) go(p);
  };
  const props: PageProps = { battery, batteries: active, batteriesLoading: batteries.loading, selectBattery };

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-20 border-b border-line bg-page/90 backdrop-blur">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-6 gap-y-3 px-4 py-3 sm:px-6">
          <div className="flex items-center gap-2.5">
            <div className="grid h-8 w-8 place-items-center rounded-lg bg-accent/15 text-accent">
              <Zap size={18} />
            </div>
            <div className="leading-tight">
              <p className="text-sm font-semibold">Battery Health Monitor</p>
              <p className="text-[11px] text-ink-3">ESP32 · INA219 · DS18B20 · NASA-trained ML</p>
            </div>
          </div>

          <div className="ml-auto flex flex-wrap items-center gap-3">
            <label className="flex items-center gap-2 text-xs text-ink-3" title="Which battery's data to view. The physically connected battery is set on the Batteries page.">
              Viewing
              <select
                aria-label="Select battery"
                value={battery?.battery_id ?? ""}
                onChange={(e) => setSelected(e.target.value)}
                disabled={!all.length}
                className="max-w-[16rem] rounded-lg border border-line bg-panel-2 px-2.5 py-1.5 text-sm text-ink focus-visible:outline-2 focus-visible:outline-accent"
              >
                {!all.length && <option value="">{batteries.loading ? "Loading…" : "No batteries yet"}</option>}
                {active.map((b) => (
                  <option key={b.battery_id} value={b.battery_id}>
                    {b.battery_id}
                  </option>
                ))}
                {archived.length > 0 && (
                  <optgroup label="Archive (read-only)">
                    {archived.map((b) => (
                      <option key={b.battery_id} value={b.battery_id}>
                        {b.battery_id} (archived)
                      </option>
                    ))}
                  </optgroup>
                )}
              </select>
            </label>
            <Pill tone={apiUp ? (health.data?.status === "ok" ? "good" : "warning") : "critical"}>
              API {apiUp ? (health.data?.status === "ok" ? "connected" : "degraded") : health.loading ? "…" : "unreachable"}
            </Pill>
          </div>
        </div>

        <nav className="mx-auto flex max-w-7xl gap-1 overflow-x-auto px-2 sm:px-4" aria-label="Sections">
          {PAGES.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              onClick={() => go(id)}
              aria-current={page === id ? "page" : undefined}
              className={`flex items-center gap-1.5 whitespace-nowrap border-b-2 px-3 py-2.5 text-sm transition-colors focus-visible:outline-2 focus-visible:outline-accent ${
                page === id ? "border-accent text-ink" : "border-transparent text-ink-3 hover:text-ink-2"
              }`}
            >
              <Icon size={15} />
              {label}
              {id === "overview" && alertTotal > 0 && (
                <span className="tabular rounded-full bg-critical/20 px-1.5 text-[11px] font-semibold text-critical">{alertTotal}</span>
              )}
            </button>
          ))}
        </nav>
      </header>

      {health.data?.mode === "simulation" && (
        <div role="status" className="border-b-2 border-warning bg-warning/20">
          <p className="mx-auto max-w-7xl px-4 py-2 text-sm font-semibold text-warning sm:px-6">
            SIMULATION MODE — every value on this dashboard is synthetic, from the simulation database. These are not
            measurements of a real battery.
          </p>
        </div>
      )}
      {battery?.archived && (
        <div className="border-b border-warning/30 bg-warning/10">
          <p className="mx-auto max-w-7xl px-4 py-2 text-xs text-warning sm:px-6">
            ARCHIVED RECORD (read-only) — {battery.battery_id} is not part of the current three-battery protocol. Its history is
            kept as recorded and is never merged into BAT-A, BAT-B or BAT-C.
          </p>
        </div>
      )}

      <main className="mx-auto max-w-7xl px-4 py-6 sm:px-6">
        {health.error && !health.data && (
          <div className="mb-4 rounded-lg border border-critical/50 bg-critical/10 px-4 py-3 text-sm">
            Cannot reach the backend ({health.error.message}). Start it with{" "}
            <code className="rounded bg-panel-2 px-1.5 py-0.5 font-mono text-xs">uvicorn iot.backend.main:app --host 0.0.0.0 --port 8000</code>{" "}
            from the repository root.
          </div>
        )}
        {page === "overview" && <Overview {...props} />}
        {page === "batteries" && <Batteries {...props} />}
        {page === "tests" && <TestManager {...props} />}
        {page === "analysis" && <Aging {...props} />}
        {page === "ml" && <MLValidation />}
      </main>

      <footer className="mx-auto max-w-7xl px-4 pb-8 pt-2 text-[11px] text-ink-3 sm:px-6">
        Software monitoring only: alerts, buttons and the Quick Test timer cannot disconnect the battery or the load. No validated
        SoH or RUL model exists for the physical batteries: at most a gated Experimental ML Estimate is shown, and the NASA
        model demonstration is kept separate.
      </footer>
    </div>
  );
}
