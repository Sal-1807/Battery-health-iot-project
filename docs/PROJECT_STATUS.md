# Project status

Last audit: 2026-11-17, branch `claude/dreamy-planck-lelbcc` (based on `main` @ `9d6aec2`).

Legend: **Verified** = exercised by a test or command in this environment.
**Not verified** = written but could not be run here (stated why).

## 1. What was in the repository before this integration

| Area | State found | Notes |
|---|---|---|
| Data pipeline (`src/extract_battery_data.py`, `src/build_features.py`) | Present | Raw NASA `.mat` files are git-ignored and not in the repo, so these scripts could not be re-run here. |
| Processed data (`data/processed/*.csv`) | Present | 468 rows: B0005 168, B0006 168, B0018 132 cycles. 15 columns, no missing values. |
| Training (`src/train_models.py`) | Present | Leave-one-battery-out (LOBO) CV + final fit on all 468 rows. |
| Trained models (`models/*.pkl`) | **Missing** | Git-ignored. Only `models/metadata.json` was committed. |
| Metrics (`results/lobo_cv_results.csv`, `training_metrics.csv`, `lobo_predictions.csv`) | Present | |
| Analyses (`results/analysis/b0018_comparison`, `results/experiments/battery_relative_normalization`) | Present | |
| IoT backend, database, simulator, tests (`iot/...`) | **Not in any branch** | `main`, `dev1` and this branch contain no `iot/` directory, no FastAPI code and no firmware. The routes `/api/v1/battery/cycle`, `/api/battery/reading` and `/api/sensor-data` mentioned in team discussions do not exist in Git. If a teammate has them locally, they were never pushed. |
| ESP32 firmware | **Not in any branch** | |
| Dashboard / frontend | **Not in any branch** | |
| README | Stale | Said "no models trained yet" although `train_models.py` and its results were already merged. |

## 2. Model reproduction

The `.pkl` files were regenerated with the team's unchanged `src/train_models.py`, run in a
scratch copy so no tracked file was touched. Result:

- `results/lobo_cv_results.csv`, `results/training_metrics.csv` and `results/lobo_predictions.csv` came out **byte-identical** to the committed ones.
- `models/metadata.json` came out identical.

So the models now in `models/` are the same models the committed metrics describe. This is a
reproduction, not a retrain with new data or settings. The models were produced with scikit-learn 1.9.1.
Each teammate must run `python src/train_models.py` once, since the `.pkl` files stay git-ignored.

## 3. Confirmed working (verified in this environment)

- Models load; the feature order matches `metadata.json` and `src/train_models.py`. Backend inference matches direct scikit-learn calls.
- FastAPI backend: every route listed in [API_REFERENCE.md](API_REFERENCE.md) is covered by tests.
- SQLite persistence across restarts, duplicate/retry handling, range queries, downsampling, CSV export.
- Telemetry validation:
  - units, ranges and timestamps
  - mA/mV mistakenly sent as A/V are rejected, never silently rescaled
  - DS18B20 −127 / 85 °C sentinels
  - floating INA219 input
- Cycle aggregation from live telemetry, completeness checks, gated SoH/RUL inference, prediction history.
- Alerts: temperature, voltage, current, sensor faults, cell not detected, device offline, low predicted SoH. Includes de-duplication, auto-resolve and editable thresholds.
- Simulator → HTTP → API → SQLite, run against a real uvicorn server.
- Dashboard: typecheck, production build, 8 render tests, and Playwright screenshots of all six pages against the live backend. No console errors; no horizontal overflow at 390 px.

## 4. Missing, broken or not verifiable here

| Item | Status |
|---|---|
| ESP32 firmware compile | **Not verified.** The Arduino/ESP32 toolchain download is blocked by this environment's network proxy. The sketch was syntax-checked with `g++ -Wall -Wextra` against stub headers that mirror the library APIs. That catches C++ errors but not library-version mismatches. |
| Flashing / on-device behaviour | **Not verified.** No hardware access. See [HARDWARE_SETUP.md](HARDWARE_SETUP.md) §6 for the checks you must do. |
| Real battery data | **None exists.** No measured discharge from the team's 3000 mAh cell has been recorded. |
| `src/extract_battery_data.py` / `build_features.py` re-run | Not run: raw NASA files are not in the repo. |
| `/api/sensor-data` | Not implemented. Its schema is unknown and it is not in Git; recreating it from memory would be guessing. |
| Authentication | None. The API is for a trusted lab LAN only. |

## 5. Integration issues found and how they were handled

