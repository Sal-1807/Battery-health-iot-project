"""
NASA Li-ion Battery Aging Dataset - dataset-level suitability assessment.

NO model training. NO interpolation or imputation of missing EOL values.
Discharge capacity is used here only to assess data quality / EOL reachability,
never as an ML feature.

Per battery .mat file:
  - scipy.io.loadmat, keep discharge cycles only, original order preserved
  - extract Capacity from each discharge cycle
  - first cycle with Capacity <= 1.4 Ah (70% of 2.0 Ah rated) = EOL
  - data-quality + trajectory checks, exclusion decision with a specific reason
"""

import glob
import hashlib
import os
from collections import Counter

import numpy as np
import pandas as pd
import scipy.io
from scipy.stats import spearmanr

EOL_THRESHOLD = 1.4       # Ah - 70% of rated
RATED = 2.0               # Ah
MIN_CYCLES = 30           # min discharge cycles for a usable degradation trajectory
CONFIRM_WIN = 5           # window for confirming a sustained EOL crossing
EOL_TOL = 5               # literal vs confirmed crossing may differ by this many cycles
                          # (capacity-regeneration bounce) before the EOL is called spurious
REGIME_FRAC = 0.35        # < this * series median => collapsed-capacity cycle
REGIME_MINLEN = 3         # contiguous collapsed cycles to call it a regime break
COLD_C = 10.0             # ambient at/below this = cold-temperature test

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repo root (src/ -> ..)
SRC = os.path.join(ROOT, "data", "raw")     # git-ignored NASA .mat files
OUTDIR = os.path.join(ROOT, "results")


# ---------------------------------------------------------------- inventory --
def discover_files():
    recs = []
    for f in sorted(glob.glob(os.path.join(SRC, "**", "*.mat"), recursive=True)):
        recs.append({
            "path": f,
            "battery_id": os.path.splitext(os.path.basename(f))[0],
            # name of the archive folder holding this .mat (works for any absolute root)
            "archive": os.path.basename(os.path.dirname(f)),
            "size_MB": round(os.path.getsize(f) / 1e6, 2),
            "md5": hashlib.md5(open(f, "rb").read()).hexdigest(),
        })
    inv = pd.DataFrame(recs)
    inv["is_duplicate_copy"] = inv.duplicated("md5", keep="first")
    return inv


# --------------------------------------------------------------- extraction --
def extract_discharge_capacities(path, battery_id):
    """Discharge-cycle capacities in original order. Returns (list, diag)."""
    mat = scipy.io.loadmat(path)
    top = [k for k in mat if not k.startswith("__")]
    key = battery_id if battery_id in mat else top[0]
    cycles = mat[key]["cycle"][0, 0]

    types = Counter()
    ambient = set()
    caps = []                       # (raw_cycle_index, capacity or nan)
    n_missing_field = n_empty = 0

    for i in range(cycles.shape[1]):
        c = cycles[0, i]
        ctype = str(c["type"][0]).strip().lower()
        types[ctype] += 1
        if ctype != "discharge":
            continue

        try:
            ambient.add(float(np.ravel(c["ambient_temperature"])[0]))
        except Exception:
            pass

        data = c["data"]
        if "Capacity" not in (data.dtype.names or ()):
            n_missing_field += 1
            caps.append((i, np.nan))
            continue

        arr = np.ravel(np.asarray(data["Capacity"]).squeeze())
        while arr.dtype == object and arr.size > 0:      # unwrap nested object arrays
            arr = np.ravel(np.asarray(arr[0]).squeeze())

        if arr.size == 0:
            n_empty += 1
            caps.append((i, np.nan))
        else:
            caps.append((i, float(arr[0])))

    return caps, {
        "n_cycle_entries": cycles.shape[1],
        "n_charge": types.get("charge", 0),
        "n_impedance": types.get("impedance", 0),
        "n_discharge_entries": types.get("discharge", 0),
        "n_missing_capacity_field": n_missing_field,
        "n_empty_capacity": n_empty,
        "ambient_temp_C": sorted(ambient),
    }


