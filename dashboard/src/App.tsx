import { useEffect, useMemo, useState } from "react";
import { Activity, Bell, BatteryCharging, ClipboardList, FlaskConical, Gauge, History, Layers, Server, TrendingDown, Zap } from "lucide-react";
import { api, type BatterySummary } from "./lib/api";
import { useLocalStorage, usePoll } from "./lib/hooks";
import { SOURCE_LABEL } from "./lib/format";
import { Pill } from "./components/ui";
import Overview from "./pages/Overview";
import Live from "./pages/Live";
import Health from "./pages/Health";
import HistoryPage from "./pages/History";
import Alerts from "./pages/Alerts";
import SystemPage from "./pages/System";
import Batteries from "./pages/Batteries";
import MLValidation from "./pages/MLValidation";
import Aging from "./pages/Aging";
import TestManager from "./pages/TestManager";

const PAGES = [
  { id: "overview", label: "Overview", icon: Gauge },
  { id: "batteries", label: "Batteries", icon: Layers },
  { id: "aging", label: "Aging analytics", icon: TrendingDown },
  { id: "tests", label: "Test Manager", icon: ClipboardList },
  { id: "live", label: "Live", icon: Activity },
  { id: "health", label: "Battery health", icon: BatteryCharging },
  { id: "history", label: "History", icon: History },
  { id: "alerts", label: "Alerts", icon: Bell },
  { id: "ml", label: "ML validation", icon: FlaskConical },
  { id: "system", label: "System", icon: Server },
] as const;
type PageId = (typeof PAGES)[number]["id"];

function usePage(): [PageId, (p: PageId) => void] {
  const read = () => {
    const h = window.location.hash.replace("#/", "").replace("#", "");
    return (PAGES.some((p) => p.id === h) ? h : "overview") as PageId;
  };
  const [page, setPage] = useState<PageId>(read);
  useEffect(() => {
    const on = () => setPage(read());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return [page, (p) => (window.location.hash = `#/${p}`)];
}

export interface PageProps {
  battery: BatterySummary | undefined;
  batteries: BatterySummary[];
  batteriesLoading: boolean;
}

export default function App() {
  const [page, go] = usePage();
  const batteries = usePoll(() => api.batteries(), 5000, []);
  const health = usePoll(() => api.health(), 10000, []);
  const [selected, setSelected] = useLocalStorage("bhm.battery", "");

  const list = batteries.data?.batteries ?? [];
  const battery = useMemo(() => list.find((b) => b.battery_id === selected) ?? list[0], [list, selected]);
  const alertTotal = list.reduce((n, b) => n + (b.active_alerts.critical ?? 0) + (b.active_alerts.warning ?? 0), 0);

  const apiUp = !health.error && health.data;
  const props: PageProps = { battery, batteries: list, batteriesLoading: batteries.loading };

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
            <label className="flex items-center gap-2 text-xs text-ink-3" title="Which battery's data to view. To change the physically connected battery, use the Batteries page.">
              Viewing
              <select
                aria-label="Select battery"
                value={battery?.battery_id ?? ""}
                onChange={(e) => setSelected(e.target.value)}
                disabled={!list.length}
                className="max-w-[16rem] rounded-lg border border-line bg-panel-2 px-2.5 py-1.5 text-sm text-ink focus-visible:outline-2 focus-visible:outline-accent"
              >
                {!list.length && <option value="">{batteries.loading ? "Loading…" : "No batteries yet"}</option>}
                {list.map((b) => (
                  <option key={b.battery_id} value={b.battery_id}>
                    {b.label || b.battery_id} — {SOURCE_LABEL[b.source]}
                  </option>
                ))}
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
              {id === "alerts" && alertTotal > 0 && (
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
      {battery && battery.source !== "hardware" && health.data?.mode !== "simulation" && page !== "ml" && page !== "aging" && page !== "tests" && (
        <div className="border-b border-warning/30 bg-warning/10">
          <p className="mx-auto max-w-7xl px-4 py-2 text-xs text-warning sm:px-6">
            {battery.source === "simulation"
              ? "DEMO MODE — this battery receives synthetic simulator data, not hardware measurements."
              : "NASA REPLAY — real NASA PCoE cycle features replayed through the model (no live device). Model output on this data is in-sample."}
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
        {page === "live" && <Live {...props} />}
        {page === "health" && <Health {...props} />}
        {page === "history" && <HistoryPage {...props} />}
        {page === "alerts" && <Alerts {...props} />}
        {page === "system" && <SystemPage />}
        {page === "ml" && <MLValidation />}
        {page === "aging" && <Aging />}
        {page === "tests" && <TestManager />}
        {page === "batteries" && (
          <Batteries
            selectBattery={(id, p) => {
              setSelected(id);
              go((p ?? "overview") as PageId);
            }}
          />
        )}
      </main>

      <footer className="mx-auto max-w-7xl px-4 pb-8 pt-2 text-[11px] text-ink-3 sm:px-6">
        SoH/RUL models were trained on three NASA 2.0 Ah cells. Live estimates are experimental and are withheld when the
        input is outside the training range. Alerts are monitoring signals only — protection must be implemented in hardware.
      </footer>
    </div>
  );
}
