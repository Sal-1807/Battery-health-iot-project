"""
Feature engineering pipeline for SoH estimation and RUL prediction.

Input : battery_data.csv  (+ raw .mat for three channel stats it does not carry)
Output: battery_health_features.csv

NO model training.

Leakage policy
--------------
Capacity and Discharge_time are used ONLY to build the labels (SoH, RUL).
Neither is written to the feature columns: both require a complete controlled
discharge, which an IoT monitor never observes, and Discharge_time is ~0.98
correlated with Capacity.

Note on the source
------------------
battery_data.csv stores per-cycle MEANS only, so Current_std, Temperature_max
and Temperature_rise cannot be computed from it. They are re-derived from the
raw Current_measured / Temperature_measured arrays in the .mat files and merged
on (Battery_ID, Cycle). Voltage_range is derived from the CSV directly.
"""

import glob
import os
import zipfile

import numpy as np
import pandas as pd
import scipy.io

BATTERIES = ["B0005", "B0006", "B0018"]
EOL_THRESHOLD = 1.4                     # Ah - 70% of the 2.0 Ah rating
RATED_CAPACITY = 2.0                    # Ah - nameplate, used as the SoH reference

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repo root (src/ -> ..)
RAW_DIR = os.path.join(ROOT, "data", "raw")          # git-ignored: NASA zips + unpacked .mat
PROCESSED_DIR = os.path.join(ROOT, "data", "processed")
IN_CSV = os.path.join(PROCESSED_DIR, "battery_data.csv")
OUT_CSV = os.path.join(PROCESSED_DIR, "battery_health_features.csv")
WORK = os.path.join(RAW_DIR, "_mat_extracted")

FEATURES = ["Voltage_mean", "Voltage_min", "Voltage_max", "Voltage_range",
            "Current_mean", "Current_std", "Temperature_mean", "Temperature_max",
            "Temperature_rise", "Cycle_number"]


# --------------------------------------------------------------------- input
def locate_mat_files():
    def found():
        hits = {}
        for f in glob.glob(os.path.join(RAW_DIR, "**", "*.mat"), recursive=True):
            name = os.path.splitext(os.path.basename(f))[0]
            if name in BATTERIES and name not in hits:
                hits[name] = f
        return hits

    hits = found()
    if all(b in hits for b in BATTERIES):
        return hits
    for z in glob.glob(os.path.join(RAW_DIR, "**", "*.zip"), recursive=True):
        with zipfile.ZipFile(z) as zf:
            zf.extractall(os.path.join(WORK, os.path.splitext(os.path.basename(z))[0]))
    hits = found()
    missing = [b for b in BATTERIES if b not in hits]
    if missing:
        raise FileNotFoundError(f"could not find .mat files for: {missing}")
    return hits


def unwrap(field):
    arr = np.asarray(field).squeeze()
    while arr.dtype == object and arr.size > 0:
        arr = np.asarray(arr.ravel()[0]).squeeze()
    return np.atleast_1d(arr).astype(float)


def channel_stats(battery_id, path):
    """Per-discharge-cycle stats that battery_data.csv does not store."""
    mat = scipy.io.loadmat(path)
    key = battery_id if battery_id in mat else [k for k in mat if not k.startswith("__")][0]
    cycles = mat[key]["cycle"][0, 0]

    rows, n = [], 0
    for i in range(cycles.shape[1]):
        entry = cycles[0, i]
        if str(entry["type"][0]).strip().lower() != "discharge":
            continue
        n += 1
        data = entry["data"]
        names = data.dtype.names or ()
        cur = unwrap(data["Current_measured"]) if "Current_measured" in names else np.array([np.nan])
        tmp = unwrap(data["Temperature_measured"]) if "Temperature_measured" in names else np.array([np.nan])
        rows.append({
            "Battery_ID": battery_id,
            "Cycle": n,
            "Current_std": float(np.nanstd(cur)) if cur.size else np.nan,
            "Temperature_max": float(np.nanmax(tmp)) if tmp.size else np.nan,
            # rise across the discharge: peak minus temperature at cycle start
            "Temperature_rise": float(np.nanmax(tmp) - tmp[0]) if tmp.size else np.nan,
        })
    return rows