# ------------------------------------------------------- trajectory analysis --
def find_regime_break(v):
    """Contiguous blocks of collapsed capacity followed by recovery -> (blocks, is_break)."""
    if v.size < 10:
        return [], False
    thr = REGIME_FRAC * float(np.median(v))
    low = v < thr
    blocks, start = [], None
    for i, f in enumerate(low):
        if f and start is None:
            start = i
        elif not f and start is not None:
            blocks.append((start, i - 1))
            start = None
    if start is not None:
        blocks.append((start, len(low) - 1))
    # a break = long collapsed block with normal cycles on BOTH sides
    brk = [b for b in blocks
           if (b[1] - b[0] + 1) >= REGIME_MINLEN and b[0] > 0 and b[1] < len(v) - 1]
    return brk, len(brk) > 0


def confirmed_eol(v, masked):
    """First index with cap<=threshold that is sustained (window median<=threshold)
    and not inside an anomalous (masked) cycle. No interpolation."""
    for k in range(v.size):
        if masked[k] or v[k] > EOL_THRESHOLD:
            continue
        w = v[k:k + CONFIRM_WIN]
        w = w[~masked[k:k + CONFIRM_WIN]]
        if w.size == 0:
            continue
        if float(np.median(w)) <= EOL_THRESHOLD:
            return k
    return None


