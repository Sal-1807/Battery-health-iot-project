from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from iot.backend.api import create_app
from iot.backend.config import Settings
from iot.backend.model_service import MODEL_FILES

# Trained models are git-ignored; tests that need them skip on a fresh checkout.
_MODEL_DIR = Settings().model_dir
needs_models = pytest.mark.skipif(
    not all((_MODEL_DIR / f).exists() for f in MODEL_FILES.values()),
    reason="models/*.pkl not generated - run `python src/train_models.py`",
)


@pytest.fixture
def settings(tmp_path):
    # Short segmentation windows so test fixtures stay small.
    return dataclasses.replace(Settings(), db_path=tmp_path / "test.db", min_segment_readings=20,
                               min_segment_duration_s=300, tail_s=60, max_gap_s=30)


@pytest.fixture
def app(settings):
    return create_app(settings)


@pytest.fixture
def client(app):
    return TestClient(app)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def reading(battery="HW-1", ts=None, v=3.85, i=0.0, t=25.0, **kw):
    body = {"battery_id": battery, "device_id": kw.pop("device_id", "esp32-a"),
            "timestamp": iso(ts or datetime.now(timezone.utc)),
            "voltage_v": v, "current_a": i, "temperature_c": t}
    body.update(kw)
    return body


def past(seconds_ago: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)
