# Battery Health Monitoring with RUL Prediction (IoT + ML)

ESP32 telemetry (INA219 voltage/current, DS18B20 temperature) → USB serial bridge → FastAPI + SQLite →
measured discharge statistics and 5-minute Quick Test Cycles → React dashboard, with NASA-trained
State-of-Health (SoH) / Remaining-Useful-Life (RUL) models shown as **experimental** estimates only.

> **Status (2026-11):** sensors, backend, bridge and dashboard work end to end on the bench.
> Batteries BAT-A-3000, BAT-B-3000 and BAT-C-2500 are each measured with **3 Quick Test Cycles of 300 s**.
> The ML models are validated only on NASA cells. On the team's batteries they produce at most a gated
> **Experimental ML Estimate**, never a validated SoH/RUL, and no capacity is inferred from a 5-minute test.

Documentation: [Project status](docs/PROJECT_STATUS.md) · [Architecture](docs/ARCHITECTURE.md) ·
[Hardware setup](docs/HARDWARE_SETUP.md) · [API reference](docs/API_REFERENCE.md) ·
[Simulation](docs/SIMULATION.md) · [Readiness audit](docs/READINESS.md) · [Dataset assessment](docs/dataset_assessment.md)

---

## Architecture

```
ESP32 (INA219 + DS18B20) ──USB serial──▶ iot/bridge/serial_bridge.py ──HTTP JSON──▶ FastAPI (iot/backend) ──▶ SQLite
                         (or Wi-Fi HTTP) ─────────────────────────────────────────▶   │ validate · cell-presence · store
                                                                                       │ device → battery assignment
                                                                                       │ discharge sessions (mAh, Wh, EFC)
                                                                                       │ Quick Test protocol · alerts
                                                                                       │ gated inference with models/*.pkl
                                                                                       ▼
                                                                  React dashboard (dashboard/) ◀── polling /api/*

offline: NASA .mat → src/extract_battery_data.py → src/build_features.py → src/train_models.py → models/*.pkl
```

Details and the data model: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Dashboard

The dashboard is focused on one battery, **BAT-A-3000** (setting `BHM_FOCUS_BATTERY`, default `BAT-A-3000`; set it to an
empty value to show all profiles again). BAT-B-3000 and BAT-C-2500 stay in the database unchanged and are only hidden.

| Section | What it shows |
|---|---|
| **Overview** | Live sensor readings (voltage, current, temperature, power), sensor and cell-presence status, live charts, and the two **Experimental Demo Estimate** scores. |
| **Quick Tests** | The 3 × 300 s protocol: progress, the completed tests and their results, live countdown for a new test, the physical-disconnect step, validity checks and CSV export. |
| **Battery Analysis** | Quick Test results (averages ± range), per-test trends, measured discharge throughput, measured full capacity (only from complete discharges), reading history with CSV export, and a collapsed *Device, alerts and records* section (device assignment, alert thresholds, system status, archived records). |
| **Battery Health Predictions** | The Experimental Demo Estimate (Demo SoH / Demo RUL) with a short method view, then the separate saved-ML-model demonstration on NASA dataset cycles and the leave-one-cell-out validation. |

Retired IDs such as `CELL-18650-01` are kept read-only under Battery Analysis → Device, alerts and records.

## Sample Demonstration Result

Overview and Battery Health Predictions also show a **Sample Demonstration Result** (SoH 91.6 %, RUL 84 remaining
cycles). These are fixed illustrative values defined in `dashboard/src/lib/sample.ts`. They are not measured, not
computed from BAT-A-3000's data and not a model output; they never reach the backend, SQLite, telemetry, Quick Test
results or the ML predictions.

## Experimental Demo Estimate (illustrative, not ML, not measured)

Once a battery has 3 valid Quick Tests, `iot/backend/demo_estimate.py` turns their **measured** values into two
illustrative scores with a fixed, documented arithmetic heuristic (no randomness, same constants for every battery):

```
R      = mean(voltage drop / mean current)                    apparent 300 s window resistance, ohm
theta  = mean(temperature rise / mean power)                  thermal response, K/W
SoH    = clamp(100 - 20 * max(0, R / 0.15 - 1) - min(5, 2 * max(0, theta - 2)), 0, 100)
RUL    = 500 * max(0, SoH - 80) / 20                          cycles
```

The constants are illustrative assumptions: a reference window resistance of 0.15 Ω for a healthy cell on this rig;
20 points lost when resistance doubles (the common "end of life ≈ resistance doubled ≈ 80 % SoH" rule of thumb); a
small capped thermal term; a linear fade over a typical 500-cycle datasheet life to 80 %. Initial/final voltage, mAh
and Wh are shown with the estimate. With fewer than 3 valid tests the dashboard shows **No estimate yet**.

