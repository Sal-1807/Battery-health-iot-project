# Battery Health Monitoring with RUL Prediction (IoT + ML)

ESP32 telemetry (INA219 voltage/current, DS18B20 temperature) → FastAPI + SQLite →
cycle aggregation → NASA-trained State-of-Health (SoH) / Remaining-Useful-Life (RUL)
models → React dashboard.

> **Status (2026-11):** sensors bench-tested by the team; backend, firmware and dashboard
> implemented and tested in software (see [docs/PROJECT_STATUS.md](docs/PROJECT_STATUS.md)).
> **No real battery discharge has been recorded yet**, and the ML models are validated only
> on NASA cells. On live data, SoH/RUL are shown as *experimental* or *unavailable*, never
> as validated results.

Documentation: [Project status](docs/PROJECT_STATUS.md) · [Architecture](docs/ARCHITECTURE.md) ·
[Hardware setup](docs/HARDWARE_SETUP.md) · [API reference](docs/API_REFERENCE.md) ·
[Dataset assessment](docs/dataset_assessment.md)

---

## Architecture

```
ESP32 (INA219 + DS18B20) ──HTTP JSON──▶ FastAPI (iot/backend) ──▶ SQLite
simulator (iot/simulator) ─────────────▶   │  validate · store · alert
                                            │  readings → discharge segments → features
                                            │  gated inference with models/*.pkl
                                            ▼
                              React dashboard (dashboard/)  ◀── polling /api/*

offline: NASA .mat → src/extract_battery_data.py → src/build_features.py → src/train_models.py → models/*.pkl
```

Details and the data model: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Repository structure

```
.
├── src/                    offline data pipeline + training (NASA)
├── data/processed/         battery_data.csv, battery_health_features.csv (468 cycles)
├── models/                 metadata.json (committed) + *.pkl (git-ignored, regenerate)
├── results/                LOBO metrics/predictions, analyses, experiments
├── iot/
│   ├── backend/            FastAPI app (api.py, telemetry.py, cycles.py, features.py,
│   │                       model_service.py, alerts.py, nasa.py, db.py, config.py)
│   ├── simulator/          simulate_esp32.py (synthetic telemetry / NASA replay)
│   ├── firmware/esp32_battery_monitor/   Arduino sketch + config.example.h
│   ├── tests/              pytest suite (45 tests)
│   └── data/               runtime SQLite database (git-ignored)
├── dashboard/              React + Vite + TypeScript + Tailwind + Recharts
└── docs/
```

## Hardware

ESP32 DevKit V1, INA219 (I²C: SDA GPIO21, SCL GPIO22, 3V3, GND), DS18B20 (DATA GPIO4 with
4.7 kΩ pull-up to 3V3), 3.7 V 3000 mAh 18650 cell, 10 Ω 5 W resistor. Wiring, firmware
upload, network setup and the device checks still to be done are in
[docs/HARDWARE_SETUP.md](docs/HARDWARE_SETUP.md).

> **Safety.** The firmware and backend only *measure* and *alert*. They do not cut off a
> load or control charging. The cell's protection status is unknown, and no discharge setup has
> been validated. Read HARDWARE_SETUP.md §5 before connecting the cell to any load.

---

## Quick start

All commands run from the repository root unless noted.

### 1. Python environment and models

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python src/train_models.py       # regenerates models/*.pkl (git-ignored)
```

`train_models.py` is deterministic (`random_state=42`). It also rewrites
`results/lobo_*.csv` and `results/training_metrics.csv`; on the same library versions they
come out byte-identical (verified), so `git status` should stay clean.

### 2. Backend

```bash
uvicorn iot.backend.main:app --host 0.0.0.0 --port 8000
```

`--host 0.0.0.0` lets the ESP32 reach it over the LAN. Check `http://localhost:8000/api/health`
and the interactive docs at `http://localhost:8000/docs`. The database is created at
`iot/data/telemetry.db`; override with `BHM_DB_PATH`. Other settings are listed in `iot/backend/config.py`.

### 3. Dashboard

```bash
cd dashboard
npm install
npm run dev          # http://localhost:5173  (proxies /api to localhost:8000)
```

