# Hardware setup — ESP32 + INA219 + DS18B20

> **Scope.** This document covers the *sensor* wiring and firmware, which the team has already
> bench-tested. It deliberately does **not** give a procedure for discharging the 18650 cell.
> That needs protection hardware the project does not yet have (§5) and must be planned and
> supervised with the professor.

## 1. Components

| Part | Role |
|---|---|
| ESP32 DevKit V1 | Wi-Fi microcontroller |
| INA219 breakout (0.1 Ω shunt) | Bus voltage, shunt voltage → current, power (I²C 0x40) |
| DS18B20 waterproof probe + 4.7 kΩ resistor | Cell surface temperature (1-Wire) |
| 3.7 V 3000 mAh 18650 Li-ion cell, holder | Device under test (protection status **unknown**) |
| 10 Ω 5 W resistor | Candidate load (see §5 before use) |
| Multimeter | Cross-check readings |

## 2. Sensor wiring (as built and tested by the team)

```
INA219  VCC  → ESP32 3V3          DS18B20  VCC (red)    → ESP32 3V3
INA219  GND  → ESP32 GND          DS18B20  GND (black)  → ESP32 GND
INA219  SDA  → GPIO21             DS18B20  DATA (yellow)→ GPIO4
INA219  SCL  → GPIO22             4.7 kΩ between DATA and 3V3
```

### How the INA219 sees the cell

The INA219 measures current through its shunt between **VIN+** and **VIN−**, and *bus voltage* from VIN− to its GND.

- The firmware reports `voltage_v` = bus voltage + shunt drop, i.e. the voltage at **VIN+ relative to ESP32 GND**.
- That equals the cell's terminal voltage only when the cell's negative terminal shares ground with the ESP32/INA219.
- Current is reported **positive when it flows VIN+ → VIN−**. With cell+ on VIN+ and the load on VIN−, that is discharge, which is the convention the backend expects. If discharge shows as negative current, VIN+ and VIN− are swapped.
- With nothing connected to VIN+/VIN−, the inputs float and read near 0 V. The firmware and backend flag any reading below 1.0 V as "no cell detected" and never treat it as battery data.

## 3. Firmware

Location: `iot/firmware/esp32_battery_monitor/`

1. Arduino IDE → Boards Manager → install **esp32 by Espressif Systems**. Board: **ESP32 Dev Module**.
2. Library Manager → install **Adafruit INA219**, **OneWire**, **DallasTemperature** (Adafruit BusIO is pulled in automatically).
3. Copy `config.example.h` to `config.h` in the same folder. Set:
   - `WIFI_SSID` / `WIFI_PASSWORD`: a **2.4 GHz** network.
   - `BACKEND_BASE_URL`: `http://<LAN IP of the PC running the backend>:8000`. Not `localhost`, which on the ESP32 means the ESP32 itself.
   - `BATTERY_ID`, `DEVICE_ID`.
   - `INA219_CALIBRATION`: `1` (32 V / 1 A) is the default. `2` (16 V / 400 mA) clips above 0.4 A; a 10 Ω load on a full cell draws ≈ 0.42 A.

   `config.h` is git-ignored. Never commit it.
4. Upload at 115200 baud and open the Serial Monitor.

What the firmware does:

- reads the sensors every `READ_INTERVAL_MS` (default 2 s);
- syncs UTC via NTP;
- POSTs JSON to `/api/battery/reading` with a per-boot `seq` and `boot_id`, so retries are de-duplicated server-side;
- backs off on network or server errors and reconnects Wi-Fi with exponential backoff;
- buffers up to 60 timestamped readings in RAM while the backend is unreachable;
- re-detects sensors that were unplugged.

It **only measures**: it never switches a load, never cuts off at low voltage and never controls charging.

## 2a. What the INA219 can and cannot tell about cell presence

With no cell attached, VIN+/VIN− float. On the team's board the open input read **0.72–1.14 V** and drifted between those values. Nothing guarantees it stays below any particular voltage. A connected cell at rest draws ~0 A, exactly like an open input.

| Evidence | Proves | Does not prove |
|---|---|---|
| Voltage outside 2.0–4.5 V at ~0 A | No usable cell measurement | Whether it is a floating input or a **deeply discharged cell**; they look the same |
| Voltage inside 2.0–4.5 V at ~0 A | nothing | That a cell is connected (an open input could float there) |
| ≥ 10 mA through the shunt on two consecutive readings | A closed circuit with a source | That the source is *the cell* (a miswired supply looks the same) |
| Operator confirmation | A person checked with a meter | — |

How each layer uses this:

- **Firmware:** makes no presence claim (`battery_connected: null`); its Serial log only gives a hint.
- **Backend** (`iot/backend/presence.py`): gives every reading a `cell_state`:
  - `no_cell`, `unverified` or `unknown`: never treated as cell voltage. No voltage alerts and no charting, and the dashboard shows **No cell** or **Unverified**.
  - `verified_current` or `confirmed`: treated as cell voltage. Voltage alerts use the unchanged thresholds.
- **Operator confirmation:** to use a resting cell's voltage, confirm it on the Overview page after checking it with a multimeter. The confirmation lapses automatically when the voltage leaves the Li-ion range or readings stop for 10 minutes.

