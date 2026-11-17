"""SQLite persistence layer.

All SQL lives in this module so the storage engine can be swapped later
(PostgreSQL/TimescaleDB) without touching the API or ML code. The schema uses
only portable SQL types; timestamps are stored both as ISO-8601 UTC text and as
integer epoch milliseconds (the latter is what range queries index on).

Schema changes: bump ``SCHEMA_VERSION`` and add a step to ``_MIGRATIONS``.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

SCHEMA_VERSION = 3

_SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS batteries (
    battery_id          TEXT PRIMARY KEY,
    label               TEXT,
    chemistry           TEXT,
    rated_capacity_ah   REAL,
    nominal_voltage_v   REAL,
    cutoff_voltage_v    REAL,
    cycle_count_offset  INTEGER,          -- known discharge cycles before monitoring began; NULL = unknown
    source              TEXT NOT NULL,     -- hardware | simulation | nasa_replay
    notes               TEXT,
    created_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS devices (
    device_id           TEXT PRIMARY KEY,
    battery_id          TEXT,
    firmware_version    TEXT,
    boot_id             TEXT,
    last_rssi_dbm       INTEGER,
    last_seen_at        TEXT,
    last_seen_ms        INTEGER,
    last_sensor_status  TEXT               -- JSON
);

CREATE TABLE IF NOT EXISTS readings (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    battery_id          TEXT NOT NULL,
    device_id           TEXT NOT NULL,
    source              TEXT NOT NULL,
    ts                  TEXT NOT NULL,
    ts_ms               INTEGER NOT NULL,
    timestamp_source    TEXT NOT NULL,     -- device | bridge | server
    received_at         TEXT NOT NULL,
    voltage_v           REAL,
    current_a           REAL,              -- positive = discharge
    temperature_c       REAL,
    power_w             REAL,
    power_source        TEXT,              -- device | computed | NULL
    seq                 INTEGER,
    boot_id             TEXT,
    ina219_status       TEXT,
    ds18b20_status      TEXT,
    battery_detected    INTEGER,           -- 1/0/NULL
    flags               TEXT NOT NULL DEFAULT '[]'
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_readings_seq ON readings(device_id, boot_id, seq);
CREATE UNIQUE INDEX IF NOT EXISTS ux_readings_ts ON readings(battery_id, device_id, source, ts_ms);
CREATE INDEX IF NOT EXISTS ix_readings_battery_ts ON readings(battery_id, ts_ms);

CREATE TABLE IF NOT EXISTS cycles (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    battery_id          TEXT NOT NULL,
    source              TEXT NOT NULL,     -- live_aggregated | nasa_replay | manual
    segment_index       INTEGER NOT NULL,  -- 1-based order among this battery's cycles of this source
    cycle_number        INTEGER,           -- model feature; NULL when unknown
    start_ts            TEXT,
    end_ts              TEXT,
    start_ms            INTEGER,
    end_ms              INTEGER,
    n_readings          INTEGER,
    duration_s          REAL,
    voltage_mean        REAL,
    voltage_min         REAL,
    voltage_max         REAL,
    voltage_range       REAL,
    current_mean        REAL,              -- NASA convention: negative = discharge
    current_std         REAL,
    temperature_mean    REAL,
    temperature_max     REAL,
    temperature_rise    REAL,
    complete            INTEGER NOT NULL,
    completeness_notes  TEXT NOT NULL DEFAULT '[]',
    ref_soh             REAL,              -- ground truth when known (NASA replay only)
    ref_rul             REAL,
    created_at          TEXT NOT NULL,
    UNIQUE(battery_id, source, segment_index)
);
CREATE INDEX IF NOT EXISTS ix_cycles_battery ON cycles(battery_id, segment_index);

CREATE TABLE IF NOT EXISTS predictions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    battery_id          TEXT NOT NULL,
    cycle_id            INTEGER,
    cycle_number        INTEGER,
    created_at          TEXT NOT NULL,
    status              TEXT NOT NULL,     -- unavailable | experimental | nasa_in_sample
    model_version       TEXT,
    data_source         TEXT NOT NULL,
    soh_pct             REAL,
    eol_probability     REAL,
    eol_predicted       INTEGER,
    rul_cycles          REAL,
    reasons             TEXT NOT NULL DEFAULT '[]',
    warnings            TEXT NOT NULL DEFAULT '[]',
    out_of_range        TEXT NOT NULL DEFAULT '[]',
    input_features      TEXT
);
CREATE INDEX IF NOT EXISTS ix_predictions_battery ON predictions(battery_id, id);

CREATE TABLE IF NOT EXISTS alerts (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    battery_id          TEXT NOT NULL,
    device_id           TEXT,
    type                TEXT NOT NULL,
    severity            TEXT NOT NULL,     -- info | warning | critical
    message             TEXT NOT NULL,
    value               REAL,
    threshold           REAL,
    unit                TEXT,
    status              TEXT NOT NULL,     -- active | resolved
    acknowledged        INTEGER NOT NULL DEFAULT 0,
    first_seen          TEXT NOT NULL,
    last_seen           TEXT NOT NULL,
    resolved_at         TEXT,
    occurrences         INTEGER NOT NULL DEFAULT 1,
    experimental        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_alerts_battery ON alerts(battery_id, status);

CREATE TABLE IF NOT EXISTS settings (
    key                 TEXT PRIMARY KEY,
    value               TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS schema_version (
    version             INTEGER NOT NULL
);
"""

