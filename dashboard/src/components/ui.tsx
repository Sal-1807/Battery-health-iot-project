import type { ReactNode } from "react";
import {
  AlertOctagon,
  AlertTriangle,
  CheckCircle2,
  CircleDashed,
  CircleSlash,
  FlaskConical,
  Info,
  Loader2,
  WifiOff,
} from "lucide-react";
import type { ConnState, PredictionStatus } from "../lib/api";
import { SOURCE_LABEL } from "../lib/format";

export function Card({
  title,
  subtitle,
  actions,
  children,
  className = "",
}: {
  title?: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`rounded-xl border border-line bg-panel p-4 sm:p-5 ${className}`}>
      {(title || actions) && (
        <header className="mb-4 flex flex-wrap items-start justify-between gap-2">
          <div className="min-w-0">
            {title && <h2 className="text-sm font-semibold tracking-wide text-ink">{title}</h2>}
            {subtitle && <p className="mt-0.5 text-xs text-ink-3">{subtitle}</p>}
          </div>
          {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
        </header>
      )}
      {children}
    </section>
  );
}

type Tone = "good" | "warning" | "serious" | "critical" | "neutral" | "accent" | "info";

const TONE: Record<Tone, string> = {
  good: "text-good border-good/40 bg-good/10",
  warning: "text-warning border-warning/40 bg-warning/10",
  serious: "text-serious border-serious/40 bg-serious/10",
  critical: "text-critical border-critical/50 bg-critical/10",
  neutral: "text-ink-2 border-line bg-panel-2",
  accent: "text-accent border-accent/40 bg-accent/10",
  info: "text-series-1 border-series-1/40 bg-series-1/10",
};

export function Pill({ tone = "neutral", icon, children, title }: { tone?: Tone; icon?: ReactNode; children: ReactNode; title?: string }) {
  return (
    <span
      title={title}
      className={`inline-flex items-center gap-1 whitespace-nowrap rounded-full border px-2 py-0.5 text-[11px] font-medium ${TONE[tone]}`}
    >
      {icon}
      {children}
    </span>
  );
}

export function ConnectionPill({ state, age }: { state: ConnState; age?: string }) {
  const map: Record<ConnState, [Tone, ReactNode, string]> = {
    online: ["good", <CheckCircle2 size={12} />, "Online"],
    stale: ["warning", <AlertTriangle size={12} />, "Stale"],
    offline: ["critical", <WifiOff size={12} />, "Offline"],
    never_seen: ["neutral", <CircleDashed size={12} />, "No data yet"],
    not_applicable: ["neutral", <Info size={12} />, "Replay (no device)"],
  };
  const [tone, icon, label] = map[state];
  return (
    <Pill tone={tone} icon={icon}>
      {label}
      {age ? ` · ${age}` : ""}
    </Pill>
  );
}

export function SeverityPill({ severity }: { severity: "info" | "warning" | "critical" }) {
  if (severity === "critical") return <Pill tone="critical" icon={<AlertOctagon size={12} />}>Critical</Pill>;
  if (severity === "warning") return <Pill tone="warning" icon={<AlertTriangle size={12} />}>Warning</Pill>;
  return <Pill tone="info" icon={<Info size={12} />}>Info</Pill>;
}

export function PredictionPill({ status }: { status: PredictionStatus | undefined }) {
  if (status === "experimental")
    return <Pill tone="warning" icon={<FlaskConical size={12} />} title="Live estimate from a NASA-trained model; not validated on this cell">Experimental</Pill>;
  if (status === "nasa_in_sample")
    return <Pill tone="info" icon={<Info size={12} />} title="Model trained on this NASA data; in-sample output">NASA in-sample</Pill>;
  return <Pill tone="neutral" icon={<CircleSlash size={12} />}>Unavailable</Pill>;
}

/** Provenance tag: what kind of number this is. */
export function Provenance({ kind }: { kind: "measured" | "simulated" | "derived" | "model" | "replay" | "unavailable" }) {
  const labels = {
    measured: "Measured",
    simulated: "Simulated",
    derived: "Derived",
    model: "Model output",
    replay: "NASA dataset",
    unavailable: "Unavailable",
  } as const;
  const tone: Record<typeof kind, Tone> = {
    measured: "accent",
    simulated: "warning",
    derived: "neutral",
    model: "info",
    replay: "info",
    unavailable: "neutral",
  };
  return <Pill tone={tone[kind]}>{labels[kind]}</Pill>;
}