# -------------------------------------------------------------------- labels
def add_soh(df):
    """SoH = (capacity at cycle / RATED capacity 2.0 Ah) x 100.

    Referenced to the nameplate rating, not each cell's first cycle, so the SoH
    scale is comparable across cells and the 1.4 Ah EOL maps to exactly 70% SoH
    for every battery. A cell whose first discharge exceeds 2.0 Ah therefore
    starts above 100%, which is reported as measured and not clipped.
    """
    df["SoH"] = df["Capacity"] / RATED_CAPACITY * 100.0
    return df


def add_rul(df):
    """RUL = EOL cycle - current cycle, floored at 0.

    EOL detection is unchanged: the first cycle whose capacity is <= 1.4 Ah.
    Post-EOL rows are KEPT (not dropped) and their negative RUL is clipped to 0,
    so RUL means 'cycles remaining until EOL, 0 once EOL is reached'. Reached_EOL
    preserves the before/after distinction that clipping collapses.
    Batteries that never reach EOL keep RUL = NaN - no synthetic EOL is invented.
    """
    eol = {}
    for b, g in df.groupby("Battery_ID"):
        g = g.sort_values("Cycle")
        hit = g.loc[g["Capacity"] <= EOL_THRESHOLD, "Cycle"]
        eol[b] = int(hit.iloc[0]) if len(hit) else np.nan

    df["EOL_cycle"] = df["Battery_ID"].map(eol)
    raw = df["EOL_cycle"] - df["Cycle"]            # NaN propagates where no EOL
    df["RUL_raw"] = raw                            # kept for the audit print only
    df["RUL"] = raw.clip(lower=0)

    # 1 at or after the EOL cycle, 0 before it; 0 where no EOL was ever reached
    df["Reached_EOL"] = np.where(df["EOL_cycle"].notna() & (df["Cycle"] >= df["EOL_cycle"]),
                                 1, 0).astype(int)
    return df, eol


