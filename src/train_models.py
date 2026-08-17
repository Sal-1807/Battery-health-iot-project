"""
Baseline ML models for SoH estimation and RUL prediction.

Input : battery_health_features.csv
Output: models/*.pkl, results/lobo_cv_results.csv, results/training_metrics.csv

Strategy
--------
Leave-One-Battery-Out (LOBO) cross-validation: each of the 3 batteries is held
out as the test set once, so every model is trained on 2 batteries and evaluated
on the 3rd. This tests generalization to unseen batteries.

Models
------
1. SoH regression: Random Forest Regressor (all 468 samples)
2. RUL prediction: Two-stage approach to handle zero-inflated distribution
   - Stage 1: Binary classifier for Reached_EOL (has battery hit EOL?)
   - Stage 2: Regressor for RUL (trained only on pre-EOL samples where RUL > 0)

Features: All 10 available features, standardized via StandardScaler.
NO leakage: Capacity and Discharge_time were already excluded by build_features.py.

Metrics
-------
Regression: RMSE, MAE, R²
Classification: Accuracy, Precision, Recall, F1
"""

import json
import os

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.metrics import (
    accuracy_score,
    f1_score,
    mean_absolute_error,
    mean_squared_error,
    precision_score,
    r2_score,
    recall_score,
)
from sklearn.preprocessing import StandardScaler

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_FILE = os.path.join(ROOT, "data", "processed", "battery_health_features.csv")
MODEL_DIR = os.path.join(ROOT, "models")
RESULTS_DIR = os.path.join(ROOT, "results")

FEATURES = [
    "Voltage_mean",
    "Voltage_min",
    "Voltage_max",
    "Voltage_range",
    "Current_mean",
    "Current_std",
    "Temperature_mean",
    "Temperature_max",
    "Temperature_rise",
    "Cycle_number",
]

RANDOM_STATE = 42


def load_data():
    """Load feature set and split into features/labels."""
    df = pd.read_csv(DATA_FILE)
    print(f"loaded {DATA_FILE}: {df.shape[0]} rows x {df.shape[1]} cols")
    print(f"  batteries: {df['Battery_ID'].unique().tolist()}")
    print(f"  rows per battery: {df.groupby('Battery_ID').size().to_dict()}")

    # Verify no leakage columns
    leakage_cols = [c for c in ["Capacity", "Discharge_time"] if c in df.columns]
    if leakage_cols:
        raise ValueError(f"leakage columns found in features: {leakage_cols}")

    # Verify all required features exist
    missing = [f for f in FEATURES if f not in df.columns]
    if missing:
        raise ValueError(f"missing features: {missing}")

    print(f"\nfeatures ({len(FEATURES)}): {FEATURES}")
    print(f"labels: SoH, RUL, Reached_EOL")
    print(f"\nRUL distribution:")
    print(f"  RUL = 0 (at/post-EOL): {(df['RUL'] == 0).sum()} rows ({(df['RUL'] == 0).mean()*100:.1f}%)")
    print(f"  RUL > 0 (pre-EOL):     {(df['RUL'] > 0).sum()} rows ({(df['RUL'] > 0).mean()*100:.1f}%)")
    print(f"  Reached_EOL=1:         {(df['Reached_EOL'] == 1).sum()} rows")

    return df


def train_soh_model(X_train, y_train):
    """Train SoH regression model."""
    model = RandomForestRegressor(
        n_estimators=100, max_depth=10, min_samples_split=5, random_state=RANDOM_STATE
    )
    model.fit(X_train, y_train)
    return model


def train_rul_models(X_train, y_train_eol, y_train_rul):
    """
    Train two-stage RUL prediction:
    1. Classifier: predict Reached_EOL
    2. Regressor: predict RUL for pre-EOL samples only
    """
    # Stage 1: EOL classifier
    clf = RandomForestClassifier(
        n_estimators=100, max_depth=10, min_samples_split=5, random_state=RANDOM_STATE
    )
    clf.fit(X_train, y_train_eol)

    # Stage 2: RUL regressor (train only on pre-EOL samples)
    pre_eol_mask = y_train_eol == 0
    X_train_pre_eol = X_train[pre_eol_mask]
    y_train_rul_pre_eol = y_train_rul[pre_eol_mask]

    reg = RandomForestRegressor(
        n_estimators=100, max_depth=10, min_samples_split=5, random_state=RANDOM_STATE
    )
    reg.fit(X_train_pre_eol, y_train_rul_pre_eol)

    print(f"  RUL classifier trained on {len(X_train)} samples")
    print(f"  RUL regressor trained on {len(X_train_pre_eol)} pre-EOL samples")

    return clf, reg


def predict_rul_twostage(clf, reg, X_test):
    """
    Two-stage RUL prediction:
    1. Predict EOL status
    2. If pre-EOL, predict RUL; if at/post-EOL, return 0
    """
    eol_pred = clf.predict(X_test)
    rul_pred = np.zeros(len(X_test))

    pre_eol_mask = eol_pred == 0
    if pre_eol_mask.sum() > 0:
        rul_pred[pre_eol_mask] = reg.predict(X_test[pre_eol_mask])
        # Clip negative predictions to 0
        rul_pred = np.maximum(rul_pred, 0)

    return eol_pred, rul_pred


