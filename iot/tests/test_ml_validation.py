"""NASA ML validation demo: leakage-free held-out metrics, EOL handling, baselines."""

from __future__ import annotations

import dataclasses
import math

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from iot.backend import ml_validation as mv
from iot.backend.api import create_app
from iot.backend.config import Settings

S = Settings()


@pytest.fixture(scope="module")
def summary():
    return mv.summary(S)


def test_each_fold_trains_only_on_the_other_cells(summary):
    for cell in mv.CELLS:
        f = summary["folds"][cell]
        assert cell not in f["train_cells"]
        assert set(f["train_cells"]) == set(mv.CELLS) - {cell}


def test_held_out_predictions_reproduce_committed_lobo_results(summary):
    assert summary["reproduction"] == {"checked": True, "matches_committed_lobo_predictions": True,
                                       "file": "results/lobo_predictions.csv"}


def test_metrics_match_committed_fold_results(summary):
    ref = pd.read_csv(S.results_dir / "lobo_cv_results.csv").set_index("test_battery")
    for cell in mv.CELLS:
        f = summary["folds"][cell]
        assert f["soh"]["model"]["rmse"] == pytest.approx(ref.loc[cell, "soh_RMSE"], rel=1e-9)
        assert f["soh"]["model"]["mae"] == pytest.approx(ref.loc[cell, "soh_MAE"], rel=1e-9)
        assert f["rul_two_stage"]["model"]["rmse"] == pytest.approx(ref.loc[cell, "rul_RMSE"], rel=1e-9)
        assert f["rul_classifier"]["accuracy"] == pytest.approx(ref.loc[cell, "eol_Accuracy"], rel=1e-9)
        assert f["rul_classifier"]["recall"] == pytest.approx(ref.loc[cell, "eol_Recall"], rel=1e-9)


def test_reported_metrics_equal_recomputation_from_per_cycle_rows(client):
    d = client.get("/api/ml-validation/B0006").json()
    rows = pd.DataFrame(d["cycles"])
    err = rows["SoH_pred"] - rows["SoH"]
    assert d["fold"]["soh"]["model"]["mae"] == pytest.approx(err.abs().mean())
    assert d["fold"]["soh"]["model"]["rmse"] == pytest.approx(math.sqrt((err ** 2).mean()))
    pre = rows[rows["Reached_EOL"] == 0]
    e2 = pre["RUL_reg_pred"] - pre["RUL"]
    assert d["fold"]["rul_regressor_pre_eol"]["model"]["mae"] == pytest.approx(e2.abs().mean())
    c = d["fold"]["rul_classifier"]["confusion"]
    assert c["tp"] + c["tn"] + c["fp"] + c["fn"] == len(rows)
    assert c["tp"] == int(((rows["Reached_EOL"] == 1) & (rows["EOL_pred"] == 1)).sum())
    # two-stage output is 0 wherever EOL is predicted, regressor output otherwise
    gated = rows[rows["EOL_pred"] == 1]
    assert (gated["RUL_pred"] == 0).all()


def test_eol_cycles_are_observed_at_the_70_percent_threshold(summary):
    df = pd.read_csv(S.features_csv)
    for cell, expected in (("B0005", 125), ("B0006", 109), ("B0018", 97)):
        eol = summary["folds"][cell]["eol"]
        assert eol["status"] == "observed" and eol["eol_cycle"] == expected
        assert eol["eol_cycle"] == int(df[(df.Battery_ID == cell) & (df.Reached_EOL == 1)].Cycle.min())
        assert summary["folds"][cell]["n_cycles"] == int((df.Battery_ID == cell).sum())


def test_censored_cell_is_not_given_an_eol():
    cell = pd.DataFrame({"Cycle": [1, 2, 3], "SoH": [95.0, 90.0, 80.1]})
    e = mv.eol_info(cell)
    assert e["status"] == "censored" and e["eol_cycle"] is None and "RUL undefined" in e["note"]
    boundary = pd.DataFrame({"Cycle": [1, 2, 3], "SoH": [80.0, 70.0, 69.0]})
    assert mv.eol_info(boundary)["eol_cycle"] == 2            # <= 70 % counts, as in build_features.py


def test_baselines_use_only_training_cells(summary):
    # RUL baseline for B0005 = mean EOL of B0006 (109) and B0018 (97) = 103 cycles minus current cycle
    rows = pd.DataFrame(mv.cell_detail(S, "B0005")["cycles"])
    expected = np.maximum(103.0 - rows["Cycle"], 0)
    assert np.allclose(rows["RUL_baseline"], expected)
    assert "103.0" in summary["folds"]["B0005"]["baseline_definition"]["rul"]
    assert "B0005" not in summary["folds"]["B0005"]["baseline_definition"]["soh"]


def test_classifier_and_regressor_reported_separately(summary):
    f = summary["folds"]["B0018"]
    assert set(f["rul_classifier"]) >= {"accuracy", "precision", "recall", "f1", "confusion"}
    assert f["rul_classifier"]["recall"] == 0.0                     # B0018's EOL is never detected
    assert f["eol"]["predicted_eol_cycle"] is None
    assert f["rul_regressor_pre_eol"]["model"]["n"] == 96            # pre-EOL cycles only
    assert f["rul_two_stage"]["model"]["n"] == 132                   # all cycles
    assert len(f["rul_by_life_stage"]) == 3


def test_independent_of_production_models(tmp_path):
    """Validation must not depend on (or use) models/*.pkl trained on all three cells."""
    mv._evaluate.cache_clear()
    s = dataclasses.replace(Settings(), db_path=tmp_path / "x.db", model_dir=tmp_path / "no-models")
    c = TestClient(create_app(s))
    assert c.get("/api/health").json()["models"]["loaded"] is False
    r = c.get("/api/ml-validation").json()
    assert r["reproduction"]["matches_committed_lobo_predictions"] is True
    assert "not used for these metrics" in r["production_models_note"]


def test_api_shape_disclaimer_and_errors(client):
    r = client.get("/api/ml-validation").json()
    assert "NOT validated for the project's physical 2500 mAh and 3000 mAh batteries" in r["disclaimer"]
    assert r["cells"] == ["B0005", "B0006", "B0018"] and r["eol_threshold_soh_pct"] == 70.0
    assert r["aggregate"]["soh_mae"]["mean"] > 0 and r["rul_difficulty"]
    assert client.get("/api/ml-validation/B0007").status_code == 404
    d = client.get("/api/ml-validation/B0018").json()
    assert len(d["cycles"]) == 132 and {"SoH", "SoH_pred", "RUL", "RUL_pred", "RUL_baseline"} <= set(d["cycles"][0])


def test_demo_does_not_touch_live_hardware_data(client):
    before = {b["battery_id"] for b in client.get("/api/batteries").json()["batteries"]}
    client.get("/api/ml-validation")
    client.get("/api/ml-validation/B0005")
    after = {b["battery_id"] for b in client.get("/api/batteries").json()["batteries"]}
    assert before == after
    cmp = client.get("/api/batteries/compare").json()["batteries"]
    assert all(b["soh"] == "unavailable" for b in cmp)
