"""USB serial bridge: line parsing, forwarding, retry/queue behaviour (no real port needed)."""

from __future__ import annotations

import json

from iot.bridge.serial_bridge import Bridge, parse_line

# Exactly what the firmware prints in TRANSPORT_WIFI 0 mode (no timestamp, rssi -127).
FW_LINE = ('@BHM {"battery_id":"CELL-18650-01","device_id":"esp32-devkit-01","source":"hardware",'
           '"voltage_v":0.8880,"current_a":-0.00020,"power_w":0.00000,"temperature_c":25.5000,'
           '"seq":%d,"boot_id":"8c94df908e38","uptime_ms":6054,"rssi_dbm":-127,"firmware_version":"1.0.0",'
           '"sensor_status":{"ina219":"ok","ds18b20":"ok","battery_connected":false}}')


def _client_poster(client):
    def post(path, body):
        r = client.post(path, json=body)
        return r.status_code, r.json()
    return post


def test_parse_line_adds_timestamp_and_ignores_log_lines():
    r = parse_line(FW_LINE % 1, now_iso=lambda: "2026-11-17T06:00:00.000Z")
    assert r["timestamp"] == "2026-11-17T06:00:00.000Z" and r["seq"] == 1
    assert parse_line("[  6054] #2 V=0.888 I=-0.0001 T=25.56 | INA219=ok") is None
    assert parse_line("") is None


def test_firmware_line_is_accepted_by_backend_end_to_end(client):
    logs = []
    b = Bridge(_client_poster(client), log=logs.append)
    for k in range(3):
        b.handle_line(FW_LINE % k)
    b.handle_line("[  6054] #2 V=0.888 human-readable line")
    assert b.sent == 3 and b.rejected == 0
    latest = client.get("/api/battery/CELL-18650-01/latest").json()["reading"]
    assert latest["source"] == "hardware" and latest["transport"] == "usb_serial"
    assert latest["timestamp_source"] == "bridge"       # PC clock, not ESP32 NTP
    assert latest["battery_detected"] is False          # floating INA219 input stays flagged
    assert any(l.startswith("[esp32]") for l in logs)


def test_resent_reading_is_a_duplicate_not_a_second_row(client):
    b = Bridge(_client_poster(client), log=lambda _: None)
    b.handle_line(FW_LINE % 5)
    b.handle_line(FW_LINE % 5)  # same boot_id + seq
    assert (b.sent, b.duplicates) == (1, 1)
    assert client.get("/api/battery/CELL-18650-01/history").json()["total"] == 1


def test_backend_down_queues_then_flushes_in_order(client):
    real = _client_poster(client)
    state = {"down": True}

    def flaky(path, body):
        if state["down"]:
            raise ConnectionRefusedError("backend down")
        return real(path, body)

    b = Bridge(flaky, log=lambda _: None)
    for k in range(4):
        b.handle_line(FW_LINE % k, now=0.0)
    assert len(b.pending) == 4 and b.sent == 0
    state["down"] = False
    b.handle_line("", now=100.0)   # idle serial line: queued readings are retried anyway
    assert b.sent == 4 and not b.pending
    assert client.get("/api/battery/CELL-18650-01/history").json()["total"] == 4


def test_invalid_reading_is_dropped_not_retried(client):
    bad = json.loads((FW_LINE % 9)[len("@BHM "):])
    bad["current_a"] = 370.0  # mA sent as A
    b = Bridge(_client_poster(client), log=lambda _: None)
    b.handle_line("@BHM " + json.dumps(bad))
    assert b.rejected == 1 and not b.pending


def test_garbled_line_is_reported_not_fatal(client):
    logs = []
    b = Bridge(_client_poster(client), log=logs.append)
    b.handle_line('@BHM {"battery_id": "CELL-1", "volt')   # cut off mid-line
    assert any("unreadable" in l for l in logs) and not b.pending
