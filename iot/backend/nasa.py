"""Read-only access to the committed NASA artefacts for the dashboard.

Predicted-vs-measured comparisons use ``results/lobo_predictions.csv``: those are
genuinely held-out predictions (model never saw that battery). The deployed
model's output on NASA rows would be in-sample and is not used for accuracy.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from .config import Settings


def _records(df: pd.DataFrame) -> list[dict]:
    return df.replace({np.nan: None}).to_dict(orient="records")


@lru_cache(maxsize=4)
def _load(features_csv: Path, results_dir: Path):
    feats = pd.read_csv(features_csv)
    lobo_pred = pd.read_csv(results_dir / "lobo_predictions.csv") if (results_dir / "lobo_predictions.csv").exists() else None
    lobo_cv = pd.read_csv(results_dir / "lobo_cv_results.csv") if (results_dir / "lobo_cv_results.csv").exists() else None
    agg = pd.read_csv(results_dir / "training_metrics.csv") if (results_dir / "training_metrics.csv").exists() else None
    return feats, lobo_pred, lobo_cv, agg


class NasaData:
    def __init__(self, s: Settings):
        self.s = s

    def _data(self):
        return _load(self.s.features_csv, self.s.results_dir)

    def batteries(self) -> list[dict]:
        feats, *_ = self._data()
        out = []
        for b, g in feats.groupby("Battery_ID"):
            g = g.sort_values("Cycle")
            eol = g.loc[g["Reached_EOL"] == 1, "Cycle"]
            out.append({"battery_id": b, "n_cycles": int(len(g)),
                        "eol_cycle": int(eol.iloc[0]) if len(eol) else None,
                        "initial_soh_pct": float(g["SoH"].iloc[0]), "final_soh_pct": float(g["SoH"].iloc[-1])})
        return out

    def cycles(self, battery_id: str) -> list[dict] | None:
        feats, lobo_pred, *_ = self._data()
        g = feats[feats["Battery_ID"] == battery_id].sort_values("Cycle")
        if g.empty:
            return None
        if lobo_pred is not None:
            p = lobo_pred[lobo_pred["Battery_ID"] == battery_id][
                ["Cycle", "SoH_pred", "RUL_pred", "Reached_EOL_pred"]].rename(columns={
                    "SoH_pred": "SoH_lobo_pred", "RUL_pred": "RUL_lobo_pred", "Reached_EOL_pred": "Reached_EOL_lobo_pred"})
            g = g.merge(p, on="Cycle", how="left")
        return _records(g)

    def metrics(self) -> dict:
        _, _, lobo_cv, agg = self._data()
        return {
            "per_fold": _records(lobo_cv) if lobo_cv is not None else None,
            "aggregate": _records(agg) if agg is not None else None,
            "note": "Leave-one-battery-out: each fold trains on two NASA cells and tests on the third. "
                    "Aggregate = mean +/- std across the 3 folds.",
        }
