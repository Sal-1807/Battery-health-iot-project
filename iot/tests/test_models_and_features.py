"""Model loading, feature compatibility and feature-definition parity."""

from __future__ import annotations

import json

import joblib
import numpy as np
import pandas as pd
import pytest

from iot.backend.config import Settings
from iot.backend.features import FEATURE_ORDER, compute_segment_features, feature_vector
from iot.backend.model_service import ModelService

S = Settings()


@pytest.fixture(scope="module")
def models():
    m = ModelService(S)
    if not m.loaded:
        pytest.skip(f"models not available: {m.error}")
    return m


def test_feature_order_matches_metadata_and_training_script():
    meta = json.loads((S.model_dir / "metadata.json").read_text())
    assert tuple(meta["features"]) == FEATURE_ORDER
    src = S.train_script.read_text()
    for f in FEATURE_ORDER:
        assert f'"{f}"' in src


def test_models_load_and_report_feature_count(models):
    info = models.info()
    assert info["loaded"] and info["model_version"].startswith("rf-nasa-")
    assert info["features"] == list(FEATURE_ORDER)
    assert set(info["feature_envelope"]) == set(FEATURE_ORDER)


def test_backend_inference_matches_direct_sklearn(models):
    """The gated service must produce exactly what the saved estimators produce."""
    df = pd.read_csv(S.features_csv)
    scaler = joblib.load(S.model_dir / "scaler.pkl")
    soh = joblib.load(S.model_dir / "soh_model.pkl")
    clf = joblib.load(S.model_dir / "rul_classifier.pkl")
    reg = joblib.load(S.model_dir / "rul_regressor.pkl")
    for _, row in df.sample(25, random_state=0).iterrows():
        cyc = {c: row[f] for c, f in [("voltage_mean", "Voltage_mean"), ("voltage_min", "Voltage_min"),
                                      ("voltage_max", "Voltage_max"), ("voltage_range", "Voltage_range"),
                                      ("current_mean", "Current_mean"), ("current_std", "Current_std"),
                                      ("temperature_mean", "Temperature_mean"),
                                      ("temperature_max", "Temperature_max"),
                                      ("temperature_rise", "Temperature_rise"), ("cycle_number", "Cycle_number")]}
        cyc["complete"] = 1
        res = models.predict_cycle(cyc, data_source="nasa_replay", rated_capacity_ah=2.0)
        X = scaler.transform(row[list(FEATURE_ORDER)].to_numpy(dtype=float).reshape(1, -1))
        assert res.status == "nasa_in_sample"
        assert res.soh_pct == pytest.approx(float(soh.predict(X)[0]))
        eol = int(clf.predict(X)[0])
        assert res.eol_predicted == eol
        expected_rul = 0.0 if eol == 1 else max(float(reg.predict(X)[0]), 0.0)
        assert res.rul_cycles == pytest.approx(expected_rul)


def test_live_cycle_refused_when_out_of_training_range(models):
    # ~0.37 A resistor discharge: Current_mean far outside NASA's -2.0..-1.52 A
    cyc = dict(voltage_mean=3.7, voltage_min=3.0, voltage_max=3.95, voltage_range=0.95, current_mean=-0.37,
               current_std=0.02, temperature_mean=25.2, temperature_max=25.6, temperature_rise=0.4,
               cycle_number=10, complete=1)
    res = models.predict_cycle(cyc, data_source="hardware", rated_capacity_ah=3.0)
    assert res.status == "unavailable" and res.soh_pct is None
    names = {o["feature"] for o in res.out_of_range}
    assert {"Current_mean", "Voltage_min", "Temperature_rise"} <= names


def test_missing_feature_is_not_imputed(models):
    cyc = dict(voltage_mean=3.5, voltage_min=2.5, voltage_max=4.19, voltage_range=1.69, current_mean=-1.8,
               current_std=0.58, temperature_mean=32, temperature_max=39, temperature_rise=None,
               cycle_number=None, complete=1)
    vec, missing = feature_vector(cyc)
    assert vec is None and missing == ["Temperature_rise", "Cycle_number"]
    res = models.predict_cycle(cyc, data_source="hardware", rated_capacity_ah=2.0)
    assert res.status == "unavailable" and "not imputed" in res.reasons[0]


def test_incomplete_cycle_refused(models):
    cyc = dict(voltage_mean=3.5, voltage_min=2.5, voltage_max=4.19, voltage_range=1.69, current_mean=-1.8,
               current_std=0.58, temperature_mean=32, temperature_max=39, temperature_rise=15,
               cycle_number=50, complete=0, completeness_notes=["did not reach end-of-discharge voltage"])
    res = models.predict_cycle(cyc, data_source="hardware", rated_capacity_ah=2.0)
    assert res.status == "unavailable" and res.soh_pct is None


def test_in_range_live_cycle_is_experimental_with_capacity_warning(models):
    df = pd.read_csv(S.features_csv)
    r = df.iloc[40]
    cyc = {k.lower(): r[k] for k in FEATURE_ORDER}
    cyc["complete"] = 1
    res = models.predict_cycle(cyc, data_source="hardware", rated_capacity_ah=3.0)
    assert res.status == "experimental" and res.soh_pct is not None
    assert any("3 Ah" in w for w in res.warnings)


def test_segment_features_match_offline_definitions():
    v = [4.19, 4.0, 3.8, 3.6, 3.2, 2.7, 3.3]
    i = [0.0, 2.0, 2.0, 2.0, 2.0, 2.0, 0.0]  # positive = discharge (live convention)
    t = [24.0, 26.0, 29.0, 33.0, 37.0, 39.5, 38.0]
    f = compute_segment_features(v, i, t)
    nasa_i = -np.array(i)
    assert f.voltage_mean == pytest.approx(np.mean(v))
    assert f.voltage_min == 2.7 and f.voltage_max == 4.19
    assert f.voltage_range == pytest.approx(4.19 - 2.7)
    assert f.current_mean == pytest.approx(np.mean(nasa_i)) and f.current_mean < 0
    assert f.current_std == pytest.approx(np.std(nasa_i))          # population std, as np.nanstd
    assert f.temperature_max == 39.5
    assert f.temperature_rise == pytest.approx(39.5 - 24.0)        # max - FIRST sample


def test_segment_features_missing_values():
    f = compute_segment_features([3.9, None, 3.7], [0.4, 0.4, None], [None, 25.0, 25.5])
    assert f.voltage_mean == pytest.approx(3.8)
    assert f.temperature_rise is None            # first temperature sample missing
    assert f.temperature_coverage == pytest.approx(2 / 3)
    empty = compute_segment_features([None], [None], [None])
    assert empty.voltage_mean is None and empty.current_std is None
