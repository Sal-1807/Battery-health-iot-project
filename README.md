# Battery Health IoT Project

Data pipeline for an IoT-based battery health monitoring system: **State of Health (SoH)
estimation** and **Remaining Useful Life (RUL) prediction**, built on the NASA Li-ion
Battery Aging Dataset.

> **Status: data engineering only — no models trained yet.**
> This repository covers dataset assessment, extraction and feature engineering.
> Model training is deliberately out of scope so far.

---

## Project structure

```
.
├── data/
│   ├── raw/                         NASA dataset (git-ignored — see Setup)
│   └── processed/
│       ├── battery_data.csv             per-cycle discharge summary (468 rows)
│       └── battery_health_features.csv  final feature set + SoH/RUL labels
├── src/
│   ├── analyze_batteries.py         dataset-wide quality / EOL assessment (34 batteries)
│   ├── make_plots.py                capacity-vs-cycle plots for every battery
│   ├── extract_battery_data.py      .mat -> battery_data.csv
│   ├── build_features.py            battery_data.csv -> battery_health_features.csv
│   └── plot_soh.py                  SoH degradation curves
├── results/
│   ├── battery_eol_assessment.csv   per-battery usability verdict
│   ├── battery_diagnostics.csv      anomaly flags, EOL reliability, ambient temp
│   ├── capacity_series_long.csv     per-cycle capacity audit trail
│   ├── file_inventory.csv           file hashes + duplicate detection
│   └── plots/
│       └── soh_degradation.png      SoH curves for B0005 / B0006 / B0018
├── docs/
│   └── dataset_assessment.md        which batteries are usable, and why
├── requirements.txt
└── README.md
```

---

## Setup

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

**The raw dataset is not committed** (it is large and redistributable from NASA).
Download the *Battery Data Set* from the
[NASA Prognostics Center of Excellence data repository](https://www.nasa.gov/intelligent-systems-division/discovery-and-systems-health/pcoe/pcoe-data-set-repository/)
and drop the `.zip` archives (or the unpacked `.mat` files) anywhere under:

```
data/raw/
```

The scripts locate the files themselves and unpack the archives on first run.

---

## Pipeline

Run from the repository root, in order:

```bash
python src/extract_battery_data.py    # .mat  -> data/processed/battery_data.csv
python src/build_features.py          # + labels -> battery_health_features.csv
python src/plot_soh.py                # -> results/plots/soh_degradation.png
```

Optional dataset-wide assessment (all 34 batteries, not just the three modelled):

```bash
python src/analyze_batteries.py       # -> results/*.csv
python src/make_plots.py              # -> results/plots/*.png (git-ignored)
```

`make_plots.py` writes a capacity-vs-cycle plot for each of the 34 batteries plus two
overviews. Those are **not committed** — they are reproducible from the CSVs in
`results/`, so only `soh_degradation.png` is tracked.

Every script is idempotent and prints its own verification block.

---

## Dataset

The NASA archive ships **38 `.mat` files but only 34 unique batteries** — B0025–B0028
appear twice, byte-identical (verified by MD5). Each file holds one MATLAB struct whose
`cycle` array interleaves three step types (`charge`, `discharge`, `impedance`) with
*different* data fields, so discharge cycles must be filtered by `type`, not by index.

Only **B0005, B0006 and B0018** are used for modelling. They are the batteries that run
at 24 °C, degrade monotonically, and reach the 1.4 Ah end-of-life threshold with a clean,
continuous trajectory. See [`docs/dataset_assessment.md`](docs/dataset_assessment.md) for
the full per-battery verdict and the reasons the other 31 were excluded.

---

## Labels

**SoH** is referenced to the **rated** capacity, not each cell's first cycle:

```
SoH = (Capacity / 2.0 Ah) * 100
```

This keeps the SoH scale comparable across cells and makes the 1.4 Ah EOL land at exactly
70% SoH for every battery. (Normalising by first-cycle capacity instead spreads the same
EOL across 68.5–75.3% SoH, so a single "alarm below 70%" rule would misfire.)
Values above 100% are reported as measured — B0006's first discharge genuinely exceeds
the 2.0 Ah nameplate.

**RUL** counts cycles until the first discharge at or below 1.4 Ah:

```
RUL = max(EOL_cycle - current_cycle, 0)
```

Post-EOL rows are kept with `RUL = 0` and flagged by `Reached_EOL = 1`. Batteries that
never reach the threshold get `RUL = NaN` — no EOL is ever extrapolated or invented.

| Battery | EOL cycle | Cycles | Final SoH |
|---------|-----------|--------|-----------|
| B0005   | 125       | 168    | 66.25%    |
| B0006   | 109       | 168    | 59.28%    |
| B0018   | 97        | 132    | 67.05%    |

---

## Features

`battery_health_features.csv` — 468 rows × 15 columns, no missing values.

| Column | Description |
|---|---|
| `Battery_ID`, `Cycle` | identifiers (cycle = 1-based discharge index, original order) |
| `Voltage_mean` / `_min` / `_max` / `_range` | terminal-voltage statistics per cycle |
| `Current_mean`, `Current_std` | load-current statistics |
| `Temperature_mean` / `_max` / `_rise` | cell temperature; rise = peak − start |
| `Cycle_number` | cycle index as a feature |
| `SoH`, `RUL`, `Reached_EOL` | labels |

### Leakage policy

`Capacity` and `Discharge_time` are used **only to construct labels** and are deliberately
excluded from the feature set:

- `Capacity` is the target that SoH is derived from.
- `Discharge_time` correlates with capacity at **r = 0.98**, and both require a complete
  controlled discharge — something a deployed IoT monitor never observes.

---

## Known caveats

- **Zero-inflated RUL.** Clipping puts 140 of 468 rows (30%) at `RUL = 0`. Consider
  classifying `Reached_EOL` first, then regressing RUL on the pre-EOL rows only.
- **`Voltage_max` is nearly constant** (σ = 0.008 V) and carries little information.
- **Capacity regeneration** makes SoH non-monotonic cycle-to-cycle; the recovery spikes
  are real electrochemistry, not noise.
- **Three cells is a small cohort.** B0046–B0048 could extend it, but they run at 4 °C
  and cross EOL within 10–17 cycles, so they should be treated as a separate cohort
  rather than pooled.

---

## Data provenance

NASA Ames Prognostics Center of Excellence, *Battery Data Set*, PCoE Datasets.
Cells: 18650 Li-ion, 2.0 Ah rated. Please cite NASA PCoE if you use this data.
