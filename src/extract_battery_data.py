"""
Extract cycle-level discharge features from the NASA Li-ion battery .mat files.

Batteries: B0005, B0006, B0018  -> battery_data.csv

NO model training. One row per discharge cycle, original cycle order preserved.

.mat layout (verified, not assumed):
    mat['B0005']                      ndarray (1,1), field 'cycle'
      └ ['cycle'][0,0]                struct array (1, N) - every test step
          ├ 'type'                    'charge' | 'discharge' | 'impedance'
          ├ 'ambient_temperature'     scalar, degC
          ├ 'time'                    6-elem test start stamp
          │                           [year, month, day, hour, minute, second]
          └ 'data'                    per-type measurement struct; for 'discharge':
                Voltage_measured      (n,) terminal voltage, V
                Current_measured      (n,) current, A (negative = discharge)
                Temperature_measured  (n,) cell temperature, degC
                Current_load          (n,) load current, A
                Voltage_load          (n,) load voltage, V
                Time                  (n,) elapsed seconds within the cycle
                Capacity              (1,) discharge capacity for the cycle, Ah
"""

import glob
import os
import zipfile

import numpy as np
import pandas as pd
import scipy.io

BATTERIES = ["B0005", "B0006", "B0018"]
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repo root (src/ -> ..)
RAW_DIR = os.path.join(ROOT, "data", "raw")          # git-ignored: NASA zips + unpacked .mat
PROCESSED_DIR = os.path.join(ROOT, "data", "processed")
OUT_CSV = os.path.join(PROCESSED_DIR, "battery_data.csv")
WORK = os.path.join(RAW_DIR, "_mat_extracted")       # zips are unpacked here if needed


# --------------------------------------------------------------------- input
def locate_mat_files():
    """Find the .mat files, unpacking the dataset zips on first run."""
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
        dest = os.path.join(WORK, os.path.splitext(os.path.basename(z))[0])
        with zipfile.ZipFile(z) as zf:
            zf.extractall(dest)
    hits = found()

    missing = [b for b in BATTERIES if b not in hits]
    if missing:
        raise FileNotFoundError(f"could not find .mat files for: {missing}")
    return hits


def unwrap(field):
    """MATLAB struct fields arrive wrapped in nested object arrays - unwrap to 1-D."""
    arr = np.asarray(field).squeeze()
    while arr.dtype == object and arr.size > 0:
        arr = np.asarray(arr.ravel()[0]).squeeze()
    return np.atleast_1d(arr).astype(float)


# ---------------------------------------------------------------- extraction
def extract_battery(battery_id, path):
    mat = scipy.io.loadmat(path)
    key = battery_id if battery_id in mat else [k for k in mat if not k.startswith("__")][0]
    cycles = mat[key]["cycle"][0, 0]

    rows = []
    cycle_no = 0
    for i in range(cycles.shape[1]):
        entry = cycles[0, i]
        if str(entry["type"][0]).strip().lower() != "discharge":
            continue

        cycle_no += 1                      # 1-based index over discharge cycles only
        data = entry["data"]
        names = data.dtype.names or ()

        def col(name):
            return unwrap(data[name]) if name in names else np.array([np.nan])

        v = col("Voltage_measured")
        c = col("Current_measured")
        t = col("Temperature_measured")
        tm = col("Time")
        cap = col("Capacity")

        # Capacity is one scalar per discharge cycle; NaN if absent/empty
        capacity = float(cap[0]) if cap.size and np.isfinite(cap[0]) else np.nan
        # elapsed seconds within this cycle (Time starts at 0)
        dis_time = float(np.nanmax(tm) - np.nanmin(tm)) if tm.size else np.nan

        rows.append({
            "Battery_ID": battery_id,
            "Cycle": cycle_no,
            "Capacity": capacity,
            "Voltage_mean": float(np.nanmean(v)) if v.size else np.nan,
            "Voltage_min": float(np.nanmin(v)) if v.size else np.nan,
            "Voltage_max": float(np.nanmax(v)) if v.size else np.nan,
            "Current_mean": float(np.nanmean(c)) if c.size else np.nan,
            "Temperature_mean": float(np.nanmean(t)) if t.size else np.nan,
            "Discharge_time": dis_time,
        })

    return rows


def main():
    paths = locate_mat_files()
    all_rows = []
    for b in BATTERIES:
        rows = extract_battery(b, paths[b])
        print(f"{b}: {len(rows)} discharge cycles extracted  <- {paths[b]}")
        all_rows.extend(rows)

    df = pd.DataFrame(all_rows, columns=[
        "Battery_ID", "Cycle", "Capacity", "Voltage_mean", "Voltage_min",
        "Voltage_max", "Current_mean", "Temperature_mean", "Discharge_time"])

    num = df.select_dtypes("number").columns.drop("Cycle")
    df[num] = df[num].round(6)
    os.makedirs(PROCESSED_DIR, exist_ok=True)
    df.to_csv(OUT_CSV, index=False)
    print(f"\nwrote {OUT_CSV}  ({len(df)} rows x {len(df.columns)} columns)")

    # ------------------------------------------------------------- verify
    pd.set_option("display.width", 200, "display.max_columns", 20)
    chk = pd.read_csv(OUT_CSV)

    print("\n" + "=" * 78)
    print("1. FIRST 10 ROWS")
    print("=" * 78)
    print(chk.head(10).to_string(index=False))

    print("\n" + "=" * 78)
    print("2. CYCLES PER BATTERY")
    print("=" * 78)
    per = (chk.groupby("Battery_ID")
              .agg(cycles=("Cycle", "count"),
                   cycle_min=("Cycle", "min"), cycle_max=("Cycle", "max"),
                   capacity_first=("Capacity", "first"), capacity_last=("Capacity", "last"))
              .reset_index())
    print(per.to_string(index=False))
    print(f"\ntotal rows: {len(chk)}")

    print("\n" + "=" * 78)
    print("3. MISSING VALUES")
    print("=" * 78)
    miss = pd.DataFrame({
        "missing (NaN)": chk.isna().sum(),
        "empty/blank": (chk.astype(str).apply(lambda s: s.str.strip() == "")).sum(),
        "dtype": chk.dtypes.astype(str),
    })
    print(miss.to_string())
    print(f"\ntotal missing cells: {int(chk.isna().sum().sum())}")
    print(f"duplicate (Battery_ID, Cycle) pairs: {int(chk.duplicated(['Battery_ID','Cycle']).sum())}")

    print("\n" + "=" * 78)
    print("4. VALUE RANGES (sanity)")
    print("=" * 78)
    print(chk.describe().T[["min", "max", "mean"]].to_string())


if __name__ == "__main__":
    main()