def assess(battery_id, caps, diag):
    raw_idx = [i for i, _ in caps]
    vals = np.array([v for _, v in caps], dtype=float)

    n_nan = int(np.isnan(vals).sum())
    finite = vals[np.isfinite(vals)]
    n_zero = int((finite == 0).sum())
    n_negative = int((finite < 0).sum())
    n_above_rated = int((finite > RATED).sum())

    valid_mask = np.isfinite(vals) & (vals > 0)
    v = vals[valid_mask]
    v_raw_idx = [r for r, m in zip(raw_idx, valid_mask) if m]
    n_valid = int(v.size)

    amb = diag["ambient_temp_C"]
    row = {"battery_id": battery_id, "valid_cycles": n_valid,
           "first_capacity_Ah": np.nan, "minimum_capacity_Ah": np.nan,
           "reaches_1.4Ah": False, "EOL_cycle": pd.NA,
           "usable_for_RUL": False, "exclusion_reason": ""}
    ex = {"battery_id": battery_id,
          "archive": "", "ambient_temp_C": ";".join(str(int(t)) for t in amb),
          "discharge_cycles_total": diag["n_discharge_entries"],
          "capacity_missing_or_empty": n_nan, "capacity_zero": n_zero,
          "capacity_negative": n_negative, "capacity_above_rated_2Ah": n_above_rated,
          "last_capacity_Ah": np.nan, "max_capacity_Ah": np.nan,
          "capacity_fade_pct": np.nan, "final_SOH_pct_of_rated": np.nan,
          "spearman_rho": np.nan, "frac_increasing_steps": np.nan,
          "EOL_cycle_literal": pd.NA, "EOL_cycle_confirmed": pd.NA,
          "EOL_reliable": False, "first_cycle_artifact": False,
          "regime_break": False, "regime_break_cycles": "",
          "anomaly_notes": ""}

    reasons, notes = [], []

    if n_valid == 0:
        row["exclusion_reason"] = "no valid discharge capacity values could be extracted"
        return row, ex, v

    first_c, last_c = float(v[0]), float(v[-1])
    min_c, max_c = float(v.min()), float(v.max())
    row["first_capacity_Ah"] = round(first_c, 4)
    row["minimum_capacity_Ah"] = round(min_c, 4)
    ex["last_capacity_Ah"] = round(last_c, 4)
    ex["max_capacity_Ah"] = round(max_c, 4)
    ex["capacity_fade_pct"] = round((first_c - last_c) / first_c * 100, 2)
    ex["final_SOH_pct_of_rated"] = round(last_c / RATED * 100, 2)

    # --- anomaly masks -------------------------------------------------------
    masked = np.zeros(n_valid, dtype=bool)

    early_med = float(np.median(v[1:6])) if n_valid >= 6 else (
        float(np.median(v[1:])) if n_valid > 1 else np.nan)
    c1_art = bool(n_valid > 2 and np.isfinite(early_med) and v[0] < 0.9 * early_med)
    if c1_art:
        masked[0] = True
        ex["first_cycle_artifact"] = True
        notes.append(f"cycle 1 is a conditioning/partial discharge "
                     f"({first_c:.3f} Ah vs {early_med:.3f} Ah early plateau) - not true initial capacity")

    blocks, is_break = find_regime_break(v)
    if is_break:
        ex["regime_break"] = True
        ex["regime_break_cycles"] = ";".join(f"{a+1}-{b+1}" for a, b in blocks)
        for a, b in blocks:
            masked[a:b + 1] = True
        tot = sum(b - a + 1 for a, b in blocks)
        notes.append(f"protocol/regime break: {tot} contiguous cycles ({ex['regime_break_cycles']}) "
                     f"collapse to ~0.06-0.1 Ah then recover - not a single degradation trajectory")

    # --- EOL -----------------------------------------------------------------
    below = np.where(v <= EOL_THRESHOLD)[0]
    lit = int(below[0]) if below.size else None
    conf = confirmed_eol(v, masked)

    if lit is not None:
        row["reaches_1.4Ah"] = True
        row["EOL_cycle"] = lit + 1                     # 1-based within discharge sequence
        ex["EOL_cycle_literal"] = lit + 1
    if conf is not None:
        ex["EOL_cycle_confirmed"] = conf + 1
    ex["EOL_reliable"] = (lit is not None and conf is not None
                          and (conf - lit) <= EOL_TOL)

    if lit is not None and not ex["EOL_reliable"]:
        why = ("cycle-1 conditioning artifact" if lit == 0 and c1_art else
               "collapsed-capacity regime block" if masked[lit] else
               "single transient dip, capacity recovers afterwards")
        notes.append(f"first crossing at cycle {lit+1} is spurious ({why}); "
                     f"confirmed sustained crossing = "
                     f"{'none' if conf is None else 'cycle ' + str(conf+1)}")

    # --- trajectory shape (on non-masked cycles) -----------------------------
    vm = v[~masked]
    if vm.size >= 3:
        rho = float(spearmanr(np.arange(vm.size), vm).statistic)
        d = np.diff(vm)
        ex["spearman_rho"] = round(rho, 4)
        ex["frac_increasing_steps"] = round(float((d > 0).mean()), 4)
    else:
        rho = np.nan

    if n_nan:
        notes.append(f"{n_nan} discharge cycle(s) with missing/empty Capacity (dropped)")
    if n_zero:
        notes.append(f"{n_zero} exact-zero capacity value(s) (dropped)")
    if n_negative:
        notes.append(f"{n_negative} negative capacity value(s)")
    if n_above_rated:
        notes.append(f"{n_above_rated} value(s) above rated {RATED} Ah (max {max_c:.3f})")
    if len(amb) > 1:
        notes.append(f"mixed ambient temperature within file ({ex['ambient_temp_C']} C)")
    if amb and max(amb) <= COLD_C and max_c <= EOL_THRESHOLD:
        notes.append(f"cold test ({ex['ambient_temp_C']} C): capacity never exceeds "
                     f"{EOL_THRESHOLD} Ah even when fresh - threshold not physically applicable")
    if np.isfinite(rho) and rho > -0.5:
        notes.append(f"weak/absent monotonic decay (Spearman rho={rho:.2f})")
    ex["anomaly_notes"] = "; ".join(notes)

    # --- exclusion decision --------------------------------------------------
    if n_valid < MIN_CYCLES:
        reasons.append(f"only {n_valid} valid discharge cycles (< {MIN_CYCLES}) - trajectory too short")
    if max_c <= EOL_THRESHOLD:
        reasons.append(f"never healthy: max capacity {max_c:.3f} Ah is at/below the 1.4 Ah EOL "
                       f"threshold from cycle 1 ({ex['ambient_temp_C']} C test) - no degradation "
                       f"span to model")
    elif conf is None:
        reasons.append(f"no valid EOL: never reaches a sustained 1.4 Ah crossing "
                       f"(min {min_c:.3f} Ah, final SOH {ex['final_SOH_pct_of_rated']:.1f}% of rated)")
    elif not ex["EOL_reliable"]:
        reasons.append(f"reported EOL (cycle {lit+1}) is spurious - it is an artifact cycle, not "
                       f"real degradation; sustained crossing is cycle {conf+1}. Usable only after "
                       f"removing the flagged artifact cycles")
    if is_break:
        reasons.append("capacity trajectory contains a protocol/regime break "
                       f"(cycles {ex['regime_break_cycles']}) - not a single continuous degradation curve")
    if np.isfinite(rho) and rho > -0.5:
        reasons.append(f"abnormal/non-monotonic capacity trajectory (Spearman rho={rho:.2f})")
    if n_negative:
        reasons.append("contains negative capacity values")

    row["usable_for_RUL"] = len(reasons) == 0
    row["exclusion_reason"] = "" if row["usable_for_RUL"] else "; ".join(reasons)
    return row, ex, v