# v2: the timestamp-based dedup key only applies to readings WITHOUT a seq
# number. Distinct readings that share a millisecond (e.g. a burst of buffered
# USB-serial lines stamped on arrival) must not be dropped as duplicates.
_SCHEMA_V2 = """
DROP INDEX IF EXISTS ux_readings_ts;
CREATE UNIQUE INDEX IF NOT EXISTS ux_readings_ts_noseq
    ON readings(battery_id, device_id, source, ts_ms) WHERE seq IS NULL;
"""

# v3: how a reading arrived (wifi | usb_serial); timestamp_source gains "bridge".
_SCHEMA_V3 = """
ALTER TABLE readings ADD COLUMN transport TEXT;
"""

_MIGRATIONS: dict[int, str] = {1: _SCHEMA_V1, 2: _SCHEMA_V2, 3: _SCHEMA_V3}


class Database:
    """Thin wrapper around a single SQLite file.

    A new connection is opened per operation: SQLite connections are cheap and
    this avoids sharing a connection across FastAPI's worker threads. A process
    level lock serialises writers so concurrent ingests never hit SQLITE_BUSY.
    """

    def __init__(self, path: Path | str):
        self.path = str(path)
        self._write_lock = threading.Lock()
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.migrate()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        return conn

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        """Serialised transaction; rolls back on any exception."""
        with self._write_lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                yield conn
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            finally:
                conn.close()

    def migrate(self) -> None:
        with self._write_lock:
            conn = self._connect()
            try:
                conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
                row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
                current = row["v"] or 0
                for version in sorted(_MIGRATIONS):
                    if version > current:
                        conn.executescript(_MIGRATIONS[version])
                        conn.execute("INSERT INTO schema_version(version) VALUES (?)", (version,))
            finally:
                conn.close()

    def ping(self) -> bool:
        try:
            with self.read() as conn:
                conn.execute("SELECT 1").fetchone()
            return True
        except sqlite3.Error:
            return False


def row_to_dict(row: sqlite3.Row | None, json_fields: tuple[str, ...] = ()) -> dict[str, Any] | None:
    if row is None:
        return None
    d = dict(row)
    for f in json_fields:
        if f in d and isinstance(d[f], str):
            try:
                d[f] = json.loads(d[f])
            except json.JSONDecodeError:
                pass
    return d
