"""Loads the team's trained models and decides WHEN they may be used.

Inference itself is the unchanged two-stage logic from ``src/train_models.py``
(``predict_rul_twostage`` is imported from that file, not re-implemented).
What this module adds is gating: a prediction is only produced when the input is
a complete cycle-level feature vector that lies inside the envelope the models
were trained on. Everything else returns ``status = "unavailable"`` with reasons.

Prediction statuses
-------------------
``nasa_in_sample``  NASA data replayed through the deployed model. The deployed
                    model was fit on all three NASA cells, so these values are
                    in-sample and are NOT an accuracy measure. Held-out accuracy
                    lives in results/lobo_*.csv.
``experimental``    Live / simulated cycle that passed every gate. Still not
                    validated: no non-NASA cell has ever been used to test the model.
``unavailable``     A gate failed; no number is produced.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

from .config import Settings
from .features import DB_TO_FEATURE, FEATURE_ORDER, feature_vector

log = logging.getLogger(__name__)

NASA_RATED_CAPACITY_AH = 2.0
NASA_EOL_SOH_PCT = 70.0
# Envelope tolerance: a feature may exceed the NASA training min/max by this
# fraction of the training range before it is called out-of-distribution.
ENVELOPE_TOLERANCE = 0.10

MODEL_FILES = {
    "scaler": "scaler.pkl",
    "soh": "soh_model.pkl",
    "rul_classifier": "rul_classifier.pkl",
    "rul_regressor": "rul_regressor.pkl",
}

LIMITATIONS = [
    "Trained on three NASA PCoE 18650 cells (B0005, B0006, B0018), 2.0 Ah rated, 24 degC ambient, ~2 A constant-current discharge.",
    "Leave-one-battery-out SoH R2 ranges 0.55-0.88 per held-out cell; B0018 shows strong domain shift (EOL recall 0 when held out).",
    "Cycle_number is an input feature: the model partly learns 'older = worse' and needs the true discharge-cycle count of the cell.",
    "SoH is defined against 2.0 Ah rated capacity and EOL at 1.4 Ah (70 %). Neither has been validated for a 3.0 Ah cell.",
    "Features are statistics over a complete discharge record; a partial discharge or a single reading cannot be used.",
    "A different load (e.g. a 10 ohm resistor, ~0.37 A) puts Current_mean and temperature features outside the training range.",
]


@dataclass
class PredictionResult:
    status: str
    soh_pct: Optional[float] = None
    eol_probability: Optional[float] = None
    eol_predicted: Optional[int] = None
    rul_cycles: Optional[float] = None
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    out_of_range: list[dict[str, Any]] = field(default_factory=list)
    input_features: Optional[dict[str, float]] = None
    model_version: Optional[str] = None


def _load_twostage(train_script: Path):
    spec = importlib.util.spec_from_file_location("bhm_train_models", train_script)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot import {train_script}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # module body only defines functions; main() is guarded
    return module.predict_rul_twostage, list(module.FEATURES)


class ModelService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.loaded = False
        self.error: Optional[str] = None
        self.load_warnings: list[str] = []
        self.metadata: dict[str, Any] = {}
        self.version: Optional[str] = None
        self.envelope: dict[str, dict[str, float]] = {}
        self._models: dict[str, Any] = {}
        self._twostage = None
        self._load()

    # ------------------------------------------------------------------ load
    def _load(self) -> None:
        d = self.settings.model_dir
        try:
            meta_path = d / "metadata.json"
            self.metadata = json.loads(meta_path.read_text())
            if tuple(self.metadata.get("features", [])) != FEATURE_ORDER:
                raise ValueError(f"metadata.json feature order {self.metadata.get('features')} "
                                 f"!= backend FEATURE_ORDER {list(FEATURE_ORDER)}")

            missing = [f for f in MODEL_FILES.values() if not (d / f).exists()]
            if missing:
                raise FileNotFoundError(
                    f"model files missing in {d}: {missing}. They are git-ignored; "
                    f"regenerate with `python src/train_models.py`.")

            import joblib  # local import: keeps API importable without sklearn for docs/tests of other parts

            digest = hashlib.sha256()
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                for key, fname in MODEL_FILES.items():
                    p = d / fname
                    digest.update(p.read_bytes())
                    self._models[key] = joblib.load(p)
            self.load_warnings = sorted({str(w.message) for w in caught})

            n_in = getattr(self._models["scaler"], "n_features_in_", None)
            if n_in != len(FEATURE_ORDER):
                raise ValueError(f"scaler expects {n_in} features, backend provides {len(FEATURE_ORDER)}")
            for key in ("soh", "rul_classifier", "rul_regressor"):
                m_in = getattr(self._models[key], "n_features_in_", None)
                if m_in != len(FEATURE_ORDER):
                    raise ValueError(f"{key} expects {m_in} features")

            twostage, train_features = _load_twostage(self.settings.train_script)
            if tuple(train_features) != FEATURE_ORDER:
                raise ValueError("src/train_models.py FEATURES differ from backend FEATURE_ORDER")
            self._twostage = twostage

            self.version = "rf-nasa-" + digest.hexdigest()[:12]
            self._build_envelope()
            self.loaded = True
            log.info("models loaded: %s", self.version)
        except Exception as exc:  # noqa: BLE001 - surfaced through /api/health
            self.loaded = False
            self.error = f"{type(exc).__name__}: {exc}"
            log.error("model load failed: %s", self.error)

    def _build_envelope(self) -> None:
        df = pd.read_csv(self.settings.features_csv)
        for f in FEATURE_ORDER:
            lo, hi = float(df[f].min()), float(df[f].max())
            self.envelope[f] = {"min": lo, "max": hi}

    # ------------------------------------------------------------------ info
    def info(self) -> dict[str, Any]:
        import sklearn

        return {
            "loaded": self.loaded,
            "error": self.error,
            "model_version": self.version,
            "load_warnings": self.load_warnings,
            "sklearn_version": sklearn.__version__,
            "features": list(FEATURE_ORDER),
            "training_batteries": self.metadata.get("batteries"),
            "n_training_samples": self.metadata.get("n_samples"),
            "estimators": self.metadata.get("models"),
            "nasa_rated_capacity_ah": NASA_RATED_CAPACITY_AH,
            "nasa_eol_soh_pct": NASA_EOL_SOH_PCT,
            "feature_envelope": self.envelope,
            "envelope_tolerance": ENVELOPE_TOLERANCE,
            "limitations": LIMITATIONS,
        }

    # -------------------------------------------------------------- gating
    def check_envelope(self, features: dict[str, float]) -> list[dict[str, Any]]:
        out = []
        for name, val in features.items():
            env = self.envelope.get(name)
            if env is None:
                continue
            span = env["max"] - env["min"]
            tol = ENVELOPE_TOLERANCE * span
            lo, hi = env["min"] - tol, env["max"] + tol
            if val < lo or val > hi:
                out.append({"feature": name, "value": val, "train_min": env["min"],
                            "train_max": env["max"], "allowed_min": lo, "allowed_max": hi})
        return out

    def _infer(self, vec: list[float]) -> tuple[float, float, int, float]:
        X = self._models["scaler"].transform(np.array([vec], dtype=float))
        soh = float(self._models["soh"].predict(X)[0])
        clf = self._models["rul_classifier"]
        proba = clf.predict_proba(X)[0]
        classes = list(clf.classes_)
        eol_prob = float(proba[classes.index(1)]) if 1 in classes else 0.0
        eol_pred, rul_pred = self._twostage(clf, self._models["rul_regressor"], X)
        return soh, eol_prob, int(eol_pred[0]), float(rul_pred[0])

    def predict_cycle(
        self,
        cycle: dict[str, Any],
        *,
        data_source: str,
        rated_capacity_ah: Optional[float],
    ) -> PredictionResult:
        """Run (or refuse) a prediction for one row of the cycles table."""
        res = PredictionResult(status="unavailable", model_version=self.version)

        if not self.loaded:
            res.reasons.append(f"models not loaded ({self.error})")
            return res

        if not cycle.get("complete"):
            res.reasons.append("cycle is incomplete: " + "; ".join(cycle.get("completeness_notes") or []))

        vec, missing = feature_vector(cycle)
        if missing:
            res.reasons.append("missing features (not imputed): " + ", ".join(missing))
        # Report out-of-range features even when the cycle is already refused, so
        # the user can see whether fixing the other reasons would be enough.
        present = {f: float(cycle[c]) for c, f in DB_TO_FEATURE.items()
                   if cycle.get(c) is not None and not (isinstance(cycle[c], float) and np.isnan(cycle[c]))}
        res.out_of_range = self.check_envelope(present)
        if data_source != "nasa_replay" and res.out_of_range:
            names = ", ".join(o["feature"] for o in res.out_of_range)
            res.reasons.append(f"features outside NASA training range: {names}")
        if vec is None or res.reasons:
            return res

        res.input_features = dict(zip(FEATURE_ORDER, vec))

        if data_source == "nasa_replay":
            res.status = "nasa_in_sample"
            res.warnings.append("NASA cell replayed through a model trained on this same data: "
                                "in-sample output, not an accuracy measure (see LOBO results).")
        else:
            res.status = "experimental"
            res.warnings.append("Live-data estimate from a model validated only on NASA cells.")
            if rated_capacity_ah is None:
                res.warnings.append("Rated capacity of this cell is not registered.")
            elif abs(rated_capacity_ah - NASA_RATED_CAPACITY_AH) > 1e-6:
                res.warnings.append(
                    f"Cell is rated {rated_capacity_ah:g} Ah; model SoH is on the NASA 2.0 Ah scale "
                    f"and EOL (70 %) has not been validated for this cell.")

        soh, eol_prob, eol_pred, rul = self._infer(vec)
        res.soh_pct, res.eol_probability, res.eol_predicted, res.rul_cycles = soh, eol_prob, eol_pred, rul
        return res