**Limitations.** These scores are not a measured SoH, not a validated RUL and not a machine-learning prediction. A
300 s test at ~0.37 A carries little health information: the apparent resistance includes polarisation and wiring,
and the reference constants were not calibrated on these cells. The NASA-trained models cannot use Quick Tests (their
10 inputs are whole-cycle statistics plus the true cycle count; see `iot/backend/short_test_ml.py` and
`GET /api/ml/quick-test-assessment`), and no defensible short-test ML model can be built from the available data
(NASA current regime ~2 A vs ~0.37 A, no measured full-capacity discharge for the cell, no observed degradation).

## Quick Test protocol

A **Quick Test Cycle** is one 5-minute (300 s) measurement under the resistive load. It is *not* a full
charge–discharge cycle, it does not change the full-cycle count, and it is never used as the battery's capacity.

1. Battery Analysis → Device, alerts and records → check that the physically connected battery is selected.
2. Quick Tests → **New 5-minute Quick Test** → complete the checklist → **Start recording**, then connect the load.
3. The countdown runs. At 300 s **the software stops recording automatically. This does NOT disconnect the
   battery or stop the current** — there is no switching hardware.
4. Physically remove the load, then click **I have physically disconnected the load**. The backend refuses this
   while it still measures load current, and refuses to start the next test until it is confirmed.
5. Repeat until each battery has 3 valid tests (9 in total).

A test counts only if it completed the full 300 s window, the battery stayed selected, load current was present for
≥ 280 s, there were no telemetry gaps, ≤ 2 % missing values and ≥ 30 readings. Otherwise it is listed as
**interrupted**, **incomplete** or **telemetry gap** and is not counted. Reported per test: voltage drop (from the last
at-rest reading before start), average current, average power, max temperature, discharged mAh and delivered Wh.

## Repository structure

```
.
├── src/                    offline data pipeline + training (NASA)
├── data/processed/         battery_data.csv, battery_health_features.csv (468 cycles)
├── models/                 metadata.json (committed) + *.pkl (git-ignored, regenerate)
├── results/                LOBO metrics/predictions, analyses, experiments
├── iot/
│   ├── backend/            FastAPI app (api.py, telemetry.py, sessions.py, battery_tests.py, aging.py,
│   │                       cycles.py, features.py, model_service.py, ml_validation.py, alerts.py, db.py, config.py)
│   ├── bridge/             serial_bridge.py (USB serial → backend)
│   ├── firmware/esp32_battery_monitor/   Arduino sketch + config.example.h
│   ├── simulation/         end-to-end simulation runner (separate database)
│   ├── simulator/          low-level synthetic telemetry / NASA replay generator (developer tool)
│   ├── tests/              pytest suite
│   └── data/               runtime SQLite databases (git-ignored)
├── dashboard/              React + Vite + TypeScript + Tailwind + Recharts
└── docs/
```

## Hardware

ESP32 DevKit V1, INA219 (I²C: SDA GPIO21, SCL GPIO22, 3V3, GND), DS18B20 (DATA GPIO4 with
4.7 kΩ pull-up to 3V3), 18650 Li-ion cells (BAT-A 3000 mAh, BAT-B 3000 mAh, BAT-C 2500 mAh, label ratings),
resistive load. Wiring, firmware upload and the transport options are in [docs/HARDWARE_SETUP.md](docs/HARDWARE_SETUP.md).

> **Safety.** The firmware, backend and dashboard only *measure*, *record* and *alert*. Nothing in this project
> connects, disconnects or switches the load, cuts off at a voltage, or controls charging. Every test is supervised
> and the load is removed by hand. Read HARDWARE_SETUP.md §5 before connecting a cell to any load.

---

## Setup

All commands run from the repository root unless noted.

### 1. Python environment and models

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python src/train_models.py       # regenerates models/*.pkl (git-ignored)
```

`train_models.py` is deterministic (`random_state=42`); on the same library versions it reproduces
`results/lobo_*.csv` byte for byte.

### 2. Backend

```bash
uvicorn iot.backend.main:app --host 0.0.0.0 --port 8000
```

Check `http://localhost:8000/api/health`; interactive API docs at `http://localhost:8000/docs`. The production
database is `iot/data/telemetry.db` (override with `BHM_DB_PATH`). Settings: `iot/backend/config.py` (`BHM_*`).

### 3. Dashboard

```bash
cd dashboard
npm install
npm run dev          # http://localhost:5173  (proxies /api to localhost:8000)
```

Production build: `npm run build && npm run preview`. If the backend runs elsewhere, set `BHM_API_URL=http://<host>:8000`.