Production build: `npm run build && npm run preview` (http://localhost:4173). If the backend runs
elsewhere, set `BHM_API_URL=http://<host>:8000` before `npm run dev` / `preview`.

### 4. ESP32

Copy `iot/firmware/esp32_battery_monitor/config.example.h` to `config.h`, set Wi-Fi and
`BACKEND_BASE_URL=http://<PC LAN IP>:8000`, then upload with the Arduino IDE (board
"ESP32 Dev Module"; libraries Adafruit INA219, OneWire, DallasTemperature).
Full steps: [docs/HARDWARE_SETUP.md](docs/HARDWARE_SETUP.md) §3–4.

### 5. Demo without hardware (clearly labelled)

```bash
# real NASA cycle features replayed through the model (battery "NASA-B0005")
python -m iot.simulator.simulate_esp32 nasa-replay --battery B0005

# synthetic ESP32 telemetry in real time (battery "SIM-18650-01", source=simulation)
python -m iot.simulator.simulate_esp32 telemetry --interval 1 --speedup 300

# or a whole synthetic discharge, back-filled instantly, with a sensor-fault injection
python -m iot.simulator.simulate_esp32 telemetry --backfill --interval 5 --speedup 20 --fault temp-disconnect
```

Simulated and replayed batteries carry a permanent banner and "Simulated"/"NASA replay"
labels in the dashboard. They can never be stored under a hardware battery ID.

## End-to-end demonstration

1. Start the backend (step 2) and dashboard (step 3).
2. Run `nasa-replay` → **Battery health** shows the model on B0005, marked *in-sample*, and below it the
   held-out (leave-one-battery-out) comparison with real accuracy metrics.
3. Run the real-time `telemetry` simulator → **Overview** and **Live** update every 2–3 s. When the
   simulated discharge ends, **History → Discharge cycles** lists the aggregated cycle. **Battery
   health** explains why no SoH is given: the cycle count is unknown and 9 of 10 features are
   outside the NASA training range.
4. **Alerts**: low-voltage and sensor-fault alerts from the simulator; edit thresholds there.
5. Stop the simulator → after 30 s the device turns *stale* and a *device offline* alert appears.
6. With hardware: flash the ESP32 → **System** shows the device, its sensor status and origin "Hardware".

## Tests

```bash
pytest iot/tests -q                 # backend, ML integration, simulator → API → SQLite
cd dashboard && npm test            # dashboard render tests (vitest + jsdom)
cd dashboard && npm run build       # typecheck + production build
```

The model tests skip (rather than fail) if `models/*.pkl` have not been generated.

## Machine-learning summary

- Training data: NASA PCoE B0005, B0006, B0018 (468 discharge cycles, 2.0 Ah cells, 24 °C, ~2 A CC).
- Features (training order): `Voltage_mean, Voltage_min, Voltage_max, Voltage_range, Current_mean,
  Current_std, Temperature_mean, Temperature_max, Temperature_rise, Cycle_number`.
  `Capacity` and `Discharge_time` are excluded (leakage).
- Models: RandomForest SoH regressor; two-stage RUL (EOL classifier → RUL regressor on pre-EOL cycles).
- Held-out LOBO results (`results/lobo_cv_results.csv`):

  | Held-out | SoH RMSE | SoH R² | EOL recall | RUL RMSE |
  |---|---|---|---|---|
  | B0005 | 3.35 pp | 0.876 | 1.00 | 14.9 cycles |
  | B0006 | 4.82 pp | 0.853 | 1.00 | 13.4 cycles |
  | B0018 | 5.16 pp | 0.552 | 0.00 | 27.2 cycles |

- **Limitations:**
  - three cells only, and B0018 shows the model can fail on a new cell;
  - SoH/EOL defined on the 2.0 Ah scale;
  - `Cycle_number` is an input;
  - live data from a 10 Ω load is far outside the training range;
  - nothing has been validated on the team's 3.0 Ah cell.

  See [docs/PROJECT_STATUS.md](docs/PROJECT_STATUS.md) §6.

---

## Offline data pipeline

**The raw dataset is not committed** (it is large and redistributable from NASA).
Download the *Battery Data Set* from the
[NASA Prognostics Center of Excellence data repository](https://www.nasa.gov/intelligent-systems-division/discovery-and-systems-health/pcoe/pcoe-data-set-repository/)
and drop the `.zip` archives (or the unpacked `.mat` files) anywhere under `data/raw/`.
The scripts locate the files themselves and unpack the archives on first run.

### Pipeline

Run from the repository root, in order:

```bash
python src/extract_battery_data.py    # .mat  -> data/processed/battery_data.csv
python src/build_features.py          # + labels -> battery_health_features.csv
python src/plot_soh.py                # -> results/plots/soh_degradation.png
python src/train_models.py            # -> models/*.pkl, results/lobo_*.csv
python src/evaluate_models.py         # -> results/model_evaluation_report.txt
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
