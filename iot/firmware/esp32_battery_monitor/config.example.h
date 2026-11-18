// ---------------------------------------------------------------------------
// Copy this file to "config.h" (same folder) and edit it.
// config.h is git-ignored: NEVER commit your real Wi-Fi password.
// ---------------------------------------------------------------------------
#pragma once

// ---- Transport ----
// 1 = send over Wi-Fi (HTTP POST to BACKEND_BASE_URL)
// 0 = print JSON lines over the USB cable; run on the PC:
//       python -m iot.bridge.serial_bridge --port COM9
//     (Wi-Fi settings below are then ignored.)
#define TRANSPORT_WIFI  1

// ---- Wi-Fi (2.4 GHz WPA2/WPA3-Personal only; the ESP32 cannot join 5 GHz
//      networks or WPA2-Enterprise / EAP networks such as many campus Wi-Fis) ----
#define WIFI_SSID      "your-network-name"
#define WIFI_PASSWORD  "your-wifi-password"

// ---- Backend ----
// LAN IP of the computer running uvicorn (NOT localhost - localhost on the ESP32
// is the ESP32 itself). Find it with `ipconfig` (Windows) / `ip addr` (Linux) /
// `ipconfig getifaddr en0` (macOS). Start the backend with --host 0.0.0.0.
#define BACKEND_BASE_URL  "http://192.168.1.50:8000"

// ---- Identity ----
// battery_id: one per physical cell. device_id: one per ESP32 board.
// Allowed characters: A-Z a-z 0-9 _ . -   (max 48)
#define BATTERY_ID  "CELL-18650-01"
#define DEVICE_ID   "esp32-devkit-01"

// ---- Timing ----
#define READ_INTERVAL_MS      2000UL   // one reading every 2 s
#define HTTP_TIMEOUT_MS       5000
#define WIFI_RETRY_MAX_MS     60000UL  // reconnect backoff ceiling

// ---- INA219 ----
// Shunt calibration. The Adafruit-style breakout has a 0.1 ohm shunt.
//   0 = 32 V / 2 A   (default, 0.1 mA nominal resolution)
//   1 = 32 V / 1 A   (finer resolution, enough for a 10 ohm load at 4.2 V = 0.42 A)
//   2 = 16 V / 400 mA (finest; CLIPS above 0.4 A - do not use with a 10 ohm load at full charge)
#define INA219_CALIBRATION  1
// Cell presence is NOT decided on the ESP32. With no cell attached the INA219
// input floats (0.72-1.14 V was observed), and a resting cell draws ~0 A just
// like an open input, so no voltage threshold can prove a cell is present.
// The backend decides from sustained current or an operator confirmation.
// (BATTERY_DETECT_MIN_V from older config.h files is now ignored.)

// ---- DS18B20 ----
#define ONE_WIRE_PIN  4
#define DS18B20_RESOLUTION_BITS 12   // 0.0625 degC, ~750 ms conversion

// ---- NTP (UTC timestamps) ----
#define NTP_SERVER_1  "pool.ntp.org"
#define NTP_SERVER_2  "time.google.com"
