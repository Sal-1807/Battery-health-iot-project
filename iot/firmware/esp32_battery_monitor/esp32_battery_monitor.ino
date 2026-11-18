/*
 * ESP32 Battery Monitor - INA219 (V/I) + DS18B20 (temperature) -> HTTP JSON
 *
 * Board : ESP32 Dev Module (ESP32 DevKit V1)
 * Libs  : Adafruit INA219, OneWire, DallasTemperature   (Arduino Library Manager)
 *         WiFi, HTTPClient, Wire, time                  (bundled with the ESP32 core)
 *
 * Wiring (as built by the team):
 *   INA219 VCC -> 3V3, GND -> GND, SDA -> GPIO21, SCL -> GPIO22
 *   DS18B20 DATA -> GPIO4 (4.7 kohm pull-up to 3V3), VCC -> 3V3, GND -> GND
 *
 * Sign convention sent to the backend: current_a > 0 while the cell DISCHARGES,
 * i.e. cell + -> INA219 VIN+, INA219 VIN- -> load. If your readings come out
 * negative during discharge, VIN+/VIN- are swapped.
 *
 * voltage_v is the INA219 "load voltage" = bus voltage (VIN- to GND) + shunt drop,
 * i.e. the voltage at VIN+ relative to ESP32 GND. It is only the cell voltage when
 * the cell's negative terminal shares ground with the ESP32/INA219.
 *
 * This firmware ONLY MEASURES. It does not switch the load, does not cut off at
 * low voltage, and does not control charging. Those protections must exist in
 * hardware before a cell is connected to a load.
 *
 * Setup: copy config.example.h to config.h and edit Wi-Fi + backend IP.
 * Without Wi-Fi: set TRANSPORT_WIFI 0 in config.h and run iot/bridge/serial_bridge.py.
 */

#if __has_include("config.h")
#include "config.h"
#else
#error "config.h not found: copy config.example.h to config.h and fill in your Wi-Fi and backend settings."
#endif

#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <Wire.h>
#include <Adafruit_INA219.h>
#include <OneWire.h>
#include <DallasTemperature.h>
#include <sys/time.h>
#include <time.h>

#define FIRMWARE_VERSION "1.0.0"
#define I2C_SDA 21
#define I2C_SCL 22
#define INA219_ADDR 0x40
#define VALID_EPOCH 1700000000L   // anything earlier means NTP has not synced
#define BUFFER_SLOTS 60
#define PAYLOAD_MAX 448
// Transport: 1 = HTTP over Wi-Fi (default), 0 = JSON lines over USB serial,
// forwarded to the backend by iot/bridge/serial_bridge.py on the PC.
#ifndef TRANSPORT_WIFI
#define TRANSPORT_WIFI 1
#endif
#define USB_JSON_PREFIX "@BHM "
// Serial-log hints only (never sent as a presence claim; see takeAndSendReading).
#ifndef CELL_HINT_MIN_V
#define CELL_HINT_MIN_V 2.0f
#endif
#ifndef CELL_HINT_MAX_V
#define CELL_HINT_MAX_V 4.5f
#endif
#ifndef CELL_HINT_CURRENT_A
#define CELL_HINT_CURRENT_A 0.010f
#endif
// A WPA2 join + DHCP commonly takes 3-8 s. Never restart an attempt sooner than this.
#ifndef WIFI_CONNECT_TIMEOUT_MS
#define WIFI_CONNECT_TIMEOUT_MS 15000UL
#endif

Adafruit_INA219 ina219(INA219_ADDR);
OneWire oneWire(ONE_WIRE_PIN);
DallasTemperature ds18b20(&oneWire);

static bool inaOk = false;
static bool dsFound = false;
static DeviceAddress dsAddr;
static char bootId[13];
static uint32_t seqNo = 0;
static unsigned long lastReadMs = 0;
static unsigned long lastSensorRetryMs = 0;
static unsigned long wifiNextAttemptMs = 0;
static unsigned long wifiBackoffMs = WIFI_CONNECT_TIMEOUT_MS;
static unsigned long httpBackoffUntilMs = 0;
static unsigned long httpBackoffMs = 0;
static bool timeSynced = false;
static bool ntpConfigured = false;

// Ring buffer of readings that failed to send. Only readings with a real UTC
// timestamp are buffered: a server-stamped reading sent later would get the
// wrong time.
static char outbox[BUFFER_SLOTS][PAYLOAD_MAX];
static int outHead = 0, outCount = 0;

