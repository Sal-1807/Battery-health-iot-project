# API reference

Base URL: `http://<host>:8000`. Interactive OpenAPI docs: `/docs`, schema: `/openapi.json`.

## Conventions

| Topic | Rule |
|---|---|
| Units | volts (V), amperes (A), degrees Celsius (°C), watts (W); the unit is in every field name |
| Current sign | `current_a > 0` = **discharging**. Cycle features use NASA's sign (negative = discharge). |
| Timestamps | ISO-8601 **with offset** (e.g. `2026-11-17T05:13:10.265Z`). Naive timestamps are rejected. |
| IDs | `^[A-Za-z0-9_.-]{1,48}$` |
| Errors | `{"detail": "..."}` with HTTP 404 (unknown battery), 409 (source conflict), 422 (validation), 503 (database error) |
| Unknown fields | rejected (422), so typos never vanish silently |
| Auth | none: trusted LAN only |
| CORS | `http://localhost:5173`, `:4173` and the `127.0.0.1` equivalents. Override with `BHM_CORS_ORIGINS` (comma-separated). |

## Telemetry

### `POST /api/battery/reading`

```json
{
  "battery_id": "CELL-18650-01",
  "device_id": "esp32-devkit-01",
  "source": "hardware",
  "timestamp": "2026-11-17T05:13:10.265Z",
  "voltage_v": 3.891,
  "current_a": 0.372,
  "power_w": 1.447,
  "temperature_c": 24.56,
  "seq": 1532,
  "boot_id": "a1b2c3d4e5f6",
  "uptime_ms": 3064000,
  "rssi_dbm": -61,
  "firmware_version": "1.0.0",
  "sensor_status": {"ina219": "ok", "ds18b20": "ok", "battery_connected": true}
}
```

Optional fields: everything except `battery_id`. At least one measurement or a `sensor_status` must be present.

**Validation**

| Field | Rule |
|---|---|
| `voltage_v` | 0–5.5 |
| `current_a` | −5…5 |
| `temperature_c` | −55…125 |
| `power_w` | 0–25 |

- Values in mV/mA/mW fall outside these ranges and are **rejected**, never rescaled.
- Timestamps more than 5 min in the future or more than 7 days old are rejected.
- Legacy field names `voltage`, `current`, `temperature`, `power` are accepted as aliases, but they must be in V/A/°C/W.

**Server-side normalisation** (each case is recorded in `flags`):

| Condition | Effect / flag |
|---|---|
| no `timestamp` | server time used; `server_timestamp` |
| `temperature_c` = −127 or DS18B20 status ≠ ok | temperature stored as null; `temperature_sensor_fault` |
| `temperature_c` = 85.0 | null; `ds18b20_power_on_value` |
| INA219 status `error`/`not_found` | voltage and current stored as null; `ina219_fault` |
| `cell_state` = `no_cell` (voltage outside 2.0–4.5 V, no current evidence) | `battery_not_detected` |
| `cell_state` = `unverified` (in range, no current evidence, not confirmed) | `cell_unverified` |
| `power_w` differs from V·\|I\| by > max(0.05 W, 15 %) | `power_inconsistent` |
| `power_w` missing | computed as V·\|I\| (`power_source: "computed"`) |

**Cell presence (`cell_state`).** Each stored reading gets one of these:

| `cell_state` | Meaning | Used as cell voltage / voltage alerts? |
|---|---|---|
| `unknown` | no voltage (INA219 fault) | no |
| `no_cell` | outside 2.0–4.5 V with no current evidence: floating input *or* deeply discharged cell (indistinguishable) | no |
| `unverified` | in range, but at ~0 A presence cannot be proven | no |
| `verified_current` | \|I\| ≥ 10 mA on two consecutive readings, or an unbroken in-range run since then | yes |
| `confirmed` | operator confirmation (below), unbroken since | yes |

- `sensor_status.battery_connected` from firmware is stored for diagnostics only and **never** used as evidence.
- Settings: `BHM_CELL_PLAUSIBLE_MIN_V`, `BHM_CELL_PLAUSIBLE_MAX_V`, `BHM_CURRENT_EVIDENCE_A`, `BHM_PRESENCE_GAP_S`.
- In history, `voltage_v` is null unless the state is verified; `voltage_raw_v` always holds the INA219 value.

**Response 200**

```json
{"status": "stored", "reading_id": 812, "ts": "2026-11-17T05:13:10.265Z",
 "timestamp_source": "device", "cell_state": "verified_current", "flags": [], "cycles_closed": []}
```

