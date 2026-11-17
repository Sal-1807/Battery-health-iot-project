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
| `voltage_v` < 1.0 V or `battery_connected=false` | `battery_not_detected`; excluded from cycles and voltage alerts |
| `power_w` differs from V·\|I\| by > max(0.05 W, 15 %) | `power_inconsistent` |
| `power_w` missing | computed as V·\|I\| (`power_source: "computed"`) |

**Response 200**

```json
{"status": "stored", "reading_id": 812, "ts": "2026-11-17T05:13:10.265Z",
 "timestamp_source": "device", "flags": [], "cycles_closed": []}
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
| `PUT /api/battery/{id}` | Update `label`, `chemistry`, `rated_capacity_ah`, `nominal_voltage_v`, `cutoff_voltage_v`, `cycle_count_offset`, `notes`. **`cycle_count_offset` is required before live cycles can be predicted** (model input `Cycle_number`). |

## Cycles and predictions

| Route | Description |
|---|---|
| `POST /api/battery/cycle` | Post a cycle-level feature vector directly (NASA replay / manual). Fields: `battery_id`, `source` (`nasa_replay`/`manual`), `cycle_number`, the 9 features in snake_case (`current_mean` negative = discharge), optional `ref_soh`, `ref_rul`, `rated_capacity_ah`. Idempotent on `cycle_number`. |
| `GET /api/battery/{id}/cycles` | Detected/replayed cycles with features, `complete`, `completeness_notes` |
| `POST /api/battery/{id}/cycles/aggregate` | Close and store segments now (uses wall-clock time, so a silent device's last segment is closed) |
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
| `GET /api/alerts?battery_id=&status=active\|resolved&limit=` | Alert list, newest first |
| `GET /api/battery/{id}/alerts` | Same, for one battery |
| `POST /api/alerts/{id}/acknowledge` | Mark acknowledged |
| `GET /api/settings/thresholds` | Current thresholds |
| `PUT /api/settings/thresholds` | Replace thresholds (warning ≤ critical ordering is validated) |

**Alert types:**

- `temperature_high`, `temperature_low`
- `voltage_low`, `voltage_high`
- `current_high`
- `temperature_sensor_fault`, `ina219_fault`
- `battery_not_detected`
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
