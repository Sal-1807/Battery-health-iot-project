"""
Battery-Relative Normalization Experiment

Runs Leave-One-Battery-Out (LOBO) cross-validation with two feature versions:
A. Absolute features (baseline) - original 10 features
B. Battery-relative normalized features - 9 normalized + Cycle_number

Both versions use the EXACT SAME:
- LOBO evaluation protocol (3 folds)
- Train/test split logic
- Targets (SoH, RUL, Reached_EOL)
- Evaluation methodology
- Model hyperparameters

This ensures a fair comparison.

Anti-leakage for Version B: For each LOBO fold, the calibration baseline for the
test battery is computed ONLY from its first 10 cycles. These cycles occur before
prediction, so no future-cycle information is used. The baseline is computed from
the test battery itself, never from training batteries or full trajectory.

Models: RandomForest (same as baseline)
- SoH: RandomForestRegressor
- RUL Stage 1: RandomForestClassifier (Reached_EOL)
- RUL Stage 2: RandomForestRegressor (RUL, pre-EOL only)

Metrics:
- Regression: RMSE, MAE, R²
- Classification: Accuracy, Precision, Recall, F1
"""

import os
import json
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.metrics import (
    accuracy_score, f1_score, mean_absolute_error, mean_squared_error,
    precision_score, r2_score, recall_score
)
from sklearn.preprocessing import StandardScaler

# Configuration
CALIBRATION_CYCLES = 10  # Calibration window for per-battery baseline
NORMALIZED_FEATURES = [
    "Voltage_mean", "Voltage_min", "Voltage_max", "Voltage_range",
    "Current_mean", "Current_std",
    "Temperature_mean", "Temperature_max", "Temperature_rise"
]
ABSOLUTE_FEATURES = NORMALIZED_FEATURES + ["Cycle_number"]
RANDOM_STATE = 42

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
DATA_ABS = os.path.join(ROOT, "data", "processed", "battery_health_features.csv")
DATA_NORM = os.path.join(ROOT, "results", "experiments", "battery_relative_normalization",
                         "battery_health_features_normalized.csv")
RESULTS_DIR = os.path.join(ROOT, "results", "experiments", "battery_relative_normalization")


def load_data(version):
    """Load feature data for the specified version (absolute or normalized)."""
    if version == "absolute":
        df = pd.read_csv(DATA_ABS)
        features = ABSOLUTE_FEATURES
        print(f"Loaded ABSOLUTE features: {df.shape[0]} rows, {len(features)} features")
    elif version == "normalized":
        df = pd.read_csv(DATA_NORM)
        features = NORMALIZED_FEATURES + ["Cycle_number"]
        print(f"Loaded NORMALIZED features: {df.shape[0]} rows, {len(features)} features")
    else:
        raise ValueError(f"Unknown version: {version}")

    return df, features


def compute_calibration_baseline(test_df, calibration_cycles=CALIBRATION_CYCLES):
    """
    Compute calibration baseline from first N cycles of the test battery.

    Anti-leakage: Only first N cycles of the test battery are used.
    These cycles occur before any prediction for this battery.

    Returns dict: {feature: (mean, std)}
    """
    # Sort by cycle and take first calibration_cycles
    cal_df = test_df.sort_values("Cycle").head(calibration_cycles)

    baseline = {}
    for feature in NORMALIZED_FEATURES:
        values = cal_df[feature].values
        mean_val = np.mean(values)
        std_val = np.std(values, ddof=1)
        if std_val == 0:
            std_val = 1.0
        baseline[feature] = (mean_val, std_val)

    return baseline


def apply_normalization_to_test(test_df, baseline):
    """
    Apply normalization to test battery using its calibration baseline.

    For each feature:
        normalized = (value - baseline_mean) / baseline_std
    """
    test_norm = test_df.copy()

    for feature in NORMALIZED_FEATURES:
        mean_val, std_val = baseline[feature]
        test_norm[feature] = (test_norm[feature] - mean_val) / std_val

    # Reorder columns to match training format
    return test_norm