- A retried message with the same `(device_id, boot_id, seq)` or the same timestamp returns `"status": "duplicate"` and the original `reading_id`.
- `cycles_closed` lists discharge segments this reading closed, each with its prediction result.
- Using a battery ID first seen with another `source` returns 409.

### `POST /api/battery/readings`

Body `{"readings": [ ...up to 500 ReadingIn... ]}`.

Response: `{"results": [...], "stored": n, "duplicates": n, "rejected": n}`. Items are processed in timestamp order, and a rejected item does not fail the batch.

### `GET /api/battery/{id}/latest`

`{"battery_id", "connection": {"state", "age_s"}, "reading": Reading}`. The connection `state` is one of `online`, `stale`, `offline` or `never_seen`.

### `GET /api/battery/{id}/history`

| Query | Default | Notes |
|---|---|---|
| `start`, `end` | – | ISO with offset |
| `limit` | 2000 | max 50000, newest rows kept |
| `max_points` | – | 10–5000: time-bucket averages for charts (`downsampled: true`, `bucket_s`) |
| `format` | `json` | `csv` → download (raw rows, no downsampling) |

Response: `{"battery_id", "total", "downsampled", "truncated", "points": [...]}`. When no cell was detected, `voltage_v` is null and the raw value is in `voltage_raw_v`.

## Batteries

| Route | Description |
|---|---|
| `GET /api/batteries` | All batteries with connection state, latest reading, latest (and latest valid) prediction, active alert counts, reading/cycle counts |
| `GET /api/battery/{id}` | One battery summary |
| `POST /api/battery/{id}/presence` | Body `{"confirmed": true\|false, "note": "..."}`. Operator confirmation that a cell is connected. Refused (409) if the latest reading is outside the Li-ion range or older than the presence gap. `false` withdraws it. |
| `PUT /api/battery/{id}` | Update `label`, `chemistry`, `rated_capacity_ah`, `nominal_voltage_v`, `cutoff_voltage_v`, `cycle_count_offset`, `notes`. **`cycle_count_offset` is required before live cycles can be predicted** (model input `Cycle_number`). |

## Cycles and predictions

