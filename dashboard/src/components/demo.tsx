import { FlaskConical } from "lucide-react";
import type { DemoEstimate } from "../lib/api";
import { fmt } from "../lib/format";
import { Pill } from "./ui";
import { SAMPLE_MODE, SAMPLE_RESULT } from "../lib/sample";

export const DEMO_LABEL = "Experimental Demo Estimate";

export function SamplePill() {
  return (
    <Pill tone="info" title="Fixed sample values for demonstration; not measured, not computed from this battery, not a model output">
      {SAMPLE_RESULT.label}
    </Pill>
  );
}

/** Status badge for the SoH/RUL values currently displayed. */
export function ResultPill() {
  return SAMPLE_MODE ? <SamplePill /> : <DemoPill />;
}

export function DemoPill() {
  return (
    <Pill tone="warning" icon={<FlaskConical size={12} />} title="Illustrative heuristic score from the Quick Tests; not measured, not validated, not ML">
      {DEMO_LABEL}
    </Pill>
  );
}

/** The SoH/RUL cards. In Sample Demonstration mode they show the labelled sample values;
 *  otherwise the computed demo scores ("No estimate yet" until the valid Quick Tests exist). */
export function DemoEstimateTiles({ e, compact = false }: { e: DemoEstimate | undefined; compact?: boolean }) {
  const ok = e?.status === "demo";
  const tiles = SAMPLE_MODE
    ? [
        { label: "SoH", value: SAMPLE_RESULT.soh_pct.toFixed(1), unit: "%" },
        { label: "RUL", value: String(SAMPLE_RESULT.rul_cycles), unit: "remaining cycles" },
      ]
    : [
        { label: "Demo SoH Estimate", value: ok ? fmt(e!.soh_pct, 1) : null, unit: "%" },
        { label: "Demo RUL Estimate", value: ok ? String(e!.rul_cycles) : null, unit: "cycles" },
      ];
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
      {tiles.map((t) => (
        <div key={t.label} className="rounded-xl border border-warning/40 bg-panel px-5 py-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-xs uppercase tracking-wider text-ink-3">{t.label}</p>
            <ResultPill />
          </div>
          {t.value != null ? (
            <p className={`tabular mt-2 font-semibold text-ink ${compact ? "text-3xl" : "text-4xl"}`}>
              {t.value} <span className="text-base font-normal text-ink-2">{t.unit}</span>
            </p>
          ) : (
            <p className="mt-2 text-2xl font-semibold text-ink-3">No estimate yet</p>
          )}
          <p className="mt-1 text-xs text-ink-3">
            {SAMPLE_MODE
              ? null
              : ok
              ? `From ${e!.tests_used.length} measured 300 s Quick Tests · illustrative, not a measured or validated value`
              : e?.reason ?? "Needs completed valid Quick Tests"}
          </p>
        </div>
      ))}
    </div>
  );
}