1. **Instantaneous readings vs cycle-level model.** The models take one 10-feature vector per *complete discharge record*. The backend therefore groups readings into discharge segments (`iot/backend/cycles.py`) and never builds features from a single reading.
2. **Sign convention.** NASA current is negative during discharge. The API uses *positive = discharge*, as the INA219 reports when wired cell+ → VIN+. The sign is flipped in exactly one place (`iot/backend/features.py`).
3. **Feature definitions.** Sample statistics over the whole record, including NASA's post-load relaxation tail:
   - `Current_std` uses `np.nanstd` (ddof = 0).
   - `Temperature_rise` = max − *first* sample.

   The live feature code mirrors this, and a test asserts it.
4. **`Cycle_number` is a model input.** Live cells need a registered prior cycle count (`PUT /api/battery/{id}` with `cycle_count_offset`). Without it, prediction is refused rather than guessed.
5. **Domain mismatch, quantified.** The training envelope is narrow:
   - `Current_mean` −2.00 … −1.52 A
   - `Voltage_min` 2.12 … 2.70 V
   - `Temperature_rise` 13.0 … 17.7 °C

   A simulated 10 Ω discharge of a 3 Ah cell lands outside the envelope on **9 of 10 features**. The gate refuses such cycles and shows the user every offending feature.
6. **In-sample vs held-out.** The deployed model was fit on all three NASA cells. Its output on NASA data is labelled `nasa_in_sample`, and accuracy is shown only from the held-out LOBO predictions.
7. **Backfill bug found by tests and fixed.** Segment closing originally compared data timestamps to wall-clock time, which split buffered uploads incorrectly. Ingest-triggered aggregation now uses data time.

## 6. Model limitations (from the committed results)

Held-out (LOBO) performance per test battery, from `results/lobo_cv_results.csv`:

| Held-out | SoH RMSE (pp) | SoH R² | EOL recall | RUL RMSE (cycles) | RUL R² |
|---|---|---|---|---|---|
| B0005 | 3.35 | 0.876 | 1.00 | 14.9 | 0.870 |
| B0006 | 4.82 | 0.853 | 1.00 | 13.4 | 0.863 |
| B0018 | 5.16 | 0.552 | **0.00** | 27.2 | 0.277 |

- Mean ± std over folds: SoH RMSE 4.44 ± 0.97, RUL RMSE 18.5 ± 7.6.
- `results/model_evaluation_report.txt` shows higher "overall" R² values (0.81 / 0.75). Those are pooled across folds, not fold means; the two should not be mixed in reports.
- Three cells is a very small cohort, and B0018 alone shows the model can fail outright on a new cell (it never flags EOL).
- SoH is referenced to 2.0 Ah and EOL to 1.4 Ah. Neither transfers to a 3.0 Ah cell without new labelled data from that cell.
- **No claim of accuracy on the team's hardware can be made yet.** That requires measured full-capacity discharges of the real cell (capacity ground truth), which do not exist.

## 7. Proposed next steps (in order)

1. Teammates pull this branch, run `python src/train_models.py`, `pytest iot/tests`, and the dashboard (README §Quick start).
2. Flash the firmware and complete the device checks in HARDWARE_SETUP.md §6. Sensors only, no load.
3. With the professor, design a *supervised* characterisation setup with proper protection and cutoff (HARDWARE_SETUP.md §5). Only then record real discharges.
4. Use those discharges to measure the cell's actual capacity (ground truth). Decide whether to adapt the SoH definition or train a cell-specific model. Any retrain must be a separate, reviewed change.
5. Optional: add auth / HTTPS if the API ever leaves the lab network, and migrate SQLite → PostgreSQL if multiple long-running devices are used.

## 8. Hardware bring-up log (2026-11-17)

Reported by the team from the Serial Monitor:

- **Firmware:** compiles and uploads with ESP32 core 3.3.12, after installing the Adafruit INA219 library.
- **Sensors:** INA219 and DS18B20 detected (`ok`). The INA219 with no cell reads ≈ 0.888 V and is correctly flagged `cell=NO (floating input)`. DS18B20 reads ≈ 25–30 °C and responds to handling.
- **Wi-Fi:**
  - `SRMIST` is **WPA2-EAP (enterprise)**, so the ESP32 cannot join it with an SSID and password (confirmed by the WiFiScan example).
  - The phone hotspot "Sal" (WPA2, channel 6, −49 dBm) is visible but the join still failed (status 6); the cause is not yet diagnosed.
  - Wi-Fi is parked; the team is using the USB serial bridge (`TRANSPORT_WIFI 0`).
- **Bugs found and fixed:**
  - Wi-Fi retries at 1/2/4 s could abort a join in progress.
  - Distinct readings within the same millisecond were dropped as duplicates (schema migration v2).