def evaluate_regression(y_true, y_pred, name):
    """Compute regression metrics."""
    rmse = np.sqrt(mean_squared_error(y_true, y_pred))
    mae = mean_absolute_error(y_true, y_pred)
    r2 = r2_score(y_true, y_pred)
    return {"model": name, "RMSE": rmse, "MAE": mae, "R2": r2}


def evaluate_classification(y_true, y_pred, name):
    """Compute classification metrics."""
    acc = accuracy_score(y_true, y_pred)
    prec = precision_score(y_true, y_pred, zero_division=0)
    rec = recall_score(y_true, y_pred, zero_division=0)
    f1 = f1_score(y_true, y_pred, zero_division=0)
    return {"model": name, "Accuracy": acc, "Precision": prec, "Recall": rec, "F1": f1}


def lobo_cross_validation(df):
    """
    Leave-One-Battery-Out cross-validation.
    Each battery is held out as test set once.
    """
    batteries = df["Battery_ID"].unique()
    print(f"\n{'='*80}")
    print("LEAVE-ONE-BATTERY-OUT CROSS-VALIDATION")
    print(f"{'='*80}\n")

    lobo_results = []
    predictions = []

    for test_battery in batteries:
        print(f"{'='*80}")
        print(f"Fold: Test on {test_battery}")
        print(f"{'='*80}")

        # Split
        train_mask = df["Battery_ID"] != test_battery
        test_mask = df["Battery_ID"] == test_battery

        train_df = df[train_mask].copy()
        test_df = df[test_mask].copy()

        train_batteries = train_df["Battery_ID"].unique().tolist()
        print(f"Train batteries: {train_batteries} ({len(train_df)} samples)")
        print(f"Test battery:    {test_battery} ({len(test_df)} samples)")

        # Prepare features and labels
        X_train = train_df[FEATURES].values
        X_test = test_df[FEATURES].values

        y_train_soh = train_df["SoH"].values
        y_test_soh = test_df["SoH"].values

        y_train_eol = train_df["Reached_EOL"].values
        y_test_eol = test_df["Reached_EOL"].values

        y_train_rul = train_df["RUL"].values
        y_test_rul = test_df["RUL"].values

        # Standardize features (fit on train only)
        scaler = StandardScaler()
        X_train = scaler.fit_transform(X_train)
        X_test = scaler.transform(X_test)

        # Train models
        print("\nTraining SoH model...")
        soh_model = train_soh_model(X_train, y_train_soh)

        print("Training RUL models (two-stage)...")
        rul_clf, rul_reg = train_rul_models(X_train, y_train_eol, y_train_rul)

        # Predict
        soh_pred = soh_model.predict(X_test)
        eol_pred, rul_pred = predict_rul_twostage(rul_clf, rul_reg, X_test)

        # Evaluate
        print("\n--- Evaluation Results ---")
        soh_metrics = evaluate_regression(y_test_soh, soh_pred, "SoH")
        print(f"SoH:  RMSE={soh_metrics['RMSE']:.4f}, MAE={soh_metrics['MAE']:.4f}, R²={soh_metrics['R2']:.4f}")

        eol_metrics = evaluate_classification(y_test_eol, eol_pred, "RUL_Classifier")
        print(
            f"EOL Classifier: Acc={eol_metrics['Accuracy']:.4f}, "
            f"Prec={eol_metrics['Precision']:.4f}, Rec={eol_metrics['Recall']:.4f}, "
            f"F1={eol_metrics['F1']:.4f}"
        )

        rul_metrics = evaluate_regression(y_test_rul, rul_pred, "RUL")
        print(f"RUL:  RMSE={rul_metrics['RMSE']:.4f}, MAE={rul_metrics['MAE']:.4f}, R²={rul_metrics['R2']:.4f}")

        # Store results
        fold_result = {
            "test_battery": test_battery,
            "train_batteries": ",".join(train_batteries),
            "n_train": len(train_df),
            "n_test": len(test_df),
            **{f"soh_{k}": v for k, v in soh_metrics.items() if k != "model"},
            **{f"eol_{k}": v for k, v in eol_metrics.items() if k != "model"},
            **{f"rul_{k}": v for k, v in rul_metrics.items() if k != "model"},
        }
        lobo_results.append(fold_result)

        # Store predictions for detailed analysis
        for i, row in test_df.iterrows():
            idx = test_df.index.get_loc(i)
            predictions.append(
                {
                    "test_battery": test_battery,
                    "Battery_ID": row["Battery_ID"],
                    "Cycle": row["Cycle"],
                    "SoH_true": row["SoH"],
                    "SoH_pred": soh_pred[idx],
                    "RUL_true": row["RUL"],
                    "RUL_pred": rul_pred[idx],
                    "Reached_EOL_true": row["Reached_EOL"],
                    "Reached_EOL_pred": eol_pred[idx],
                }
            )

        print()

    return pd.DataFrame(lobo_results), pd.DataFrame(predictions)