### 4. ESP32 and the USB bridge

Copy `iot/firmware/esp32_battery_monitor/config.example.h` to `config.h`, set `TRANSPORT_WIFI 0` for USB, and upload
with the Arduino IDE (board "ESP32 Dev Module"; libraries Adafruit INA219, OneWire, DallasTemperature). Close the
Serial Monitor, then forward the readings:

```bash
python -m iot.bridge.serial_bridge --port COM9
```

Use your own port (`/dev/ttyUSB0` on Linux). Wi-Fi transport is optional; see [docs/HARDWARE_SETUP.md](docs/HARDWARE_SETUP.md) §3–4.

## Simulation (development only, clearly labelled)

```bash
python -m iot.simulation.run
```

This runs 52 checks of a synthetic workflow through the real API, in a **separate** database
(`iot/data/simulation.db`): analytic 1000 mAh / 4.000 Wh checks, full and partial discharges, battery switching,
gaps, duplicates, out-of-order telemetry, tests, isolation and CSV exports. It verifies that the production database is
unchanged. To view it, start the backend with `BHM_MODE=simulation`; every page then shows a SIMULATION MODE
banner and simulated profiles are tagged SIMULATED. See [docs/SIMULATION.md](docs/SIMULATION.md).

## Tests

```bash
pytest iot/tests -q                 # backend: 182 tests, includes the simulation run
python -m iot.simulation.run        # 52 end-to-end checks
cd dashboard && npm test            # dashboard: 56 tests (vitest + jsdom)
cd dashboard && npm run build       # typecheck + production build
```

The model tests skip (rather than fail) if `models/*.pkl` have not been generated.

## What is measured, simulated and estimated

| Kind | Where | Meaning |
|---|---|---|
| **Measured** | Overview, Quick Tests, Battery Analysis | INA219 / DS18B20 readings and quantities integrated from them (mAh, Wh, EFC). Voltage counts as a cell voltage only once presence is verified by load current or by an operator. |
| **Simulated** | Only in `BHM_MODE=simulation` | Synthetic data in its own database, always labelled. |
| **Experimental Demo Estimate** | Overview, Battery Health Predictions | Illustrative heuristic score from the measured Quick Tests (see above). Not measured, not validated, not ML. |
| **Experimental ML Estimate** | Battery Health Predictions, Overview | Output of the NASA-trained models on a physical battery's discharge cycle, *only* if the cycle is complete, every input lies inside the NASA training range (widened by 10 % of that range) and the cycle count is known. Not validated for these cells. |
| **Not available** | — | Shown with the reason whenever the gate is not passed. No SoH, RUL or capacity number is ever filled in. |

## Limitations

- **Quick Tests cannot feed the ML models, and no short-test model is defensible.** Battery Health Predictions shows,
  per battery, the measured Quick Test features next to the 10 saved-model inputs and why each is incompatible
  (`iot/backend/short_test_ml.py`). The checks that fail: the load current (~0.37 A) is far outside NASA's ~2 A
  regime, there is no measured full-capacity discharge to evaluate an SoH prediction against, and 3 short tests show
  no degradation trend for RUL. SoH/RUL therefore stay *Not available* after 3/3 Quick Tests; the saved models are
  demonstrated on NASA dataset cycles instead.
- **No validated SoH/RUL for BAT-A/B/C.** The models were trained on three NASA 2.0 Ah cells at ~2 A constant current.
  A 300 s Quick Test is not a complete discharge cycle, so it cannot be fed to the cycle-level models; in practice the
  physical batteries show **Not available** unless a complete, in-range discharge is recorded.
- **Quick Tests compare batteries; they do not measure capacity.** Total capacity needs a full, uninterrupted,
  rested-full-charge → cutoff discharge, which none of the Quick Tests is.
- **Label ratings** (3000 / 2500 mAh) are manufacturer values, not measurements. EFC uses them only as a normaliser.
- **No hardware cutoff or protection.** Stopping, aborting or the timer ending changes only the software record.
- **Single INA219 channel.** Only one battery can be measured at a time; the operator selects which.
- **Timestamps** over USB come from the PC clock (labelled as such).
- Firmware compiles in the Arduino IDE on the team's machine; it was not compiled in CI.

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
  - live data from a small resistive load is far outside the training range;
  - nothing has been validated on the team's 3000 / 2500 mAh cells.
- **Policy for physical batteries:** the saved models run only through the gate described above and the result is
  labelled *Experimental ML Estimate*. The dashboard's model demo applies the same saved models to real NASA dataset
  cycles (in-sample, shown next to the held-out leave-one-cell-out prediction) and never attaches those values to
  BAT-A/B/C.

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