# ---------------------------------------------------------------------- main
def main():
    df = pd.read_csv(IN_CSV)
    print(f"loaded {IN_CSV}: {df.shape[0]} rows x {df.shape[1]} cols")
    print(f"  columns: {list(df.columns)}")

    # --- augment with channel stats the CSV cannot provide -------------------
    paths = locate_mat_files()
    extra = pd.DataFrame([r for b in BATTERIES for r in channel_stats(b, paths[b])])
    before = len(df)
    df = df.merge(extra, on=["Battery_ID", "Cycle"], how="left", validate="one_to_one")
    assert len(df) == before, "merge changed the row count"
    print(f"merged Current_std / Temperature_max / Temperature_rise "
          f"({extra.shape[0]} rows, unmatched: {int(df['Current_std'].isna().sum())})")

    # --- derived feature ----------------------------------------------------
    df["Voltage_range"] = df["Voltage_max"] - df["Voltage_min"]
    df["Cycle_number"] = df["Cycle"]

    # --- labels -------------------------------------------------------------
    df = add_soh(df)
    df, eol = add_rul(df)

    print("\nEOL (first cycle with Capacity <= 1.4 Ah):")
    for b in BATTERIES:
        v = eol[b]
        print(f"  {b}: {'never reaches EOL -> RUL = NaN' if pd.isna(v) else f'cycle {int(v)}'}")

    n_clipped = int((df["RUL_raw"] < 0).sum())
    print(f"\nRUL: {n_clipped} post-EOL rows had negative RUL -> clipped to 0 (rows kept)")

    out = df[["Battery_ID", "Cycle"] + FEATURES + ["SoH", "RUL", "Reached_EOL"]].copy()
    num = out.select_dtypes("number").columns.drop(["Cycle", "Cycle_number", "Reached_EOL"])
    out[num] = out[num].round(6)
    os.makedirs(PROCESSED_DIR, exist_ok=True)
    out.to_csv(OUT_CSV, index=False)
    print(f"\nwrote {OUT_CSV}  ({out.shape[0]} rows x {out.shape[1]} cols)")

    # ------------------------------------------------------------- verify
    pd.set_option("display.width", 250, "display.max_columns", 30)
    chk = pd.read_csv(OUT_CSV)

    print("\n" + "=" * 100)
    print("1. FIRST 5 ROWS")
    print("=" * 100)
    print(chk.head(5).to_string(index=False))

    print("\n" + "=" * 100)
    print("2. SHAPE")
    print("=" * 100)
    print(f"shape: {chk.shape}   ({chk.shape[0]} rows, {chk.shape[1]} columns)")
    print(f"features: {len(FEATURES)}   labels: 2 (SoH, RUL)   identifiers: 2")
    print(f"\nrows per battery:\n{chk.groupby('Battery_ID').size().to_string()}")

    print("\n" + "=" * 100)
    print("3. MISSING VALUES")
    print("=" * 100)
    miss = pd.DataFrame({"missing": chk.isna().sum(), "dtype": chk.dtypes.astype(str)})
    print(miss.to_string())
    print(f"\ntotal missing cells: {int(chk.isna().sum().sum())}")

    print("\n" + "=" * 100)
    print("4. NEGATIVE-RUL CHECK")
    print("=" * 100)
    n_neg = int((chk["RUL"] < 0).sum())
    print(f"  rows with RUL < 0 : {n_neg}   {'PASS' if n_neg == 0 else 'FAIL'}")
    print(f"  RUL min / max     : {chk['RUL'].min():.0f} / {chk['RUL'].max():.0f}")
    print(f"  rows with RUL = 0 : {int((chk['RUL'] == 0).sum())}")
    print(f"  Reached_EOL counts: {chk['Reached_EOL'].value_counts().sort_index().to_dict()}")
    print("  cross-check (RUL==0) vs (Reached_EOL==1): "
          f"{'consistent' if ((chk['RUL'] == 0) == (chk['Reached_EOL'] == 1)).all() else 'MISMATCH'}")

    print("\n" + "=" * 100)
    print("5. RUL DISTRIBUTION")
    print("=" * 100)
    print(chk.groupby("Battery_ID")["RUL"].describe().to_string())
    print("\nbinned (all batteries):")
    bins = [-0.1, 0.1, 20, 40, 60, 80, 100, 130]
    labels = ["0 (at/after EOL)", "1-20", "21-40", "41-60", "61-80", "81-100", "101-124"]
    print(pd.cut(chk["RUL"], bins=bins, labels=labels).value_counts()
            .reindex(labels).to_frame("rows").to_string())
    print("\nper battery, before vs at/after EOL:")
    print(chk.groupby(["Battery_ID", "Reached_EOL"]).size()
             .unstack(fill_value=0)
             .rename(columns={0: "before EOL", 1: "at/after EOL"}).to_string())

    print("\n" + "=" * 100)
    print("6. FINAL SoH PER BATTERY  (SoH = Capacity / 2.0 Ah x 100)")
    print("=" * 100)
    for b, g in chk.groupby("Battery_ID"):
        g = g.sort_values("Cycle")
        at_eol = g.loc[g["Reached_EOL"] == 1, "SoH"]
        print(f"  {b}: first {g['SoH'].iloc[0]:6.2f}%   final {g['SoH'].iloc[-1]:6.2f}%   "
              f"min {g['SoH'].min():6.2f}%   SoH at EOL cycle: "
              f"{at_eol.iloc[0]:.2f}%" if len(at_eol) else "no EOL")
    print("\nleakage check - Capacity / Discharge_time present in output? "
          f"{[c for c in ('Capacity', 'Discharge_time') if c in chk.columns] or 'NO (correct)'}")

    print("\n" + "=" * 100)
    print("5. FEATURE RANGES")
    print("=" * 100)
    print(chk[FEATURES].describe().T[["min", "max", "mean", "std"]].to_string())


if __name__ == "__main__":
    main()