// ------------------------------------------------------------------ helpers
static void logf(const char *fmt, ...) {
  char buf[256];
  va_list ap;
  va_start(ap, fmt);
  vsnprintf(buf, sizeof(buf), fmt, ap);
  va_end(ap);
  Serial.printf("[%10lu] %s\n", millis(), buf);
}

static bool i2cPresent(uint8_t addr) {
  Wire.beginTransmission(addr);
  return Wire.endTransmission() == 0;
}

static void initIna219() {
  inaOk = i2cPresent(INA219_ADDR) && ina219.begin();
  if (!inaOk) {
    logf("INA219: NOT FOUND at 0x%02X - check SDA=GPIO%d SCL=GPIO%d, 3V3, GND", INA219_ADDR, I2C_SDA, I2C_SCL);
    return;
  }
#if INA219_CALIBRATION == 1
  ina219.setCalibration_32V_1A();
#elif INA219_CALIBRATION == 2
  ina219.setCalibration_16V_400mA();
#else
  ina219.setCalibration_32V_2A();
#endif
  logf("INA219: ok (calibration %d)", INA219_CALIBRATION);
}

static void initDs18b20() {
  ds18b20.begin();
  dsFound = ds18b20.getDeviceCount() > 0 && ds18b20.getAddress(dsAddr, 0);
  if (dsFound) {
    ds18b20.setResolution(dsAddr, DS18B20_RESOLUTION_BITS);
    ds18b20.setWaitForConversion(true);
    logf("DS18B20: ok (%d device(s))", ds18b20.getDeviceCount());
  } else {
    logf("DS18B20: NOT FOUND on GPIO%d - check DATA wiring and 4.7k pull-up to 3V3", ONE_WIRE_PIN);
  }
}

static void makeBootId() {
  uint32_t a = esp_random(), b = esp_random();
  snprintf(bootId, sizeof(bootId), "%08lx%04lx", (unsigned long)a, (unsigned long)(b & 0xFFFF));
}

static bool utcTimestamp(char *out, size_t n) {
  struct timeval tv;
  gettimeofday(&tv, nullptr);
  if (tv.tv_sec < VALID_EPOCH) return false;
  struct tm t;
  gmtime_r(&tv.tv_sec, &t);
  snprintf(out, n, "%04d-%02d-%02dT%02d:%02d:%02d.%03ldZ", t.tm_year + 1900, t.tm_mon + 1, t.tm_mday,
           t.tm_hour, t.tm_min, t.tm_sec, (long)(tv.tv_usec / 1000));
  return true;
}

// Append ',"key":<value>' or ',"key":null'
static void jsonNum(char *buf, size_t n, const char *key, bool valid, float v, int decimals) {
  size_t len = strlen(buf);
  if (valid && isfinite(v)) snprintf(buf + len, n - len, ",\"%s\":%.*f", key, decimals, v);
  else snprintf(buf + len, n - len, ",\"%s\":null", key);
}

// ------------------------------------------------------------------ Wi-Fi
static const char *wifiStatusName(int st) {
  switch (st) {
    case WL_NO_SSID_AVAIL: return "network not found (wrong SSID, out of range, or 5 GHz only)";
    case WL_CONNECT_FAILED: return "connect failed (wrong password or enterprise/WPA2-Enterprise login)";
    case WL_CONNECTION_LOST: return "connection lost";
    case WL_DISCONNECTED: return "disconnected / still trying (no answer from access point)";
    case WL_IDLE_STATUS: return "idle";
    default: return "other";
  }
}

static void wifiStart() {
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  wifiNextAttemptMs = millis() + WIFI_CONNECT_TIMEOUT_MS;  // give the first join time to finish
  logf("WiFi: connecting to \"%s\"...", WIFI_SSID);
}

static void wifiMaintain() {
  static bool wasConnected = false;
  bool connected = WiFi.status() == WL_CONNECTED;
  if (connected && !wasConnected) {
    logf("WiFi: connected, IP %s, RSSI %d dBm", WiFi.localIP().toString().c_str(), WiFi.RSSI());
    wifiBackoffMs = WIFI_CONNECT_TIMEOUT_MS;
    if (!ntpConfigured) {
      configTime(0, 0, NTP_SERVER_1, NTP_SERVER_2);  // UTC, no DST
      ntpConfigured = true;
      logf("NTP: sync requested");
    }
  } else if (!connected && wasConnected) {
    logf("WiFi: connection lost");
    wifiBackoffMs = WIFI_CONNECT_TIMEOUT_MS;
    wifiNextAttemptMs = millis() + wifiBackoffMs;
  }
  wasConnected = connected;

  if (!connected && (long)(millis() - wifiNextAttemptMs) >= 0) {
    int st = WiFi.status();
    logf("WiFi: not connected - status %d: %s", st, wifiStatusName(st));
    logf("WiFi: reconnect attempt (next in %lu ms)", wifiBackoffMs);
    WiFi.disconnect();
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    wifiNextAttemptMs = millis() + wifiBackoffMs;
    wifiBackoffMs = min(wifiBackoffMs * 2, (unsigned long)WIFI_RETRY_MAX_MS);
  }

  if (!timeSynced && time(nullptr) >= VALID_EPOCH) {
    timeSynced = true;
    char ts[32];
    utcTimestamp(ts, sizeof(ts));
    logf("NTP: synced, UTC %s", ts);
  }
}