## 9. False battery detection — fixed 2026-11-18

**Symptom:** with no cell connected, the floating INA219 input read 0.72–1.14 V. Readings at 1.14 V crossed the 1.0 V "battery detect" threshold, so the backend raised **critical** low-voltage alerts and the dashboard showed the value as a battery voltage. The firmware made the same 1.0 V judgement.

**Fix:**

- **No threshold decides presence any more.** Each reading gets an explicit `cell_state` from evidence the hardware actually provides: Li-ion range plausibility, sustained shunt current, or an operator confirmation (see HARDWARE_SETUP.md §2a).
- **Voltage alerts** run only on `verified_current` or `confirmed` readings. Thresholds are unchanged.
- **Dashboard** shows **No cell** or **Unverified**, and offers "Confirm cell connected" for a resting cell.
- **Firmware** no longer claims presence.
- **History repair:** on upgrade (schema v4), existing readings are back-filled with `cell_state`. Voltage alerts raised without any verified reading are resolved and marked `classification = invalid_measurement` with a note; **no records are deleted**.

**Known limitation:**

- A floating input and a deeply discharged cell (below 2.0 V at rest) remain indistinguishable with the current hardware.
- A resting, unconfirmed cell gets no voltage alerts.
- Hardware options that would improve this are listed in HARDWARE_SETUP.md §2a.

## 10. Measured discharge sessions — added 2026-11-18

- **What is measured:** live mAh and Wh, from trapezoidal integration of measured current and power.
- **When it integrates:** only between consecutive on-readings (≥ 20 mA) at most 60 s apart.
- **What is never counted:** near-zero noise and charging current.
- **Load on/off moments:** not integrated; the unknown share is bounded in `uncertainty_mah`.
- **Gaps:** gaps over 60 s are recorded and never filled; gaps over 5 min end the session.
- **History:** sessions persist after the load stops, and the next session starts from 0.
- **Interpretation:** a session's charge is not the battery's capacity, and SoH/RUL are not derived from it.
- **Unchanged:** protection thresholds; no discharge control was added.

## 11. Multiple physical batteries — added 2026-11-19

- **Profiles:** `BAT-A-3000`, `BAT-B-3000`, `BAT-C-2500`, created by migration v6. Existing data, including `CELL-18650-01`, is kept and is not re-attributed.
- **Routing:** readings follow time-ranged device assignments selected in the dashboard (Batteries page). Uncovered readings go to `UNASSIGNED-<device>`.
- **Switching:** ends sessions and segments with `battery_switched` and clears presence evidence.
- **Cycle counts:** EFC (throughput ÷ own rating) is tracked separately from completed experimental cycles (rested full charge to cutoff, no gaps).
- **SoH/RUL:** never produced for physical profiles. The NASA replay demonstration is unchanged.
- **Not covered:** charging is not instrumented (there is no charge-current path through the INA219). A "completed cycle" is therefore inferred from a rested full-charge voltage before the discharge, not from a measured charge.

## 12. NASA ML validation demo — added 2026-11-19

Dashboard page **ML validation** (API `/api/ml-validation`) shows leave-one-cell-out results for B0005, B0006 and B0018:

- The evaluation models reproduce the committed `results/lobo_predictions.csv` exactly.
- The EOL classifier and the RUL regressor are reported separately, next to the two-stage output.
- Each cell's observed EOL is shown: cycle 125 for B0005, 109 for B0006 and 97 for B0018.

**Honest result:** simple baselines beat the model on 2 of 3 held-out cells for both SoH and RUL.

- **B0006:** a mean-EOL baseline gets RUL MAE 1.3 cycles against the model's 8.8. This is partly luck, because the training cells' mean EOL (111) lands close to B0006's actual EOL (109).
- **B0018:** the EOL classifier never fires (recall 0).

Three cells cannot support a robust accuracy claim. The predictions are not validated for the project's 2500 mAh or 3000 mAh cells.

## 13. Aging analytics — added 2026-11-19

The **Aging analytics** page covers BAT-A, BAT-B and BAT-C:

- **Totals:** delivered mAh and Wh, discharge time, and EFC normalised by each battery's own rating.
- **Capacity:** measured capacity across comparable full tests.
- **Retention:** relative to the battery's first measured test. It is not SoH, and the label is not treated as an initial capacity.
- **Trends:** per-session mean temperature and mean loaded voltage.
- **Comparison table:** across the three batteries, including C-rate.
- **Exclusions:** each excluded session is listed with its reason.
- **Without data:** the page shows "Insufficient data" until at least two comparable full tests exist. No such tests have been recorded on real hardware yet.