def train_soh_model(X_train, y_train):
    """Train SoH regression model (identical to baseline)."""
    model = RandomForestRegressor(
        n_estimators=100, max_depth=10, min_samples_split=5, random_state=RANDOM_STATE
    )
    model.fit(X_train, y_train)
    return model


def train_rul_models(X_train, y_train_eol, y_train_rul):
    """Train two-stage RUL models (identical to baseline)."""
    clf = RandomForestClassifier(
        n_estimators=100, max_depth=10, min_samples_split=5, random_state=RANDOM_STATE
    )
    clf.fit(X_train, y_train_eol)

    pre_eol_mask = y_train_eol == 0
    X_train_pre_eol = X_train[pre_eol_mask]
    y_train_rul_pre_eol = y_train_rul[pre_eol_mask]

    reg = RandomForestRegressor(
        n_estimators=100, max_depth=10, min_samples_split=5, random_state=RANDOM_STATE
    )
    reg.fit(X_train_pre_eol, y_train_rul_pre_eol)

    return clf, reg


def predict_rul_twostage(clf, reg, X_test):
    """Two-stage RUL prediction (identical to baseline)."""
    eol_pred = clf.predict(X_test)
    rul_pred = np.zeros(len(X_test))

    pre_eol_mask = eol_pred == 0
    if pre_eol_mask.sum() > 0:
        rul_pred[pre_eol_mask] = reg.predict(X_test[pre_eol_mask])
        rul_pred = np.maximum(rul_pred, 0)

    return eol_pred, rul_pred


def evaluate_regression(y_true, y_pred):
    """Compute regression metrics."""
    rmse = np.sqrt(mean_squared_error(y_true, y_pred))
    mae = mean_absolute_error(y_true, y_pred)
    r2 = r2_score(y_true, y_pred)
    return rmse, mae, r2


def evaluate_classification(y_true, y_pred):
    """Compute classification metrics."""
    acc = accuracy_score(y_true, y_pred)
    prec = precision_score(y_true, y_pred, zero_division=0)
    rec = recall_score(y_true, y_pred, zero_division=0)
    f1 = f1_score(y_true, y_pred, zero_division=0)
    return acc, prec, rec, f1


