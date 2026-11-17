# Architecture

```
 ┌──────────────────────┐   HTTP POST JSON (Wi-Fi, LAN)   ┌───────────────────────────────────────────┐
 │ ESP32 DevKit V1      │ ──────────────────────────────▶ │ FastAPI backend  (iot/backend)            │
 │  INA219  V, I  (I²C) │   /api/battery/reading          │                                           │
 │  DS18B20 T  (1-Wire) │   every 2 s, seq + boot_id,     │  telemetry.py  validate → normalise →     │
 │  NTP → UTC timestamp │   retry + RAM buffer            │                store reading → alerts     │
 └──────────────────────┘                                 │  cycles.py     reading stream → discharge │
                                                          │                segments → features        │
 ┌──────────────────────┐   same JSON, source=simulation  │  features.py   NASA-identical statistics  │
 │ simulate_esp32.py    │ ──────────────────────────────▶ │  model_service gate → scaler → RF models  │
 │  telemetry (synthetic)│                                │                (src/train_models.py logic)│
 │  nasa-replay (real   │ ── /api/battery/cycle ────────▶ │  alerts.py     thresholds, dedupe, resolve│
 │   NASA feature rows) │                                 │  nasa.py       committed CSV results      │
 └──────────────────────┘                                 │  db.py         SQLite (WAL)               │
                                                          └───────────────┬───────────────────────────┘
                                                                          │ JSON (polling, /api proxy)
                                                          ┌───────────────▼───────────────────────────┐
                                                          │ Dashboard  (dashboard/, React + Vite + TS │
                                                          │  + Tailwind + Recharts)                   │
                                                          │  Overview · Live · Battery health ·       │
                                                          │  History · Alerts · System                │
                                                          └───────────────────────────────────────────┘

 Offline ML pipeline (unchanged):  NASA .mat → src/extract_battery_data.py → src/build_features.py
                                   → data/processed/battery_health_features.csv → src/train_models.py
                                   → models/*.pkl + results/lobo_*.csv
```

## Module boundaries

| Path | Owner concern | Depends on |
|---|---|---|
| `src/` | Offline data + training (existing team code, unchanged) | NASA raw data |
| `iot/backend/features.py` | Feature definitions for live data | numpy only |
| `iot/backend/model_service.py` | Load models, gate and run inference | `models/`, `src/train_models.py` (imports `predict_rul_twostage`, `FEATURES`) |
| `iot/backend/cycles.py` | Segmentation, completeness, persist cycles/predictions | features, model_service |
| `iot/backend/telemetry.py` | Ingest + read queries | cycles, alerts, db |
| `iot/backend/alerts.py` | Threshold rules | db |
| `iot/backend/db.py` | **All SQL / storage** | sqlite3 |
| `iot/backend/api.py` | HTTP routes only | everything above |
| `iot/simulator/` | Demo data sources | stdlib only |
| `iot/firmware/` | ESP32 sketch | Arduino libs |
| `dashboard/` | UI | backend HTTP API only |

## Data model (SQLite, `iot/data/telemetry.db`)

- `batteries`: one row per physical/simulated/replayed cell. `source` is fixed at first contact (`hardware` | `simulation` | `nasa_replay`); mixing sources on one ID is refused (HTTP 409).
- `devices`: one row per ESP32: last seen, RSSI, firmware, last sensor status.
- `readings`: raw time series. Unique keys `(device_id, boot_id, seq)` and `(battery_id, device_id, source, ts_ms)` make retries idempotent.
- `cycles`: one row per detected discharge segment (or replayed NASA cycle). Holds the 9 measured features, completeness flag and notes, and `cycle_number`.
- `predictions`: every inference attempt, *including refusals*, with status, reasons, out-of-range features, input vector and model version hash.
- `alerts`: one active row per (battery, type); auto-resolved.
- `settings`: alert thresholds (JSON).

**Migration path:** all SQL is in `db.py` and uses portable types (TEXT/REAL/INTEGER). Timestamps are stored as ISO-8601 UTC text and integer epoch-ms. Moving to PostgreSQL/TimescaleDB means re-implementing `Database` and replacing `INSERT … ON CONFLICT DO NOTHING` (already supported by PostgreSQL). Schema versions are tracked in `schema_version`; add new migrations to `_MIGRATIONS`.

## Cycle aggregation rules

A *discharge segment*:

- starts at the first reading with `current_a ≥ 0.05 A` and a detected cell;
- continues while readings keep discharging and no gap exceeds 60 s;
- keeps up to 120 s of rest readings after the load stops (NASA records include this relaxation tail);
- closes when the tail window has passed in *data time* or the device falls silent.

It is **complete** only if all of these hold:

- ≥ 30 readings over ≥ 10 min;
- reached the cell's cutoff voltage (default 3.0 V, per battery configurable);
- ended because the load stopped (not because of data loss);
- ≥ 95 % voltage/current and ≥ 90 % temperature coverage;
- has a temperature at the start.

Incomplete segments are stored with notes but never predicted on. Every threshold is configurable through `BHM_*` environment variables (`iot/backend/config.py`).

## Prediction gate

A prediction is produced only if the cycle is complete, all 10 features are present (nothing imputed), and — for live data — every feature lies within the NASA training range ± 10 % of that range.

| Status | When | Meaning |
|---|---|---|
| `unavailable` | any gate fails | no number; reasons and out-of-range features stored |
| `experimental` | live/simulated cycle passed every gate | model output, not validated on this cell |
| `nasa_in_sample` | NASA replay | the deployed model was trained on this data |

Validated accuracy is only ever shown from `results/lobo_predictions.csv` (held-out).