# -------------------------------------------------------------------- main --
def main():
    os.makedirs(OUTDIR, exist_ok=True)
    inv = discover_files()
    inv.to_csv(os.path.join(OUTDIR, "file_inventory.csv"), index=False)

    print("=" * 100)
    print("1. FILE INVENTORY & DUPLICATE DETECTION")
    print("=" * 100)
    print(f"  .mat files found      : {len(inv)}")
    print(f"  unique battery IDs    : {inv['battery_id'].nunique()}")
    print(f"  unique file contents  : {inv['md5'].nunique()}")
    dups = inv[inv.duplicated("md5", keep=False)]
    print("\n  DUPLICATE BATTERY FILES (byte-identical, same ID in two archives):")
    for md5, g in dups.groupby("md5"):
        print(f"    {g['battery_id'].iloc[0]}  md5={md5[:10]}  ->  " + "  |  ".join(g["archive"]))
    print("    -> second copy dropped; analysis runs on unique files only")

    uniq = inv[~inv["is_duplicate_copy"]].sort_values("battery_id")
    rows, extras, traj = [], [], {}
    for _, r in uniq.iterrows():
        caps, diag = extract_discharge_capacities(r["path"], r["battery_id"])
        row, ex, v = assess(r["battery_id"], caps, diag)
        ex["archive"] = r["archive"]
        rows.append(row); extras.append(ex); traj[r["battery_id"]] = v

    final = pd.DataFrame(rows)[["battery_id", "valid_cycles", "first_capacity_Ah",
                                "minimum_capacity_Ah", "reaches_1.4Ah", "EOL_cycle",
                                "usable_for_RUL", "exclusion_reason"]]
    diagnostics = pd.DataFrame(extras)

    final.to_csv(os.path.join(OUTDIR, "battery_eol_assessment.csv"), index=False)
    diagnostics.to_csv(os.path.join(OUTDIR, "battery_diagnostics.csv"), index=False)
    pd.DataFrame([{"battery_id": b, "discharge_cycle": i, "capacity_Ah": c}
                  for b, v in traj.items() for i, c in enumerate(v, 1)]
                 ).to_csv(os.path.join(OUTDIR, "capacity_series_long.csv"), index=False)

    pd.set_option("display.width", 250)
    pd.set_option("display.max_colwidth", 95)

    print("\n" + "=" * 100)
    print("2. FINAL TABLE  (battery_eol_assessment.csv)")
    print("=" * 100)
    print(final.to_string(index=False))

    print("\n" + "=" * 100)
    print("3. EXPLICIT FLAG GROUPS")
    print("=" * 100)
    never = final[~final["reaches_1.4Ah"]]["battery_id"].tolist()
    no_eol = diagnostics[diagnostics["EOL_cycle_confirmed"].isna()]["battery_id"].tolist()
    unreliable = diagnostics[(~diagnostics["EOL_reliable"]) &
                             (diagnostics["EOL_cycle_literal"].notna())]["battery_id"].tolist()
    abnormal = diagnostics[(diagnostics["spearman_rho"] > -0.5) |
                           (diagnostics["regime_break"]) |
                           (diagnostics["first_cycle_artifact"])]["battery_id"].tolist()
    usable = final[final["usable_for_RUL"]]["battery_id"].tolist()

    print(f"  duplicate battery files            : {sorted(dups['battery_id'].unique())}")
    print(f"  never reach 1.4 Ah at all          : {never}")
    print(f"  no valid (sustained) EOL           : {no_eol}")
    print(f"  EOL present but SPURIOUS/unreliable: {unreliable}")
    print(f"  abnormal / non-monotonic trajectory: {abnormal}")
    print(f"  USABLE FOR RUL                     : {usable}")

    print("\n" + "=" * 100)
    print("4. ANOMALY DETAIL")
    print("=" * 100)
    for _, a in diagnostics.iterrows():
        if a["anomaly_notes"]:
            print(f"  {a['battery_id']} [{a['ambient_temp_C']} C]: {a['anomaly_notes']}")

    print(f"\nCSV outputs -> {os.path.abspath(OUTDIR)}")


if __name__ == "__main__":
    main()