// ------------------------------------------------------------------ HTTP
// Returns the HTTP status (negative = transport error).
static int postJson(const char *path, const char *body) {
  if (WiFi.status() != WL_CONNECTED) return -100;
  HTTPClient http;
  String url = String(BACKEND_BASE_URL) + path;
  if (!http.begin(url)) return -101;
  http.setTimeout(HTTP_TIMEOUT_MS);
  http.addHeader("Content-Type", "application/json");
  int code = http.POST((uint8_t *)body, strlen(body));
  if (code != 200) {
    String resp = code > 0 ? http.getString() : http.errorToString(code);
    logf("HTTP %d: %s", code, resp.substring(0, 180).c_str());
  }
  http.end();
  return code;
}

static bool retryable(int code) { return code < 0 || code >= 500 || code == 408 || code == 429; }

static void outboxPush(const char *payload) {
  int idx = (outHead + outCount) % BUFFER_SLOTS;
  if (outCount == BUFFER_SLOTS) {          // full: drop the oldest
    outHead = (outHead + 1) % BUFFER_SLOTS;
    outCount--;
    logf("buffer full: oldest reading dropped");
  }
  strncpy(outbox[idx], payload, PAYLOAD_MAX - 1);
  outbox[idx][PAYLOAD_MAX - 1] = '\0';
  outCount++;
}

static void outboxFlush(int maxSends) {
  while (outCount > 0 && maxSends-- > 0) {
    int code = postJson("/api/battery/reading", outbox[outHead]);
    if (retryable(code)) return;           // keep it, try later
    if (code != 200) logf("buffered reading rejected (HTTP %d), discarded", code);
    outHead = (outHead + 1) % BUFFER_SLOTS;
    outCount--;
  }
}