export function SourceBadge({ source }: { source: string }) {
  if (source === "hardware") return <Pill tone="accent">{SOURCE_LABEL.hardware}</Pill>;
  if (source === "simulation") return <Pill tone="warning" icon={<FlaskConical size={12} />}>{SOURCE_LABEL.simulation}</Pill>;
  return <Pill tone="info">{SOURCE_LABEL[source] ?? source}</Pill>;
}

export function StatTile({
  label,
  value,
  unit,
  tag,
  foot,
  muted = false,
}: {
  label: string;
  value: ReactNode;
  unit?: string;
  tag?: ReactNode;
  foot?: ReactNode;
  muted?: boolean;
}) {
  return (
    <div className="flex min-w-0 flex-col rounded-xl border border-line bg-panel p-4">
      <div className="flex items-start justify-between gap-2">
        <span className="text-xs font-medium uppercase tracking-wider text-ink-3">{label}</span>
        {tag}
      </div>
      <div className={`tabular mt-3 flex items-baseline gap-1.5 ${muted ? "text-ink-3" : "text-ink"}`}>
        <span className="text-3xl font-semibold leading-none">{value}</span>
        {unit && <span className="text-sm text-ink-2">{unit}</span>}
      </div>
      {foot && <div className="mt-3 text-xs leading-snug text-ink-3">{foot}</div>}
    </div>
  );
}

export function EmptyState({ icon, title, children }: { icon?: ReactNode; title: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-line px-4 py-10 text-center">
      <div className="text-ink-3">{icon ?? <CircleDashed size={22} />}</div>
      <p className="text-sm font-medium text-ink-2">{title}</p>
      {children && <div className="max-w-xl text-xs leading-relaxed text-ink-3">{children}</div>}
    </div>
  );
}

export function ErrorBox({ message }: { message: string }) {
  return (
    <div role="alert" className="flex items-start gap-2 rounded-lg border border-critical/50 bg-critical/10 px-3 py-2 text-sm text-ink">
      <AlertOctagon size={16} className="mt-0.5 shrink-0 text-critical" />
      <span>{message}</span>
    </div>
  );
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 py-8 text-sm text-ink-3">
      <Loader2 size={16} className="animate-spin" /> {label}
    </div>
  );
}

export function Notice({ tone = "warning", title, children }: { tone?: "warning" | "info"; title: string; children: ReactNode }) {
  const cls = tone === "warning" ? "border-warning/40 bg-warning/5" : "border-series-1/40 bg-series-1/5";
  const Icon = tone === "warning" ? AlertTriangle : Info;
  return (
    <div className={`flex gap-3 rounded-lg border px-4 py-3 ${cls}`}>
      <Icon size={16} className={`mt-0.5 shrink-0 ${tone === "warning" ? "text-warning" : "text-series-1"}`} />
      <div className="text-xs leading-relaxed text-ink-2">
        <p className="mb-1 text-sm font-medium text-ink">{title}</p>
        {children}
      </div>
    </div>
  );
}

export function Segmented<T extends string>({
  value,
  options,
  onChange,
  label,
}: {
  value: T;
  options: { value: T; label: string }[];
  onChange: (v: T) => void;
  label: string;
}) {
  return (
    <div role="radiogroup" aria-label={label} className="inline-flex rounded-lg border border-line bg-panel-2 p-0.5">
      {options.map((o) => (
        <button
          key={o.value}
          role="radio"
          aria-checked={value === o.value}
          onClick={() => onChange(o.value)}
          className={`rounded-md px-2.5 py-1 text-xs font-medium transition-colors focus-visible:outline-2 focus-visible:outline-accent ${
            value === o.value ? "bg-accent/15 text-accent" : "text-ink-2 hover:text-ink"
          }`}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Table({ head, children, empty }: { head: ReactNode[]; children: ReactNode; empty?: boolean }) {
  return (
    <div className="overflow-x-auto rounded-lg border border-line">
      <table className="tabular w-full min-w-max text-left text-xs">
        <thead className="bg-panel-2 text-ink-3">
          <tr>
            {head.map((h, i) => (
              <th key={i} className="whitespace-nowrap px-3 py-2 font-medium">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-line text-ink-2">{children}</tbody>
      </table>
      {empty && <p className="px-3 py-6 text-center text-xs text-ink-3">No rows.</p>}
    </div>
  );
}