def train_final_models(df):
    """
    Train final models on ALL data for deployment.
    These are saved for future use, but LOBO results show generalization.
    """
    print(f"\n{'='*80}")
    print("TRAINING FINAL MODELS ON ALL DATA")
    print(f"{'='*80}\n")

    X = df[FEATURES].values
    y_soh = df["SoH"].values
    y_eol = df["Reached_EOL"].values
    y_rul = df["RUL"].values

    # Fit scaler on all data
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    # Train models
    print("Training SoH model...")
    soh_model = train_soh_model(X_scaled, y_soh)

    print("Training RUL models...")
    rul_clf, rul_reg = train_rul_models(X_scaled, y_eol, y_rul)

    # Save models
    os.makedirs(MODEL_DIR, exist_ok=True)
    joblib.dump(scaler, os.path.join(MODEL_DIR, "scaler.pkl"))
    joblib.dump(soh_model, os.path.join(MODEL_DIR, "soh_model.pkl"))
    joblib.dump(rul_clf, os.path.join(MODEL_DIR, "rul_classifier.pkl"))
    joblib.dump(rul_reg, os.path.join(MODEL_DIR, "rul_regressor.pkl"))

    # Save metadata
    metadata = {
        "features": FEATURES,
        "n_samples": len(df),
        "batteries": df["Battery_ID"].unique().tolist(),
        "models": {
            "soh": "RandomForestRegressor(n_estimators=100, max_depth=10)",
            "rul_classifier": "RandomForestClassifier(n_estimators=100, max_depth=10)",
            "rul_regressor": "RandomForestRegressor(n_estimators=100, max_depth=10)",
        },
        "random_state": RANDOM_STATE,
    }
    with open(os.path.join(MODEL_DIR, "metadata.json"), "w") as f:
        json.dump(metadata, f, indent=2)

    print(f"\nSaved models to {MODEL_DIR}/")
    print("  - scaler.pkl")
    print("  - soh_model.pkl")
    print("  - rul_classifier.pkl")
    print("  - rul_regressor.pkl")
    print("  - metadata.json")


def main():
    # Load data
    df = load_data()

    # LOBO cross-validation
    lobo_results, predictions = lobo_cross_validation(df)

    # Compute aggregate metrics
    print(f"\n{'='*80}")
    print("AGGREGATE LOBO RESULTS (mean ± std across 3 folds)")
    print(f"{'='*80}\n")

    metrics_cols = [c for c in lobo_results.columns if c not in ["test_battery", "train_batteries", "n_train", "n_test"]]
    aggregate = lobo_results[metrics_cols].agg(["mean", "std"]).T
    aggregate.columns = ["mean", "std"]

    print("SoH Regression:")
    for metric in ["soh_RMSE", "soh_MAE", "soh_R2"]:
        mean_val = aggregate.loc[metric, "mean"]
        std_val = aggregate.loc[metric, "std"]
        print(f"  {metric.replace('soh_', '')}: {mean_val:.4f} ± {std_val:.4f}")

    print("\nEOL Classification:")
    for metric in ["eol_Accuracy", "eol_Precision", "eol_Recall", "eol_F1"]:
        mean_val = aggregate.loc[metric, "mean"]
        std_val = aggregate.loc[metric, "std"]
        print(f"  {metric.replace('eol_', '')}: {mean_val:.4f} ± {std_val:.4f}")

    print("\nRUL Regression:")
    for metric in ["rul_RMSE", "rul_MAE", "rul_R2"]:
        mean_val = aggregate.loc[metric, "mean"]
        std_val = aggregate.loc[metric, "std"]
        print(f"  {metric.replace('rul_', '')}: {mean_val:.4f} ± {std_val:.4f}")

    # Save results
    os.makedirs(RESULTS_DIR, exist_ok=True)
    lobo_results.to_csv(os.path.join(RESULTS_DIR, "lobo_cv_results.csv"), index=False)
    predictions.to_csv(os.path.join(RESULTS_DIR, "lobo_predictions.csv"), index=False)

    aggregate_save = aggregate.reset_index()
    aggregate_save.columns = ["metric", "mean", "std"]
    aggregate_save.to_csv(os.path.join(RESULTS_DIR, "training_metrics.csv"), index=False)

    print(f"\nSaved results to {RESULTS_DIR}/")
    print("  - lobo_cv_results.csv (per-fold metrics)")
    print("  - lobo_predictions.csv (all predictions)")
    print("  - training_metrics.csv (aggregate metrics)")

    # Train final models on all data
    train_final_models(df)

    print(f"\n{'='*80}")
    print("TRAINING COMPLETE")
    print(f"{'='*80}")
    print("\nNext step: run `python src/evaluate_models.py` for detailed analysis.")


if __name__ == "__main__":
    main()
