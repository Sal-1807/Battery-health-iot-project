# Simulation and end-to-end verification

No physical battery is needed. Everything here is **synthetic** and is stored in a **separate simulation database**.

## One command

From the repository root:

```bash
python -m iot.simulation.run
```

It prints one line per check and ends with `N checks passed, 0 failed`. It exits non-zero on any failure and writes `iot/data/simulation_report.json`. The run takes about 30 s and posts about 4,300 simulated readings.

To regenerate the dashboard test fixture after changing backend behaviour:

```bash
python -m iot.simulation.run --fixture dashboard/src/__tests__/fixtures/simulation.json
```

The full suites also run the simulation:

```bash
pytest iot/tests -q
```
```bash
cd dashboard && npm test
```

## Viewing the simulated data in the dashboard

On Linux / macOS:

```bash
BHM_MODE=simulation BHM_DB_PATH=iot/data/simulation.db uvicorn iot.backend.main:app --port 8000
```

On Windows (cmd), one line at a time:

```bash
set BHM_MODE=simulation
```
```bash
set BHM_DB_PATH=iot\data\simulation.db
```
```bash
uvicorn iot.backend.main:app --port 8000
```

Open a new terminal (or run `set BHM_MODE=` and `set BHM_DB_PATH=`) to go back to real data.

Then start the dashboard as usual (`cd dashboard && npm run dev`). A **SIMULATION MODE** banner is shown on every page, and simulated profiles carry a **SIMULATED** tag. Restart without `BHM_MODE` to return to real data.

## How isolation is guaranteed

1. **Separate database.** The runner always writes to `iot/data/simulation.db`, which is reset on each run, and refuses the production path. It also hashes the production database before and after the run and checks that it's unchanged.
2. **Separate profiles.** Simulation mode seeds `SIM-BAT-A-3000`, `SIM-BAT-B-3000` and `SIM-BAT-C-2500` with `source = simulation`. Ingest refuses to mix sources on one battery, so simulated readings can never be stored under a real profile.
3. **Simulation mode refuses hardware telemetry** (HTTP 403). A real ESP32 pointed at it cannot mix in.
4. **Profile views** (Batteries, Aging, Test Manager) show only real profiles in production and only SIM profiles in simulation mode.
5. **SoH/RUL** stay unavailable for all physical and simulated profiles.

## What is simulated

**Signals** (`iot/simulation/model.py`):

- **Cell model:** generic Li-ion OCV curve, internal resistance, a set *actual* capacity, and a first-order thermal model.
- **Sensor models:**
  - INA219: 4 mV voltage steps, 0.1 mA current steps and 2 mW power steps, plus noise;
  - DS18B20: 0.0625 °C steps.
- **Reference count:** the cell keeps its own exact coulomb count, used as ground truth.
- **Message format:** readings go out in exactly the JSON shape the USB bridge sends, through `/api/battery/reading`.

**Scenarios and checks** (`iot/simulation/run.py`):

| Scenario | Verifies |
|---|---|
| S1 analytic (SIM-BAT-A) | 1.000 A × 1 h = **1000 mAh**, 4.0 V × 1.0 A × 1 h = **4.000 Wh**. Dropped readings don't create a gap or assumed current; duplicates are ignored; an out-of-order reading isn't re-integrated. A partial discharge isn't a full cycle. EFC = charge / 3000. Completing the software test claims no hardware action. |
| S2 realistic full discharges (SIM-BAT-C) | Integrated charge within 1 % of the cell's true coulomb count; full-range classification; 2 completed cycles; EFC = charge / 2500; Test Manager validity; aging retention = run 2 / run 1 (simulated 2 % fade) |
| S3 partial + battery switch | Partial classification; the B session ends `battery_switched`; post-switch readings go to A; nothing stored under B after the switch |
| S4 interruptions | A 90 s gap recorded and not filled; a 10 min silence ends the session; DS18B20 dropouts and an INA219 fault counted; aging excludes the session with reasons |
| S5 software stop (near real time) | After **Complete**, the response says no hardware was controlled, and the measured hardware state still shows load current |
| S6 isolation | Hardware telemetry refused; real profiles untouched; only SIM profiles listed; no SoH/RUL; production database hash unchanged |
| S7 exports | Tests CSV equals the API values; history CSV row counts equal stored readings |

The simulation verifies the **software**: ingest, integration, classification and display. It doesn't validate the cell model against your physical batteries, and simulated numbers must not be reported as measurements.
