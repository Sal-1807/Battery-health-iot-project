// ---------------------------------------------------------------------------
// Copy this file to "config.h" (same folder) and edit it.
// config.h is git-ignored: NEVER commit your real Wi-Fi password.
// ---------------------------------------------------------------------------
#pragma once

// ---- Wi-Fi (2.4 GHz only; the ESP32 cannot join 5 GHz networks) ----
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
// Below this input voltage the INA219 is considered to have no cell attached
// (floating/disconnected input). Such readings are flagged, not used as battery data.
#define BATTERY_DETECT_MIN_V  1.0f

// ---- DS18B20 ----
#define ONE_WIRE_PIN  4
#define DS18B20_RESOLUTION_BITS 12   // 0.0625 degC, ~750 ms conversion

// ---- NTP (UTC timestamps) ----
#define NTP_SERVER_1  "pool.ntp.org"
#define NTP_SERVER_2  "time.google.com"