| Route | Description |
|---|---|
| `POST /api/battery/cycle` | Post a cycle-level feature vector directly (NASA replay / manual). Fields: `battery_id`, `source` (`nasa_replay`/`manual`), `cycle_number`, the 9 features in snake_case (`current_mean` negative = discharge), optional `ref_soh`, `ref_rul`, `rated_capacity_ah`. Idempotent on `cycle_number`. |
| `GET /api/battery/{id}/cycles` | Detected/replayed cycles with features, `complete`, `completeness_notes` |
| `POST /api/battery/{id}/cycles/aggregate` | Close and store segments now (uses wall-clock time, so a silent device's last segment is closed) |
| `GET /api/battery/{id}/sessions` | Measured discharge sessions, newest first: `charge_mah`, `energy_wh`, `uncertainty_mah`, `duration_s`, start/end, gaps, `kind` (`partial` / `full_range_unvalidated`), `interpretation`. A session's charge is what flowed **during that session**: not capacity, SoC or SoH. Rules are in `iot/backend/sessions.py`. `/api/batteries` also returns `active_session` and `last_session`. |
| `GET /api/battery/{id}/predictions` | Prediction history (`include_unavailable=false` to hide refusals). Joined with `ref_soh`/`ref_rul` where known. |
| `POST /api/battery/{id}/predict` | Re-run the gated model on the latest cycle (e.g. after setting `cycle_count_offset`) |

**Prediction object**

| Field | Meaning |
|---|---|
| `status` | `unavailable` / `experimental` / `nasa_in_sample` |
| `soh_pct` | SoH %, on the NASA 2.0 Ah scale |
| `eol_probability`, `eol_predicted` | stage-1 classifier output |
| `rul_cycles` | 0 if `eol_predicted = 1`, else the regressor output clipped at ≥ 0 |
| `reasons` | why it was refused |
| `warnings` | caveats on an output that was produced |
| `out_of_range` | per feature: `value`, `train_min`, `train_max`, `allowed_min`, `allowed_max` |
| `input_features` | the vector actually used |
| `model_version` | `rf-nasa-<sha256 of the 4 .pkl files>` |

## Alerts

| Route | Description |
|---|---|
| `GET /api/alerts?battery_id=&status=active\|resolved&limit=` | Alert list, newest first. Voltage alerts carry `classification`: `valid`, or `invalid_measurement` with a `resolution_note`. |
| `GET /api/battery/{id}/alerts` | Same, for one battery |
| `POST /api/alerts/{id}/acknowledge` | Mark acknowledged |
| `GET /api/settings/thresholds` | Current thresholds |
| `PUT /api/settings/thresholds` | Replace thresholds (warning ≤ critical ordering is validated) |

**Alert types:**

- `temperature_high`, `temperature_low`
- `voltage_low`, `voltage_high`
- `current_high`
- `temperature_sensor_fault`, `ina219_fault`
- `battery_not_detected` (severity **info**: no usable cell measurement)
- `cell_signal_lost` (warning: a verified cell's reading left the Li-ion range at rest)
- `device_offline`
- `soh_low`: only from `experimental`/`nasa_in_sample` predictions, and flagged `experimental=1`

**Default thresholds** (with units):

| Threshold | Warning | Critical |
|---|---|---|
| High temperature | 45 °C | 55 °C |
| Low temperature | 0 °C | – |
| Low voltage | 3.2 V | 3.0 V |
| High voltage | 4.2 V | 4.25 V |
| \|Current\| | 1.0 A | – |
| Predicted SoH | 80 % | 70 % |
| Device offline after | 30 s | – |

## Models, NASA data, system

| Route | Description |
|---|---|
| `GET /api/health` | `status` ok/degraded, database ok, models loaded + version |
| `GET /api/system/status` | Health + devices (state, RSSI, firmware, sensor status, data origin) + reading counts by source + model info |
| `GET /api/models` | Feature order, training envelope, estimators, scikit-learn version, limitations, LOBO per-fold and aggregate metrics |
| `GET /api/nasa/batteries` | B0005/B0006/B0018 summaries (cycles, EOL cycle, initial/final SoH) |
| `GET /api/nasa/{id}/cycles` | Features + measured SoH/RUL + held-out LOBO predictions per cycle |

## Not implemented

`/api/v1/*` and `/api/sensor-data` from earlier discussions are not in the repository. Their schemas are unknown, so they were not recreated. If a teammate has that code locally, compare it with the routes above before merging.

## Multiple physical batteries

Profiles `BAT-A-3000` (3000 mAh), `BAT-B-3000` (3000 mAh) and `BAT-C-2500` (2500 mAh) are created by schema migration v6 (`managed = 1`).

| Route | Description |
|---|---|
| `GET /api/devices` | Devices and their current assignment (selected battery) |
| `PUT /api/devices/{device_id}/assignment` | Body `{"battery_id": "BAT-B-3000" \| null, "note": "..."}`. Selects the physically connected battery from now on. Ends the previous battery's discharge session (`end_reason = battery_switched`), closes its open cycle segment as incomplete, and clears presence evidence on both batteries. 409 if the profile is already selected on another device. |
| `GET /api/devices/{device_id}/assignments` | Assignment history |
| `GET /api/battery/{id}/stats` | Sessions, total discharged mAh/Wh, `equivalent_full_cycles`, `completed_cycles`, `capacity_measurements`, assignment history |
| `GET /api/batteries/compare` | The same per profile, side by side |

**Routing.** A reading is stored under the battery whose assignment covers the reading's own timestamp. `battery_id` sent by the firmware is kept as `reported_battery_id`.

- If no assignment covers the reading, a profile **never** receives it: it goes to `UNASSIGNED-<device_id>`.
- Devices that have never been assigned keep the old behaviour (the firmware's ID).

**Selection vs presence.** Selecting a profile is not presence. Presence still needs load current or `POST /api/battery/{id}/presence`, and the latter is refused for a profile that isn't selected.

**Cycle counting.**
- **EFC** (equivalent full cycles) = total measured discharge ÷ *that battery's* rated capacity: a throughput figure.
- **Completed cycles** counts only sessions that ran from a rested full charge (≥ 4.15 V) to the cutoff with no telemetry gaps. A short session never counts as a cycle.

`soh` in the comparison is `experimental` if the battery has any gate-passing Experimental ML Estimate, otherwise `unavailable`. `quick_test_cycles` is the number of valid Quick Test Cycles. `GET /api/batteries` marks every battery with `archived` (true for retired IDs such as `CELL-18650-01`, `UNASSIGNED-*` and older demo data); archived records are read-only history.

## NASA ML validation demo (separate from live hardware)

| Route | Description |
|---|---|
| `GET /api/ml-validation` | Leave-one-cell-out results for B0005, B0006 and B0018. Per held-out cell: SoH MAE/RMSE; EOL classifier accuracy, precision, recall, F1 and confusion matrix; RUL regressor alone (pre-EOL cycles) MAE/RMSE; two-stage RUL MAE/RMSE; baselines; RUL error by life stage; observed/censored EOL. Also mean ± std across folds, a reproduction check against `results/lobo_predictions.csv`, the protocol and the disclaimer. |
| `GET /api/ml-validation/{cell}` | Per-cycle actual, predicted and baseline SoH and RUL for one held-out cell |
| `GET /api/ml-validation/{cell}/demo?cycle=N` | Model demonstration on one **real NASA dataset cycle**: its features, the measured SoH/RUL, the saved production models' output (in-sample, labelled as such) and the held-out leave-one-cell-out prediction for the same cycle. Never applied to BAT-A/B/C. |

How the evaluation models are built:

- They are trained in memory on the two *other* cells, with the team's own `src/train_models.py` functions.
- The production `models/*.pkl` were trained on all three cells, so they are never used for these metrics.
- Baselines: SoH uses a line fit of SoH against cycle on the training cells. RUL uses the training cells' mean EOL cycle minus the current cycle.

## Aging analytics (physical battery profiles)

| Route | Description |
|---|---|
| `GET /api/aging` | Per profile (BAT-A, B and C): delivered mAh, Wh and discharge time, EFC, numbers of full and comparable tests, reference test, retention status, plus the comparability rules |
| `GET /api/aging/{battery_id}` | The same plus the capacity series, per-session temperature and loaded-voltage trends, and each session's eligibility with reasons. 400 for non-profile batteries. |

**What counts as a full test.** A full test must:

- start from a rested full charge (≥ 4.15 V);
- reach the cutoff voltage;
- end because the load stopped;
- have no telemetry gaps;
- have on/off transition uncertainty ≤ 2 % of its charge.

**What counts as comparable.** Mean current within ±15 % and mean temperature within ±5 °C of the reference test, with the same cutoff voltage.

**Retention** is each test's measured capacity divided by the battery's **first comparable measured test**. It requires at least 2 comparable tests; otherwise the status is `insufficient_data`. It is not absolute SoH, and the label rating is never used as the reference. SoH and RUL are always `unavailable`.

**Behaviour change.** Sessions that reach the cutoff but end because the battery was switched or telemetry was lost are no longer classified as full range, and so no longer count as completed cycles.

## Battery Test Manager (records only, no hardware control)

| Route | Description |
|---|---|
| `POST /api/tests` | `{"battery_id": "BAT-A-3000", "notes": "...", "kind": "quick"}` creates a test with a unique ID, e.g. `T-20261119-BAT-A-3000-001`. Physical profiles only. `kind` is `quick` (default; 300 s window, `duration_s` 60–3600 may override) or `supervised` (open-ended legacy test). 409 while the previous test on that battery still awaits a disconnect confirmation. |
| `POST /api/tests/{uid}/confirm-disconnect` | The operator states that the load was **physically** removed. 409 while the latest reading is recent and shows load current (≥ 0.02 A). Recorded as a confirmation, not a hardware action. |
| `PUT /api/tests/{uid}/checklist` | Confirmations (identity, load rating, supervisor approval, disconnect procedure, cutoff) plus `supervisor_name`, `cutoff_v` (2.5–3.3 V), `load_resistance_ohm`, `load_power_rating_w`. The test becomes `ready` when complete and the battery is selected on a device. A load rated below its 4.2 V dissipation is refused. |
| `POST /api/tests/{uid}/start\|pause\|resume\|complete\|abort` | Changes the **software** state only. 409 on an invalid transition, a changed battery selection, or another active test on the same battery or device. Every response includes `hardware_notice`; pause, complete and abort also return `warning` ("Physically disconnect the discharge load now"). |
| `GET /api/tests?battery_id=&state=` | All tests with results, validity checks and measured hardware state |
| `GET /api/tests/{uid}` | One test: live results while running, frozen results after completion or abort |
| `GET /api/tests/export.csv?battery_id=` | One row per test |
| `GET /api/tests/{uid}/readings.csv` | The battery's readings in the test window, each with a `test_running` flag |

**Results.** Initial and final voltage, minimum loaded voltage, mean and peak current and power, temperatures, delivered mAh and Wh, elapsed and load-on time. They're integrated only inside running intervals, and only between consecutive on-readings ≤ 60 s apart.

**Data-quality flags.** Telemetry gaps (including at the start or end of a window), missing measurements, pauses (and load current measured while paused), and a battery-selection change during the test.

**Valid for full-capacity / cycle analysis.** Only if *all* of these hold:

- the test was completed;
- the checklist was complete;
- the cell was rested at ≥ 4.15 V at the start;
- the cutoff was reached;
- no gaps;
- never paused;
- the battery stayed selected;
- at most 2 % of readings have a missing value;
- at least 30 readings.

A completed test that didn't reach the cutoff is classified as `partial discharge (not a full cycle)`. SoH and RUL are always `unavailable`.

**Throughput vs full capacity** (`/api/aging`, `/api/aging/{id}`):

- `throughput` is the **measured discharge throughput**: charge, energy and time from *all* sessions, partial ones included. It reports the partial and full contributions separately and gives EFC = throughput ÷ the battery's own rating. It is not the battery's capacity.
- A session overlapping an earlier one in time is excluded and listed in `overlapping_sessions_excluded`, so charge is never counted twice. Duplicate telemetry is already rejected at ingest.
- **Measured full capacity**, retention and completed cycles still come only from full tests. Thresholds are unchanged: 4.15 V rested start, 3.0 V default cutoff.

## Quick Test Cycles (3 × 300 s per battery)

| Route | Description |
|---|---|
| `GET /api/quick-tests?battery_id=` | Per profile: valid Quick Test Cycles (`count` / `target` = 3), `next_test_number`, per-test metrics, every attempt with its status, summary (mean/min/max over the first 3 valid tests) and counts of tests not counted. Also the A/B/C `comparison` (each metric with `_range` and `_diff_pct` vs. the group mean), `model_status` and `protocol`. |
| `GET /api/quick-tests/features.csv` | One row per valid Quick Test with its measured features, for experimental analysis. The first line states that no validated model exists for these features. |

Each quick test in `/api/tests` carries a `quick_test` block: `status` (`counted`, `incomplete`, `interrupted`, `telemetry_gap`, `invalid`, `in_progress`, `not_started`), `number`, `elapsed_s`, `remaining_s`, `auto_ended`, `needs_disconnect_confirmation` and the validity `checks`.

**Automatic end.** When a running quick test reaches `started + duration_s`, the backend completes its *software record* at exactly that time with the note "software recording window ended automatically; this did NOT disconnect the battery or stop the discharge current". This happens on the next request to the tests, quick-tests or batteries endpoints.

**Counted** only if: completed (not aborted), never paused, battery stayed selected, the full window was recorded, load current present for ≥ 280 s with charge delivered, no telemetry gaps, ≤ 2 % readings with a missing value, ≥ 30 readings. Priority when several fail: interrupted > telemetry gap > incomplete.

**Metrics:** initial voltage (last at-rest reading before start), final voltage, voltage drop, mean voltage (Wh ÷ Ah), mean current, mean power, max temperature, temperature rise, discharged mAh, delivered Wh. A Quick Test is not a full cycle: it does not change EFC rules, completed cycles or measured full capacity.

## ML predictions for physical batteries

After each complete discharge cycle the backend calls the saved models through the gate in `iot/backend/model_service.py`. A result is stored as `experimental` only if the cycle is complete, every feature lies inside the NASA training range widened by 10 % of that range, and the cycle number is known; otherwise `unavailable` with the reasons. Physical batteries never get any other status.

## Quick Tests and the ML models (`GET /api/ml/quick-test-assessment?battery_id=`)

For each physical profile (or one battery) this reads the counted Quick Test Cycles and returns:

- `quick_tests.measured`: the genuine per-test values (voltages, drop, mean current/power, max temperature, mAh, Wh);
- `saved_model.inputs`: each of the 10 saved-model inputs with its NASA training range, the closest Quick Test value
  (if any), a `kind` (`missing`, `different_quantity`, `out_of_regime`) and the reason it cannot be used;
- `short_test_model`: checks for whether a defensible short-test model could be built (training windows, operating
  regime, ground-truth SoH for the cell, observed degradation for RUL) and the conclusion;
- `soh` / `rul`: always `{"status": "unavailable", "value": null}`. The endpoint never produces an SoH or RUL number.

The logic and the per-input explanations are in `iot/backend/short_test_ml.py`.

## Experimental Demo Estimate and single-battery focus

- `GET /api/quick-tests` adds `demo_estimate` per battery, and `GET /api/batteries` adds it under
  `quick_test_cycles.demo_estimate`: `status` (`demo` | `no_estimate`), `label` ("Experimental Demo Estimate"),
  `soh_pct`, `rul_cycles`, `inputs` (averaged measured values plus apparent resistance and thermal response),
  `components`, `constants`, `tests_used`, `disclaimer`. Computed by `iot/backend/demo_estimate.py`; it is a
  documented heuristic, not an ML prediction, and it never writes to the predictions table.
- `GET /api/health` reports `focus_battery` (setting `BHM_FOCUS_BATTERY`, default `BAT-A-3000`; `SIM-BAT-A-3000` in
  simulation mode; `null` when empty). The dashboard shows only that profile; every endpoint still serves all profiles.
