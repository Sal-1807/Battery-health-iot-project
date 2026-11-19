# Pre-hardware software readiness audit — 2026-11-19

Scope: software path from ESP32 sensors to dashboard. No physical discharge was run, and no production data was touched; every probe used throwaway databases.

Legend:

- **PASS:** verified by tests or probes in this audit.
- **WARNING:** works, but has a known limitation the operator must manage.
- **BLOCKED:** must be resolved before a physical discharge test.

## Defects found and fixed in this audit

| # | Defect (reproduced first) | Fix |
|---|---|---|
| 1 | `BHM_MODE=simulation` without `BHM_DB_PATH` fell back to the **production** database and seeded SIM profiles into it | Refuses to start |
| 2 | A simulation database opened in production mode accepted real ESP32 telemetry (and vice versa) | Each database is bound to its mode (`settings.db_mode`); a mismatch refuses to start. Existing databases are inferred: any hardware reading → production. |
| 3 | The legacy hardware ID `CELL-18650-01`, where readings went before profiles were used, could receive an "experimental" NASA SoH/RUL | No SoH/RUL for **any** hardware battery. *Revised for the final submission:* hardware batteries may show only a gate-passing **Experimental ML Estimate**; archived IDs are excluded from the predictions view. |
| 4 | After a battery switch, the switched-away battery and the legacy ID got permanent false **device_offline** warnings | Offline alerts are evaluated only for the battery the device is currently recording to |
| 5 | An unselected profile showed **Offline** | New state **Not selected as connected** |
| 6 | Test Manager said **"no load current measured"** while −0.37 A flowed (reversed INA219 wiring) | Negative current is reported, with a hint to check VIN+/VIN− |
| 7 | Selecting a battery for a device that has never reported gave no warning (DEVICE_ID mismatch would silently send data elsewhere) | Warning on the Batteries page |

## Checklist

| Area | Status | Evidence / note |
|---|---|---|
| Sensor → bridge → API → DB → dashboard path | PASS | Real uvicorn plus the bridge over HTTP; 52/52 simulation checks through the real ingest API |
| Units (V, A, °C, W); mA/mV sent as A/V rejected, never rescaled | PASS | `test_api` validation tests |
| Current direction (+ = discharge); negative never counted as discharge | PASS / WARNING | Software verified. **Not yet confirmed on hardware** (HARDWARE_SETUP §6 check 11). |
| Power: device register or V·\|I\|; inconsistency flagged | PASS | |
| Capacity integration (1 A × 1 h = 1000 mAh; 4 V → 4.000 Wh) | PASS | Simulation S1 |
| Telemetry gaps: never filled; > 5 min ends the session | PASS | Simulation S4, session tests |
| Duplicate and out-of-order readings | PASS | Simulation S1 |
| Battery identity: time-ranged assignment; buffered readings follow their own timestamp | PASS | `test_multibattery` |
| Assignment vs device clock | WARNING | Assignment time is the server clock and reading time is the device/PC clock. In USB mode both are the PC clock. Over Wi-Fi with NTP, skew is usually under 1 s, but swap cells only while the load is off. |
| Floating input / no cell / sensor fault → no misleading health output | PASS | `test_presence`, real-bridge probe |
| Complete vs partial cycles; EFC per own rating (3000 / 2500) | PASS | Simulation S1–S3, `test_aging`, `test_sessions` |
| SoH/RUL: NASA demo separate; none for physical or simulated profiles | PASS | Leave-one-cell-out page; fix 3 |
| Test Manager makes no hardware-control claims; disconnect warning | PASS | Simulation S5, fix 6 |
| Simulation isolation (both directions) | PASS | Fixes 1–2, simulation S6 |
| Dashboard: no battery, missing sensors, empty histories | PASS | Dashboard tests (39) |
| Firmware compiles on a real ESP32 toolchain | WARNING | The current sketch (with the presence-hint change) has only been syntax-checked here. The previous USB-mode build was flashed and ran on the team's board. |
| Voltage accuracy vs multimeter | WARNING | Not yet done (HARDWARE_SETUP §6 check 10) |
| API authentication | WARNING | None: trusted lab LAN only. Run a single uvicorn worker. |
| Charging measurement | WARNING | Not instrumented. "Completed cycle" infers the charge from a rested full voltage. |
| **Cell protection, hardware voltage cutoff, fuse, load mounting, supervision** | **BLOCKED** | Required before any physical discharge (HARDWARE_SETUP §5). The software cannot provide them. |

## Commands used

```bash
pytest iot/tests -q
```
```bash
cd dashboard && npm test && npm run build
```
```bash
python -m iot.simulation.run
```