// ------------------------------------------------------------------ measurement
static void takeAndSendReading() {
  // Retry missing sensors every 10 s (hot-plug / loose wire recovery).
  if ((!inaOk || !dsFound) && millis() - lastSensorRetryMs > 10000) {
    lastSensorRetryMs = millis();
    if (!inaOk) initIna219();
    if (!dsFound) initDs18b20();
  }

  // ---- INA219
  const char *inaStatus = "not_found";
  bool vOk = false, iOk = false, pOk = false;
  float loadV = NAN, currentA = NAN, powerW = NAN;
  if (inaOk) {
    if (!i2cPresent(INA219_ADDR)) {
      inaOk = false;
      inaStatus = "error";
      logf("INA219: stopped responding");
    } else {
      inaStatus = "ok";
      float busV = ina219.getBusVoltage_V();
      float shuntMv = ina219.getShuntVoltage_mV();
      float currentMa = ina219.getCurrent_mA();
      float powerMw = ina219.getPower_mW();
      loadV = busV + shuntMv / 1000.0f;
      currentA = currentMa / 1000.0f;
      powerW = powerMw / 1000.0f;
      vOk = iOk = pOk = true;
    }
  }
  // The INA219 cannot tell an open (floating) input from a resting cell: both
  // show ~0 A, and the open input can float to ~0.7-1.1 V or more. The firmware
  // therefore makes NO presence claim (battery_connected is sent as null); the
  // backend decides from sustained current or an operator confirmation.
  // The hint below is for the Serial log only.
  const char *inputHint = "?";
  if (vOk && iOk) {
    if (fabsf(currentA) >= CELL_HINT_CURRENT_A) inputHint = "current flowing";
    else if (loadV < CELL_HINT_MIN_V || loadV > CELL_HINT_MAX_V) inputHint = "outside Li-ion range (no cell / floating)";
    else inputHint = "in Li-ion range, unverified at rest";
  }

  // ---- DS18B20
  const char *dsStatus = "not_found";
  bool tOk = false;
  float tempC = NAN;
  if (dsFound) {
    ds18b20.requestTemperaturesByAddress(dsAddr);
    tempC = ds18b20.getTempC(dsAddr);
    if (tempC == DEVICE_DISCONNECTED_C) {
      dsStatus = "disconnected";
      dsFound = false;  // re-scan on next retry
    } else if (tempC == 85.0f) {
      dsStatus = "error";  // power-on reset value: conversion did not complete
    } else {
      dsStatus = "ok";
      tOk = true;
    }
  }

  // ---- JSON
  char ts[32];
  bool haveTime = utcTimestamp(ts, sizeof(ts));
  char body[PAYLOAD_MAX];
  snprintf(body, sizeof(body), "{\"battery_id\":\"%s\",\"device_id\":\"%s\",\"source\":\"hardware\"",
           BATTERY_ID, DEVICE_ID);
  if (haveTime) {
    size_t len = strlen(body);
    snprintf(body + len, sizeof(body) - len, ",\"timestamp\":\"%s\"", ts);
  }
  jsonNum(body, sizeof(body), "voltage_v", vOk, loadV, 4);
  jsonNum(body, sizeof(body), "current_a", iOk, currentA, 5);
  jsonNum(body, sizeof(body), "power_w", pOk, fabsf(powerW), 5);
  jsonNum(body, sizeof(body), "temperature_c", tOk, tempC, 4);
  size_t len = strlen(body);
  snprintf(body + len, sizeof(body) - len,
           ",\"seq\":%lu,\"boot_id\":\"%s\",\"uptime_ms\":%lu,\"rssi_dbm\":%d,\"firmware_version\":\"%s\","
           "\"sensor_status\":{\"ina219\":\"%s\",\"ds18b20\":\"%s\",\"battery_connected\":%s}}",
           (unsigned long)seqNo, bootId, millis(),
           (TRANSPORT_WIFI && WiFi.status() == WL_CONNECTED) ? WiFi.RSSI() : -127,
           FIRMWARE_VERSION, inaStatus, dsStatus, "null");
  seqNo++;

  Serial.printf("[%10lu] #%lu V=%s I=%s T=%s | INA219=%s DS18B20=%s input=%s time=%s\n", millis(),
                (unsigned long)(seqNo - 1),
                vOk ? String(loadV, 3).c_str() : "--", iOk ? String(currentA, 4).c_str() : "--",
                tOk ? String(tempC, 2).c_str() : "--", inaStatus, dsStatus,
                inputHint, haveTime ? ts : "unsynced");

#if !TRANSPORT_WIFI
  // USB transport: one machine-readable line per reading; the PC bridge adds
  // the UTC timestamp and posts it. Human-readable lines above are ignored by it.
  Serial.print(USB_JSON_PREFIX);
  Serial.println(body);
  return;
#endif

  // ---- send (with backoff after transport/server errors)
  if ((long)(millis() - httpBackoffUntilMs) < 0) {
    if (haveTime) outboxPush(body);
    return;
  }
  int code = postJson("/api/battery/reading", body);
  if (code == 200) {
    httpBackoffMs = 0;
    outboxFlush(5);
  } else if (retryable(code)) {
    if (haveTime) outboxPush(body);
    httpBackoffMs = httpBackoffMs ? min(httpBackoffMs * 2, 30000UL) : 2000UL;
    httpBackoffUntilMs = millis() + httpBackoffMs;
    logf("send failed, backing off %lu ms (%d buffered)", httpBackoffMs, outCount);
  }
  // 4xx (except 408/429): the backend rejected the data itself; resending would not help.
}

// ------------------------------------------------------------------ Arduino
void setup() {
  Serial.begin(115200);
  delay(300);
  Serial.println();
  logf("ESP32 battery monitor %s  battery=%s device=%s", FIRMWARE_VERSION, BATTERY_ID, DEVICE_ID);
#if TRANSPORT_WIFI
  logf("transport: Wi-Fi -> %s", BACKEND_BASE_URL);
#else
  logf("transport: USB serial (run iot/bridge/serial_bridge.py on the PC; close the Serial Monitor first)");
#endif
  makeBootId();
  logf("boot_id: %s", bootId);

  Wire.begin(I2C_SDA, I2C_SCL);
  initIna219();
  initDs18b20();
#if TRANSPORT_WIFI
  wifiStart();
#else
  WiFi.mode(WIFI_OFF);
#endif
}

void loop() {
#if TRANSPORT_WIFI
  wifiMaintain();
#endif
  if (millis() - lastReadMs >= READ_INTERVAL_MS) {
    lastReadMs = millis();
    takeAndSendReading();
  }
  delay(10);
}