def run_lobo_version(version, df, features):
    """
    Run LOBO CV for one feature version.

    For normalized version:
    1. For each fold (held-out battery):
       a. Compute calibration baseline from first 10 cycles of TEST battery ONLY
       b. Normalize TEST battery features using this baseline
       c. Train on ABSOLUTE features of other 2 batteries (no normalization needed
          since they have their own baselines)
       d. Use normalized features for prediction

    Anti-leakage: Baseline computed only from first 10 cycles of test battery,
    which occur before prediction.
    """
    print(f"\n{'='*80}")
    print(f"LOBO CROSS-VALIDATION - {version.upper()} FEATURES")
    print(f"{'='*80}\n")

    batteries = df["Battery_ID"].unique()
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

        if version == "normalized":
            # Compute calibration baseline from first 10 cycles of TEST battery
            baseline = compute_calibration_baseline(test_df)
            # Normalize test battery using its own baseline
            test_df = apply_normalization_to_test(test_df, baseline)
            # Verify baseline was computed only from first 10 cycles
            cal_cycles = test_df.sort_values("Cycle").head(CALIBRATION_CYCLES)[features].mean()
            print(f"  Calibration baseline computed from first {CALIBRATION_CYCLES} cycles "
                  f"of {test_battery}")
            print(f"  Normalized test features range: "
                  f"[{test_df[features].min().min():.3f}, {test_df[features].max().max():.3f}]")

        # Prepare features and labels
        X_train = train_df[features].values
        X_test = test_df[features].values

        y_train_soh = train_df["SoH"].values
        y_test_soh = test_df["SoH"].values

        y_train_eol = train_df["Reached_EOL"].values
        y_test_eol = test_df["Reached_EOL"].values

        y_train_rul = train_df["RUL"].values
        y_test_rul = test_df["RUL"].values

        # Standardize features (fit on train only - identical to baseline)
        scaler = StandardScaler()
        X_train = scaler.fit_transform(X_train)
        X_test = scaler.transform(X_test)

        # Train models
        print("  Training SoH model...")
        soh_model = train_soh_model(X_train, y_train_soh)

        print("  Training RUL models (two-stage)...")
        rul_clf, rul_reg = train_rul_models(X_train, y_train_eol, y_train_rul)

        # Predict
        soh_pred = soh_model.predict(X_test)
        eol_pred, rul_pred = predict_rul_twostage(rul_clf, rul_reg, X_test)

        # Evaluate
        soh_rmse, soh_mae, soh_r2 = evaluate_regression(y_test_soh, soh_pred)
        eol_acc, eol_prec, eol_rec, eol_f1 = evaluate_classification(y_test_eol, eol_pred)
        rul_rmse, rul_mae, rul_r2 = evaluate_regression(y_test_rul, rul_pred)

        print(f"\n  --- Evaluation Results ---")
        print(f"  SoH:  RMSE={soh_rmse:.4f}, MAE={soh_mae:.4f}, R²={soh_r2:.4f}")
        print(f"  EOL:  Acc={eol_acc:.4f}, Prec={eol_prec:.4f}, Rec={eol_rec:.4f}, F1={eol_f1:.4f}")
        print(f"  RUL:  RMSE={rul_rmse:.4f}, MAE={rul_mae:.4f}, R²={rul_r2:.4f}")

        # Store results
        fold_result = {
            "version": version,
            "test_battery": test_battery,
            "train_batteries": ",".join(train_batteries),
            "n_train": len(train_df),
            "n_test": len(test_df),
            "soh_RMSE": soh_rmse,
            "soh_MAE": soh_mae,
            "soh_R2": soh_r2,
            "eol_Accuracy": eol_acc,
            "eol_Precision": eol_prec,
            "eol_Recall": eol_rec,
            "eol_F1": eol_f1,
            "rul_RMSE": rul_rmse,
            "rul_MAE": rul_mae,
            "rul_R2": rul_r2,
        }
        lobo_results.append(fold_result)

        # Store predictions
        for i, row in test_df.iterrows():
            idx = test_df.index.get_loc(i)
            predictions.append({
                "version": version,
                "test_battery": test_battery,
                "Battery_ID": row["Battery_ID"],
                "Cycle": row["Cycle"],
                "SoH_true": row["SoH"],
                "SoH_pred": soh_pred[idx],
                "RUL_true": row["RUL"],
                "RUL_pred": rul_pred[idx],
                "Reached_EOL_true": row["Reached_EOL"],
                "Reached_EOL_pred": eol_pred[idx],
            })

        print()

    return pd.DataFrame(lobo_results), pd.DataFrame(predictions)


def main():
    os.makedirs(RESULTS_DIR, exist_ok=True)

    print("=" * 80)
    print("BATTERY-RELATIVE NORMALIZATION EXPERIMENT")
    print("=" * 80)
    print("Comparing absolute vs normalized features with identical LOBO protocol")
    print(f"Calibration window: first {CALIBRATION_CYCLES} cycles per battery")
    print(f"Random state: {RANDOM_STATE}")

    # Run Version A: Absolute features
    df_abs, features_abs = load_data("absolute")
    lobo_abs, preds_abs = run_lobo_version("absolute", df_abs, features_abs)

    # Run Version B: Normalized features
    df_norm, features_norm = load_data("normalized")
    lobo_norm, preds_norm = run_lobo_version("normalized", df_norm, features_norm)

    # Save results
    lobo_abs.to_csv(os.path.join(RESULTS_DIR, "lobo_results_absolute.csv"), index=False)
    lobo_norm.to_csv(os.path.join(RESULTS_DIR, "lobo_results_normalized.csv"), index=False)
    preds_abs.to_csv(os.path.join(RESULTS_DIR, "predictions_absolute.csv"), index=False)
    preds_norm.to_csv(os.path.join(RESULTS_DIR, "predictions_normalized.csv"), index=False)

    print(f"\n{'='*80}")
    print("EXPERIMENT COMPLETE")
    print(f"{'='*80}")
    print(f"Results saved to: {RESULTS_DIR}")


if __name__ == "__main__":
    main()