Trade-off, stated plainly: a resting cell that nobody has confirmed gets **no voltage alerts**. Under load this can't happen, because current verifies presence. A cell pulled below 2.0 V *under load* is still recognised and raises the critical alert.

**Hardware upgrades that would give better evidence** (not implemented or tested):

1. **A pull-down resistor from VIN+ to GND**, so an open input reads near 0 V instead of floating. Choose the value with the INA219 datasheet's input bias current and the cell's self-drain in mind, then **measure the open-input reading** to confirm it actually drops. Only after that measurement should the backend be changed to trust in-range resting voltages.
2. **A firmware-switched test load**: a MOSFET plus resistor that briefly draws a known current, which creates current evidence on demand. Like any load on the cell, this needs the protection in §5.
3. **A mechanical presence switch** in the battery holder.

## 3a. Without Wi-Fi: USB serial bridge

Use this when the ESP32 cannot join a network. For example, campus Wi-Fi that uses
**WPA2-Enterprise / EAP** (such as `SRMIST`) needs a per-user login this firmware does not support.

1. In `config.h` set `#define TRANSPORT_WIFI  0` and upload. The Wi-Fi settings are then ignored, and the radio is turned off.
2. **Close the Arduino IDE Serial Monitor**: only one program can open the COM port.
3. On the PC, from the repository root, with the backend running:
   ```bash
   pip install pyserial
   python -m iot.bridge.serial_bridge --port COM9
   ```
   Use your board's port: Arduino IDE → Tools → Port, or Windows Device Manager → Ports.
4. The bridge echoes the ESP32 log and forwards each `@BHM {...}` line to `/api/battery/reading`.

How it behaves:

- Timestamps come from the PC clock, because the ESP32 has no NTP without Wi-Fi.
- If the backend is down, the bridge queues up to 5000 readings and re-sends them in order. Duplicates are ignored server-side via `boot_id` + `seq`.
- The data is still the ESP32's real sensor data (`source = hardware`). Only the transport differs.

To return to Wi-Fi later, set `TRANSPORT_WIFI 1` and re-upload.

## 4. Network setup

1. Run the backend on the PC with `--host 0.0.0.0` (README §Quick start).
2. Find the PC's LAN IP: `ipconfig` (Windows), `ip addr` (Linux), `ipconfig getifaddr en0` (macOS).
3. Allow inbound TCP 8000 in the PC firewall. Windows asks the first time Python listens; allow it on *Private* networks.
4. From a phone on the same Wi-Fi, open `http://<PC-IP>:8000/api/health`. If that fails, the ESP32 will fail too.
5. Some campus or guest networks isolate clients from each other. In that case, use a phone hotspot or a lab router.

## 5. Before any battery discharge — required, not optional

The project has not validated a discharge setup, and the cell's protection status is unknown. Do not connect the cell to a load until all of the following exist and have been reviewed with the professor:

- **Cell protection:** a protected cell, or an external protection/BMS board providing over-discharge, over-current and short-circuit protection.
- **Hardware voltage cutoff:** something that physically disconnects the load at the manufacturer's minimum voltage. The backend's low-voltage *alert* is not a cutoff.
- **Load power and thermal handling:**
  - 10 Ω at 4.2 V dissipates ≈ 1.8 W, so the 5 W resistor will get hot.
  - Mount it off the breadboard on a non-flammable surface, away from the cell and the DS18B20.
  - Breadboard contacts are not rated for sustained current.
- **Fusing** in series with the cell, and a manual disconnect within reach.
- **Supervision:** never unattended. Use a fire-safe surface or container, and set an over-temperature stop criterion.
- **Charging** only with a proper Li-ion CC/CV charger IC/module, never from a bench supply or by improvisation.

Also note that a 10 Ω load (≈ 0.37 A) differs completely from the NASA ≈ 2 A test profile. Data recorded this way will not be in the model's input range (see PROJECT_STATUS.md §5).

## 6. Device-side checks the team must perform (not verifiable from software)

| # | Check | Expected |
|---|---|---|
| 1 | Sketch compiles and uploads | No errors; Serial shows `INA219: ok` and `DS18B20: ok` |
| 2 | Wi-Fi + NTP | `WiFi: connected, IP …`, then `NTP: synced, UTC …` |
| 3 | Backend receives data | Dashboard → System shows the device **Online**, origin "Hardware (ESP32)" |
| 4 | No cell on INA219 inputs | Serial `input=outside Li-ion range (no cell / floating)`; dashboard voltage **No cell**, info alert "No cell measurement", no low-voltage alert, voltage not plotted |
| 5 | Temperature | DS18B20 reading within ~0.5 °C of a reference thermometer; hand-warming raises it |
| 6 | Unplug DS18B20 DATA | Serial `DS18B20: disconnected` / not found; dashboard "Temperature sensor fault" alert; recovers within ~10 s of re-plugging |
| 7 | Unplug INA219 SDA | `INA219: stopped responding`; voltage/current shown as unavailable; recovers after re-plugging |
| 8 | Stop the backend for 30 s, restart | Firmware logs backoff and buffering; buffered readings appear afterwards (no duplicates) |
| 9 | Turn Wi-Fi off/on | Firmware reconnects with backoff without a reboot |
| 10 | Voltage cross-check (only once §5 is satisfied and a cell is connected) | INA219 voltage within ±20 mV of the multimeter at the cell terminals |
| 11 | Current sign (same condition) | Positive during discharge |
