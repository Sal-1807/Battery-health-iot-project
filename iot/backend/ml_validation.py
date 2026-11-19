"""NASA ML validation demo: leakage-free held-out evaluation of the SoH/RUL models.

Why this module trains its own models
-------------------------------------
The production models in models/*.pkl were fit on ALL three NASA cells
(B0005, B0006, B0018). Their predictions on any of those cells are in-sample
and must not be reported as test results. Instead, for each cell this module
trains evaluation-only models on the OTHER two cells (leave-one-cell-out) and
predicts the held-out cell, using the team's own training functions from
src/train_models.py (same features, hyperparameters, scaler and random_state).
Nothing is written to disk; the production models are never loaded here.

The two-stage RUL predictions and SoH predictions this produces are checked
against the committed held-out results (results/lobo_predictions.csv); they
reproduce them exactly with the library versions used here.

What is reported per held-out cell
----------------------------------
* SoH regressor: MAE / RMSE (% points).
* RUL stage 1, EOL classifier: accuracy, precision, recall, F1, confusion matrix.
* RUL stage 2, regressor alone: MAE / RMSE on the cycles that are truly pre-EOL
  (where the regressor's target is defined).
* RUL two-stage pipeline (classifier gates the regressor; 0 once EOL is
  predicted): MAE / RMSE on all cycles - what the system would actually output.
* Baselines fit on the same two training cells:
  - SoH: straight line SoH ~ cycle number;
  - RUL: mean EOL cycle of the training cells minus the current cycle (>= 0).

EOL definition (project-wide): first discharge with capacity <= 1.4 Ah, i.e.
SoH <= 70 % of the 2.0 Ah rating. A cell that never reaches it is censored and
its RUL is undefined (never extrapolated).
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import math
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

from .config import Settings

CELLS = ("B0005", "B0006", "B0018")
EOL_SOH_PCT = 70.0

DISCLAIMER = (
    "NASA demonstration only. These models were trained and validated on three NASA PCoE 18650 cells "
    "(2.0 Ah rated, 24 degC, ~2 A constant-current discharge). They are NOT validated for the project's "
    "physical 2500 mAh and 3000 mAh batteries, and no SoH/RUL is produced for those batteries."
)

PROTOCOL = (
    "Leave-one-cell-out: for each cell, evaluation models are trained on the other two cells only and predict "
    "the held-out cell. The production models (trained on all three cells) are not used for these metrics, "
    "because their predictions on these cells would be in-sample."
)

RUL_DIFFICULTY = [
    "RUL depends on degradation that has not happened yet; the features describe only the current cycle.",
    "Early in life the cell is far from EOL and small differences in fade rate compound into large cycle errors.",
    "Capacity regeneration after rest periods makes SoH non-monotonic, so the same features can occur at very "
    "different remaining lives.",
    "With three cells, each held-out cell is extrapolated from two examples; B0018 differs in voltage and "
    "temperature behaviour (see results/analysis/b0018_comparison) and its EOL is missed entirely.",
    "Cycle_number is an input, so the model partly learns 'older = closer to EOL', which only transfers to cells "
    "that age at the same rate.",
    "After EOL the target is clipped to 0, so errors there measure the EOL classifier, not life prediction.",
]


def _train_module(path: Path):
    spec = importlib.util.spec_from_file_location("bhm_train_models_eval", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _reg_metrics(y_true, y_pred) -> dict[str, Optional[float]]:
    y_true, y_pred = np.asarray(y_true, float), np.asarray(y_pred, float)
    if y_true.size == 0:
        return {"n": 0, "mae": None, "rmse": None}
    err = y_pred - y_true
    return {"n": int(y_true.size), "mae": float(np.mean(np.abs(err))), "rmse": float(math.sqrt(np.mean(err ** 2)))}


def _clf_metrics(y_true, y_pred) -> dict[str, Any]:
    y_true, y_pred = np.asarray(y_true, int), np.asarray(y_pred, int)
    tp = int(((y_true == 1) & (y_pred == 1)).sum())
    tn = int(((y_true == 0) & (y_pred == 0)).sum())
    fp = int(((y_true == 0) & (y_pred == 1)).sum())
    fn = int(((y_true == 1) & (y_pred == 0)).sum())
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    return {"n": int(y_true.size), "accuracy": (tp + tn) / max(y_true.size, 1), "precision": prec, "recall": rec,
            "f1": (2 * prec * rec / (prec + rec)) if prec + rec else 0.0,
            "confusion": {"tp": tp, "tn": tn, "fp": fp, "fn": fn}}


def eol_info(cell_df: pd.DataFrame) -> dict[str, Any]:
    """Observed vs censored EOL from the SoH series (project threshold 70 %)."""
    g = cell_df.sort_values("Cycle")
    hit = g.loc[g["SoH"] <= EOL_SOH_PCT, "Cycle"]
    last = int(g["Cycle"].iloc[-1])
    if len(hit):
        return {"status": "observed", "eol_cycle": int(hit.iloc[0]), "last_cycle": last,
                "cycles_after_eol": last - int(hit.iloc[0])}
    return {"status": "censored", "eol_cycle": None, "last_cycle": last, "cycles_after_eol": None,
            "note": f"SoH stayed above {EOL_SOH_PCT:g} % for all {last} cycles: EOL not observed, RUL undefined."}


def _stage_errors(fold: pd.DataFrame, eol_cycle: Optional[int]) -> list[dict[str, Any]]:
    """RUL error by life stage (pre-EOL cycles only), model vs baseline."""
    if eol_cycle is None:
        return []
    pre = fold[fold["Cycle"] < eol_cycle]
    out = []
    for name, lo, hi in (("early life (first third)", 0, 1 / 3), ("mid life", 1 / 3, 2 / 3), ("late life (last third)", 2 / 3, 1.01)):
        part = pre[(pre["Cycle"] / eol_cycle >= lo) & (pre["Cycle"] / eol_cycle < hi)]
        out.append({"stage": name, "n": int(len(part)),
                    "model": _reg_metrics(part["RUL"], part["RUL_pred"]),
                    "baseline": _reg_metrics(part["RUL"], part["RUL_baseline"])})
    return out


@lru_cache(maxsize=4)
def _evaluate(features_csv: str, train_script: str, lobo_csv: str, _mtime: float) -> dict[str, Any]:
    tm = _train_module(Path(train_script))
    feats = list(tm.FEATURES)
    df = pd.read_csv(features_csv)
    folds: dict[str, Any] = {}
    rows: dict[str, list[dict]] = {}

    for cell in CELLS:
        train = df[df["Battery_ID"] != cell]
        test = df[df["Battery_ID"] == cell].sort_values("Cycle").copy()
        train_cells = sorted(train["Battery_ID"].unique().tolist())
        assert cell not in train_cells                                   # leakage guard

        from sklearn.preprocessing import StandardScaler
        scaler = StandardScaler()
        Xtr = scaler.fit_transform(train[feats].values)
        Xte = scaler.transform(test[feats].values)
        with contextlib.redirect_stdout(io.StringIO()):
            soh_m = tm.train_soh_model(Xtr, train["SoH"].values)
            clf, reg = tm.train_rul_models(Xtr, train["Reached_EOL"].values, train["RUL"].values)
        eol_pred, rul_pred = tm.predict_rul_twostage(clf, reg, Xte)
        classes = list(clf.classes_)
        test["SoH_pred"] = soh_m.predict(Xte)
        test["EOL_prob"] = clf.predict_proba(Xte)[:, classes.index(1)] if 1 in classes else 0.0
        test["EOL_pred"] = eol_pred
        test["RUL_pred"] = rul_pred
        test["RUL_reg_pred"] = np.maximum(reg.predict(Xte), 0.0)        # regressor alone, every cycle

        # baselines from the same two training cells
        slope, icpt = np.polyfit(train["Cycle_number"].values, train["SoH"].values, 1)
        test["SoH_baseline"] = icpt + slope * test["Cycle_number"].values
        train_eols = [eol_info(train[train["Battery_ID"] == c])["eol_cycle"] for c in train_cells]
        train_eols = [e for e in train_eols if e is not None]
        mean_eol = float(np.mean(train_eols)) if train_eols else None
        test["RUL_baseline"] = np.maximum(mean_eol - test["Cycle"].values, 0.0) if mean_eol is not None else np.nan

        eol = eol_info(test)
        pre = test[test["Reached_EOL"] == 0]
        first_pred_eol = test.loc[test["EOL_pred"] == 1, "Cycle"]
        folds[cell] = {
            "cell": cell,
            "train_cells": train_cells,
            "n_cycles": int(len(test)),
            "eol": {**eol, "predicted_eol_cycle": int(first_pred_eol.iloc[0]) if len(first_pred_eol) else None},
            "soh": {"model": _reg_metrics(test["SoH"], test["SoH_pred"]),
                    "baseline": _reg_metrics(test["SoH"], test["SoH_baseline"])},
            "rul_classifier": _clf_metrics(test["Reached_EOL"], test["EOL_pred"]),
            "rul_regressor_pre_eol": {"model": _reg_metrics(pre["RUL"], pre["RUL_reg_pred"]),
                                      "baseline": _reg_metrics(pre["RUL"], pre["RUL_baseline"])},
            "rul_two_stage": {"model": _reg_metrics(test["RUL"], test["RUL_pred"]),
                              "baseline": _reg_metrics(test["RUL"], test["RUL_baseline"])},
            "rul_by_life_stage": _stage_errors(test, eol["eol_cycle"]),
            "baseline_definition": {"soh": f"SoH = {icpt:.3f} + {slope:.4f} x cycle (line fit on {', '.join(train_cells)})",
                                    "rul": (f"RUL = max({mean_eol:.1f} - cycle, 0), mean EOL cycle of {', '.join(train_cells)}"
                                            if mean_eol is not None else "unavailable: no training cell reached EOL")},
        }
        keep = ["Cycle", "SoH", "SoH_pred", "SoH_baseline", "RUL", "RUL_pred", "RUL_reg_pred", "RUL_baseline",
                "Reached_EOL", "EOL_pred", "EOL_prob"]
        rows[cell] = test[keep].replace({np.nan: None}).to_dict(orient="records")

    # reproduction check against the committed held-out predictions
    repro: dict[str, Any] = {"checked": False}
    if os.path.exists(lobo_csv):
        ref = pd.read_csv(lobo_csv)
        ok = True
        for cell in CELLS:
            r = ref[ref["Battery_ID"] == cell].sort_values("Cycle")
            mine = pd.DataFrame(rows[cell])
            ok &= len(r) == len(mine) and np.allclose(r["SoH_pred"].values, mine["SoH_pred"].values, atol=1e-6) \
                and np.allclose(r["RUL_pred"].values, mine["RUL_pred"].values, atol=1e-6) \
                and np.array_equal(r["Reached_EOL_pred"].values.astype(int), mine["EOL_pred"].values.astype(int))
        repro = {"checked": True, "matches_committed_lobo_predictions": bool(ok), "file": "results/lobo_predictions.csv"}

    def agg(path):
        vals = [folds[c] for c in CELLS]
        for k in path:
            vals = [v[k] for v in vals]
        vals = [v for v in vals if v is not None]
        return {"mean": float(np.mean(vals)), "std": float(np.std(vals, ddof=1))} if vals else None

    aggregate = {
        "soh_mae": agg(["soh", "model", "mae"]), "soh_rmse": agg(["soh", "model", "rmse"]),
        "soh_baseline_mae": agg(["soh", "baseline", "mae"]), "soh_baseline_rmse": agg(["soh", "baseline", "rmse"]),
        "eol_accuracy": agg(["rul_classifier", "accuracy"]), "eol_recall": agg(["rul_classifier", "recall"]),
        "eol_f1": agg(["rul_classifier", "f1"]),
        "rul_reg_mae": agg(["rul_regressor_pre_eol", "model", "mae"]),
        "rul_reg_rmse": agg(["rul_regressor_pre_eol", "model", "rmse"]),
        "rul_mae": agg(["rul_two_stage", "model", "mae"]), "rul_rmse": agg(["rul_two_stage", "model", "rmse"]),
        "rul_baseline_mae": agg(["rul_two_stage", "baseline", "mae"]),
        "rul_baseline_rmse": agg(["rul_two_stage", "baseline", "rmse"]),
    }
    return {"folds": folds, "rows": rows, "aggregate": aggregate, "reproduction": repro}


def evaluate(s: Settings) -> dict[str, Any]:
    csv = str(s.features_csv)
    lobo = str(Path(s.results_dir) / "lobo_predictions.csv")
    return _evaluate(csv, str(s.train_script), lobo, os.path.getmtime(csv))


def summary(s: Settings) -> dict[str, Any]:
    r = evaluate(s)
    return {"cells": list(CELLS), "protocol": PROTOCOL, "disclaimer": DISCLAIMER, "eol_threshold_soh_pct": EOL_SOH_PCT,
            "eol_definition": "First discharge cycle with capacity <= 1.4 Ah (70 % of the 2.0 Ah rating).",
            "production_models_note": "models/*.pkl were trained on all three cells; they are not used for these metrics.",
            "rul_difficulty": RUL_DIFFICULTY, "folds": r["folds"], "aggregate": r["aggregate"],
            "reproduction": r["reproduction"]}


def cell_detail(s: Settings, cell: str) -> Optional[dict[str, Any]]:
    if cell not in CELLS:
        return None
    r = evaluate(s)
    return {"cell": cell, "disclaimer": DISCLAIMER, "protocol": PROTOCOL, "fold": r["folds"][cell],
            "cycles": r["rows"][cell]}
