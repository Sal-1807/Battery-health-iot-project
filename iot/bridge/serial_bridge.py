"""USB serial -> backend bridge (use when the ESP32 cannot use Wi-Fi).

The firmware, built with ``TRANSPORT_WIFI 0``, prints one line per reading:

    @BHM {"battery_id":"CELL-18650-01", ... }

This script reads those lines from the USB serial port, adds a UTC timestamp
from the PC clock (the ESP32 has no NTP without Wi-Fi), and POSTs each reading
to ``/api/battery/reading``. All other serial lines (the human-readable log) are
echoed to the console unchanged.

If the backend is unreachable, readings are kept in memory (up to
``--buffer``) and re-sent in order once it is back. The firmware's per-boot
``seq``/``boot_id`` make re-sends idempotent on the server.

Close the Arduino IDE Serial Monitor first: only one program can open the port.

    pip install pyserial
    python -m iot.bridge.serial_bridge --port COM9                   # Windows
    python -m iot.bridge.serial_bridge --port /dev/ttyUSB0           # Linux
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import deque
from datetime import datetime, timezone
from typing import Callable, Iterable, Optional

from iot.simulator.simulate_esp32 import Poster, http_poster

PREFIX = "@BHM "


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_line(line: str, now_iso: Callable[[], str] = utc_now_iso) -> Optional[dict]:
    """Return the reading dict for a ``@BHM`` line, else None. Raises ValueError on bad JSON."""
    line = line.strip()
    if not line.startswith(PREFIX):
        return None
    reading = json.loads(line[len(PREFIX):])
    if not isinstance(reading, dict):
        raise ValueError("payload is not a JSON object")
    reading.setdefault("timestamp", now_iso())
    return reading


class Bridge:
    def __init__(self, post: Poster, buffer_size: int = 5000, log: Callable[[str], None] = print,
                 echo_device_log: bool = True):
        self.post = post
        self.pending: deque[dict] = deque(maxlen=buffer_size)
        self.log = log
        self.echo = echo_device_log
        self.sent = self.duplicates = self.rejected = self.dropped = 0
        self.retry_at = 0.0
        self.backoff = 0.0

    def handle_line(self, line: str, now: Optional[float] = None) -> None:
        now = time.monotonic() if now is None else now
        try:
            reading = parse_line(line)
        except (json.JSONDecodeError, ValueError) as e:
            self.log(f"[bridge] unreadable data line ({e}): {line.strip()[:120]}")
            return
        if reading is None:
            if self.echo and line.strip():
                self.log(f"[esp32] {line.rstrip()}")
            if self.pending:
                self.flush(now)  # retry the queue even when no new reading arrives
            return
        if len(self.pending) == self.pending.maxlen:
            self.dropped += 1
        self.pending.append(reading)
        self.flush(now)

    def flush(self, now: Optional[float] = None) -> None:
        now = time.monotonic() if now is None else now
        if now < self.retry_at:
            return
        while self.pending:
            r = self.pending[0]
            try:
                status, body = self.post("/api/battery/reading", r)
            except OSError as e:
                status, body = 0, {"error": str(e)}
            if status == 200:
                self.pending.popleft()
                self.backoff = 0.0
                if body.get("status") == "duplicate":
                    self.duplicates += 1
                else:
                    self.sent += 1
                for c in body.get("cycles_closed", []):
                    self.log(f"[bridge] cycle closed: segment {c['segment_index']} complete={c['complete']} "
                             f"prediction={c['prediction']['status']}")
                if body.get("flags"):
                    self.log(f"[bridge] seq={r.get('seq')} stored, flags: {', '.join(body['flags'])}")
            elif status in (0, 408, 429) or status >= 500:
                self.backoff = min(self.backoff * 2 if self.backoff else 2.0, 30.0)
                self.retry_at = now + self.backoff
                self.log(f"[bridge] backend unavailable (HTTP {status or 'no connection'}); "
                         f"{len(self.pending)} reading(s) queued, retry in {self.backoff:.0f} s")
                return
            else:
                self.pending.popleft()
                self.rejected += 1
                self.log(f"[bridge] reading rejected by backend (HTTP {status}): {body.get('detail', body)}")

    def run(self, lines: Iterable[str]) -> None:
        last_report = time.monotonic()
        for line in lines:
            self.handle_line(line)
            if time.monotonic() - last_report > 30:
                last_report = time.monotonic()
                self.log(f"[bridge] sent={self.sent} duplicates={self.duplicates} rejected={self.rejected} "
                         f"queued={len(self.pending)} dropped={self.dropped}")


def serial_lines(port: str, baud: int) -> Iterable[str]:
    try:
        import serial  # pyserial
    except ImportError:
        raise SystemExit("pyserial is not installed: pip install pyserial")
    while True:
        try:
            with serial.Serial(port, baud, timeout=1) as ser:
                print(f"[bridge] reading {port} at {baud} baud (Ctrl+C to stop)")
                while True:
                    raw = ser.readline()
                    if raw:
                        yield raw.decode("utf-8", errors="replace")
                    else:
                        yield ""  # lets the bridge retry queued readings while the board is quiet
        except serial.SerialException as e:
            msg = str(e)
            hint = " - is the Arduino Serial Monitor still open?" if "denied" in msg.lower() or "busy" in msg.lower() else ""
            print(f"[bridge] cannot use {port}: {msg}{hint}. Retrying in 3 s...", file=sys.stderr)
            time.sleep(3)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", required=True, help="serial port, e.g. COM9 or /dev/ttyUSB0")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--api", default="http://localhost:8000", help="backend base URL")
    ap.add_argument("--buffer", type=int, default=5000, help="max readings kept while backend is down")
    ap.add_argument("--quiet", action="store_true", help="do not echo the ESP32's human-readable log")
    args = ap.parse_args(argv)

    bridge = Bridge(http_poster(args.api), buffer_size=args.buffer, echo_device_log=not args.quiet)
    try:
        bridge.run(serial_lines(args.port, args.baud))
    except KeyboardInterrupt:
        print(f"\n[bridge] stopped. sent={bridge.sent} duplicates={bridge.duplicates} "
              f"rejected={bridge.rejected} unsent={len(bridge.pending)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
