"""Runtime configuration. Every value can be overridden by an environment variable
prefixed with ``BHM_`` so the same code runs in tests, on a laptop and on a lab PC."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _env(name: str, default: str) -> str:
    return os.environ.get(f"BHM_{name}", default)


@dataclass(frozen=True)
class Settings:
    # "production" (default) or "simulation". Simulation mode serves a separate
    # simulation database with SIM-* profiles and refuses hardware telemetry.
    mode: str = field(default_factory=lambda: _env("MODE", "production").strip().lower())
    db_path: Path = field(default_factory=lambda: Path(_env("DB_PATH", str(REPO_ROOT / "iot" / "data" / "telemetry.db"))))
    model_dir: Path = field(default_factory=lambda: Path(_env("MODEL_DIR", str(REPO_ROOT / "models"))))
    features_csv: Path = field(default_factory=lambda: Path(_env(
        "FEATURES_CSV", str(REPO_ROOT / "data" / "processed" / "battery_health_features.csv"))))
    results_dir: Path = field(default_factory=lambda: Path(_env("RESULTS_DIR", str(REPO_ROOT / "results"))))
    train_script: Path = field(default_factory=lambda: REPO_ROOT / "src" / "train_models.py")

    cors_origins: tuple[str, ...] = field(default_factory=lambda: tuple(
        o.strip() for o in _env(
            "CORS_ORIGINS",
            "http://localhost:5173,http://127.0.0.1:5173,http://localhost:4173,http://127.0.0.1:4173",
        ).split(",") if o.strip()))

    # ---- cycle segmentation (live telemetry -> discharge segments) ----
    # A reading counts as "discharging" when current (positive = discharge) is at
    # or above this value.
    discharge_on_a: float = float(_env("DISCHARGE_ON_A", "0.05"))
    # Gap between consecutive readings that splits/terminates a segment.
    max_gap_s: float = float(_env("MAX_GAP_S", "60"))
    # NASA discharge records include a short relaxation tail after the load is
    # removed. Rest readings up to this long after the load stops are included.
    tail_s: float = float(_env("TAIL_S", "120"))
    min_segment_readings: int = int(_env("MIN_SEGMENT_READINGS", "30"))
    min_segment_duration_s: float = float(_env("MIN_SEGMENT_DURATION_S", "600"))
    # Default "reached end of discharge" voltage for completeness checks. Per
    # battery override lives in the batteries table.
    default_cutoff_v: float = float(_env("DEFAULT_CUTOFF_V", "3.0"))

    # ---- cell presence / measurement validity (see presence.py) ----
    # Li-ion plausibility window. A reading outside it at ~0 A is "no usable cell
    # measurement". A reading inside it does NOT prove a cell is connected.
    cell_plausible_min_v: float = float(_env("CELL_PLAUSIBLE_MIN_V", "2.0"))
    cell_plausible_max_v: float = float(_env("CELL_PLAUSIBLE_MAX_V", "4.5"))
    # |current| on two consecutive readings that proves a closed circuit
    # (~20x the +/-0.5 mA noise observed on the team's INA219 with an open input).
    current_evidence_a: float = float(_env("CURRENT_EVIDENCE_A", "0.010"))
    # Presence evidence expires after a reading gap this long (cell may have been swapped).
    presence_gap_s: float = float(_env("PRESENCE_GAP_S", "600"))

    # ---- discharge sessions (see sessions.py) ----
    # A reading counts as "load on" at or above this current (40x the observed
    # +/-0.5 mA open-input noise). Below it nothing is ever integrated.
    session_on_a: float = float(_env("SESSION_ON_A", "0.020"))
    # Session ends after this long without an on-reading.
    session_stop_hold_s: float = float(_env("SESSION_STOP_HOLD_S", "10"))
    # A reading gap longer than this ends the session (no current is assumed across it).
    session_end_gap_s: float = float(_env("SESSION_END_GAP_S", "300"))
    # Rest voltage at/above which a session is considered to start from full charge
    # (used only to label a session full-range vs partial; never to compute capacity).
    full_charge_v: float = float(_env("FULL_CHARGE_V", "4.15"))

    # Timestamps further than this in the future are rejected.
    max_future_skew_s: float = float(_env("MAX_FUTURE_SKEW_S", "300"))
    # Timestamps older than this are rejected (firmware buffer is far shorter).
    max_backfill_days: float = float(_env("MAX_BACKFILL_DAYS", "7"))


def get_settings() -> Settings:
    return Settings()
