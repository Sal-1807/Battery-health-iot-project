"""
Model evaluation and visualization for baseline ML models.

Input : models/*.pkl, results/lobo_predictions.csv
Output: results/model_evaluation_report.txt, results/plots/model_*.png

Generates:
- Detailed evaluation report
- Prediction vs actual plots (SoH and RUL)
- Residual analysis plots
- Per-battery performance breakdown
- Feature importance (from final models)
"""

import os

import joblib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.metrics import confusion_matrix, mean_absolute_error, mean_squared_error, r2_score

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_DIR = os.path.join(ROOT, "models")
RESULTS_DIR = os.path.join(ROOT, "results")
PLOTS_DIR = os.path.join(RESULTS_DIR, "plots")

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


def load_predictions():
    """Load LOBO predictions."""
    pred_file = os.path.join(RESULTS_DIR, "lobo_predictions.csv")
    if not os.path.exists(pred_file):
        raise FileNotFoundError(f"{pred_file} not found. Run train_models.py first.")
    return pd.read_csv(pred_file)


def load_models():
    """Load trained models."""
    scaler = joblib.load(os.path.join(MODEL_DIR, "scaler.pkl"))
    soh_model = joblib.load(os.path.join(MODEL_DIR, "soh_model.pkl"))
    rul_clf = joblib.load(os.path.join(MODEL_DIR, "rul_classifier.pkl"))
    rul_reg = joblib.load(os.path.join(MODEL_DIR, "rul_regressor.pkl"))
    return scaler, soh_model, rul_clf, rul_reg


def plot_predictions_vs_actual(df, target, ylabel, filename):
    """Scatter plot: predicted vs actual."""
    fig, axes = plt.subplots(1, 3, figsize=(15, 4))
    batteries = df["Battery_ID"].unique()

    for i, battery in enumerate(batteries):
        ax = axes[i]
        subset = df[df["Battery_ID"] == battery]
        y_true = subset[f"{target}_true"]
        y_pred = subset[f"{target}_pred"]

        ax.scatter(y_true, y_pred, alpha=0.6, s=30)
        ax.plot([y_true.min(), y_true.max()], [y_true.min(), y_true.max()], "r--", lw=2, label="Perfect prediction")

        rmse = np.sqrt(mean_squared_error(y_true, y_pred))
        mae = mean_absolute_error(y_true, y_pred)
        r2 = r2_score(y_true, y_pred)

        ax.set_title(f"{battery}\nRMSE={rmse:.2f}, MAE={mae:.2f}, R²={r2:.3f}")
        ax.set_xlabel(f"Actual {ylabel}")
        ax.set_ylabel(f"Predicted {ylabel}")
        ax.legend(loc="upper left")
        ax.grid(alpha=0.3)

    plt.tight_layout()
    os.makedirs(PLOTS_DIR, exist_ok=True)
    plt.savefig(os.path.join(PLOTS_DIR, filename), dpi=150)
    plt.close()
    print(f"  saved {filename}")


def plot_residuals(df, target, ylabel, filename):
    """Residual plots."""
    fig, axes = plt.subplots(1, 3, figsize=(15, 4))
    batteries = df["Battery_ID"].unique()

    for i, battery in enumerate(batteries):
        ax = axes[i]
        subset = df[df["Battery_ID"] == battery]
        y_true = subset[f"{target}_true"]
        y_pred = subset[f"{target}_pred"]
        residuals = y_true - y_pred

        ax.scatter(y_pred, residuals, alpha=0.6, s=30)
        ax.axhline(0, color="r", linestyle="--", lw=2)
        ax.set_title(f"{battery}")
        ax.set_xlabel(f"Predicted {ylabel}")
        ax.set_ylabel("Residual (Actual - Predicted)")
        ax.grid(alpha=0.3)

    plt.tight_layout()
    plt.savefig(os.path.join(PLOTS_DIR, filename), dpi=150)
    plt.close()
    print(f"  saved {filename}")


def plot_time_series(df, target, ylabel, filename):
    """Time-series plot: prediction vs actual over cycles."""
    fig, axes = plt.subplots(3, 1, figsize=(12, 9))
    batteries = df["Battery_ID"].unique()

    for i, battery in enumerate(batteries):
        ax = axes[i]
        subset = df[df["Battery_ID"] == battery].sort_values("Cycle")
        cycles = subset["Cycle"]
        y_true = subset[f"{target}_true"]
        y_pred = subset[f"{target}_pred"]

        ax.plot(cycles, y_true, "o-", label="Actual", alpha=0.7, markersize=4)
        ax.plot(cycles, y_pred, "s-", label="Predicted", alpha=0.7, markersize=4)
        ax.set_title(battery)
        ax.set_xlabel("Cycle")
        ax.set_ylabel(ylabel)
        ax.legend()
        ax.grid(alpha=0.3)

    plt.tight_layout()
    plt.savefig(os.path.join(PLOTS_DIR, filename), dpi=150)
    plt.close()
    print(f"  saved {filename}")


def plot_confusion_matrix_all(df, filename):
    """Confusion matrix for EOL classification."""
    fig, axes = plt.subplots(1, 3, figsize=(15, 4))
    batteries = df["Battery_ID"].unique()

    for i, battery in enumerate(batteries):
        ax = axes[i]
        subset = df[df["Battery_ID"] == battery]
        y_true = subset["Reached_EOL_true"]
        y_pred = subset["Reached_EOL_pred"]

        cm = confusion_matrix(y_true, y_pred)
        im = ax.imshow(cm, cmap="Blues", interpolation="nearest")
        ax.set_title(battery)
        ax.set_xlabel("Predicted")
        ax.set_ylabel("Actual")
        ax.set_xticks([0, 1])
        ax.set_yticks([0, 1])
        ax.set_xticklabels(["Pre-EOL", "At/Post-EOL"])
        ax.set_yticklabels(["Pre-EOL", "At/Post-EOL"])

        # Add text annotations
        for row in range(cm.shape[0]):
            for col in range(cm.shape[1]):
                ax.text(col, row, str(cm[row, col]), ha="center", va="center", color="black", fontsize=14)

        plt.colorbar(im, ax=ax)

    plt.tight_layout()
    plt.savefig(os.path.join(PLOTS_DIR, filename), dpi=150)
    plt.close()
    print(f"  saved {filename}")


def plot_feature_importance(model, features, title, filename):
    """Bar plot of feature importances."""
    importances = model.feature_importances_
    indices = np.argsort(importances)[::-1]

    plt.figure(figsize=(10, 6))
    plt.barh(range(len(features)), importances[indices], align="center")
    plt.yticks(range(len(features)), [features[i] for i in indices])
    plt.xlabel("Feature Importance")
    plt.title(title)
    plt.gca().invert_yaxis()
    plt.grid(axis="x", alpha=0.3)
    plt.tight_layout()
    plt.savefig(os.path.join(PLOTS_DIR, filename), dpi=150)
    plt.close()
    print(f"  saved {filename}")


def generate_evaluation_report(df):
    """Generate comprehensive text report."""
    report_lines = []
    report_lines.append("=" * 100)
    report_lines.append("MODEL EVALUATION REPORT")
    report_lines.append("=" * 100)
    report_lines.append("")

    # Overall metrics
    report_lines.append("OVERALL METRICS (Leave-One-Battery-Out CV)")
    report_lines.append("-" * 100)

    # SoH
    soh_rmse = np.sqrt(mean_squared_error(df["SoH_true"], df["SoH_pred"]))
    soh_mae = mean_absolute_error(df["SoH_true"], df["SoH_pred"])
    soh_r2 = r2_score(df["SoH_true"], df["SoH_pred"])
    report_lines.append(f"SoH Regression:  RMSE={soh_rmse:.4f}  MAE={soh_mae:.4f}  R²={soh_r2:.4f}")

    # EOL
    eol_acc = (df["Reached_EOL_true"] == df["Reached_EOL_pred"]).mean()
    report_lines.append(f"EOL Classification:  Accuracy={eol_acc:.4f}")

    # RUL
    rul_rmse = np.sqrt(mean_squared_error(df["RUL_true"], df["RUL_pred"]))
    rul_mae = mean_absolute_error(df["RUL_true"], df["RUL_pred"])
    rul_r2 = r2_score(df["RUL_true"], df["RUL_pred"])
    report_lines.append(f"RUL Regression:  RMSE={rul_rmse:.4f}  MAE={rul_mae:.4f}  R²={rul_r2:.4f}")

    report_lines.append("")

    # Per-battery breakdown
    report_lines.append("PER-BATTERY PERFORMANCE (held-out test set)")
    report_lines.append("-" * 100)

    for battery in df["Battery_ID"].unique():
        subset = df[df["Battery_ID"] == battery]
        report_lines.append(f"\n{battery} (n={len(subset)} cycles)")
        report_lines.append("-" * 50)

        # SoH
        soh_rmse_b = np.sqrt(mean_squared_error(subset["SoH_true"], subset["SoH_pred"]))
        soh_mae_b = mean_absolute_error(subset["SoH_true"], subset["SoH_pred"])
        soh_r2_b = r2_score(subset["SoH_true"], subset["SoH_pred"])
        report_lines.append(f"  SoH:  RMSE={soh_rmse_b:.4f}  MAE={soh_mae_b:.4f}  R²={soh_r2_b:.4f}")

        # EOL
        eol_acc_b = (subset["Reached_EOL_true"] == subset["Reached_EOL_pred"]).mean()
        cm = confusion_matrix(subset["Reached_EOL_true"], subset["Reached_EOL_pred"])
        report_lines.append(f"  EOL:  Accuracy={eol_acc_b:.4f}")
        report_lines.append(f"        Confusion Matrix: TN={cm[0,0]}, FP={cm[0,1]}, FN={cm[1,0]}, TP={cm[1,1]}")

        # RUL
        rul_rmse_b = np.sqrt(mean_squared_error(subset["RUL_true"], subset["RUL_pred"]))
        rul_mae_b = mean_absolute_error(subset["RUL_true"], subset["RUL_pred"])
        rul_r2_b = r2_score(subset["RUL_true"], subset["RUL_pred"])
        report_lines.append(f"  RUL:  RMSE={rul_rmse_b:.4f}  MAE={rul_mae_b:.4f}  R²={rul_r2_b:.4f}")

        # RUL breakdown: pre-EOL only
        pre_eol = subset[subset["RUL_true"] > 0]
        if len(pre_eol) > 0:
            rul_rmse_pre = np.sqrt(mean_squared_error(pre_eol["RUL_true"], pre_eol["RUL_pred"]))
            rul_mae_pre = mean_absolute_error(pre_eol["RUL_true"], pre_eol["RUL_pred"])
            rul_r2_pre = r2_score(pre_eol["RUL_true"], pre_eol["RUL_pred"])
            report_lines.append(
                f"  RUL (pre-EOL only, n={len(pre_eol)}):  "
                f"RMSE={rul_rmse_pre:.4f}  MAE={rul_mae_pre:.4f}  R²={rul_r2_pre:.4f}"
            )

    report_lines.append("")
    report_lines.append("=" * 100)
    report_lines.append("END OF REPORT")
    report_lines.append("=" * 100)

    report_text = "\n".join(report_lines)

    # Save to file
    report_file = os.path.join(RESULTS_DIR, "model_evaluation_report.txt")
    with open(report_file, "w") as f:
        f.write(report_text)

    print(report_text)
    print(f"\nSaved report to {report_file}")


def main():
    print(f"{'='*100}")
    print("MODEL EVALUATION")
    print(f"{'='*100}\n")

    # Load predictions
    print("Loading predictions...")
    df = load_predictions()
    print(f"  loaded {len(df)} predictions across {df['Battery_ID'].nunique()} batteries")

    # Load models for feature importance
    print("\nLoading models...")
    scaler, soh_model, rul_clf, rul_reg = load_models()
    print("  models loaded")

    # Generate plots
    print("\nGenerating plots...")
    print("  Prediction vs Actual plots:")
    plot_predictions_vs_actual(df, "SoH", "SoH (%)", "model_soh_predictions.png")
    plot_predictions_vs_actual(df, "RUL", "RUL (cycles)", "model_rul_predictions.png")

    print("\n  Residual plots:")
    plot_residuals(df, "SoH", "SoH (%)", "model_soh_residuals.png")
    plot_residuals(df, "RUL", "RUL (cycles)", "model_rul_residuals.png")

    print("\n  Time-series plots:")
    plot_time_series(df, "SoH", "SoH (%)", "model_soh_timeseries.png")
    plot_time_series(df, "RUL", "RUL (cycles)", "model_rul_timeseries.png")

    print("\n  EOL confusion matrices:")
    plot_confusion_matrix_all(df, "model_eol_confusion_matrix.png")

    print("\n  Feature importance:")
    plot_feature_importance(soh_model, FEATURES, "SoH Model Feature Importance", "model_soh_feature_importance.png")
    plot_feature_importance(rul_clf, FEATURES, "EOL Classifier Feature Importance", "model_eol_feature_importance.png")
    plot_feature_importance(rul_reg, FEATURES, "RUL Regressor Feature Importance", "model_rul_feature_importance.png")

    # Generate text report
    print("\nGenerating evaluation report...")
    generate_evaluation_report(df)

    print(f"\n{'='*100}")
    print("EVALUATION COMPLETE")
    print(f"{'='*100}")
    print(f"\nAll results saved to {RESULTS_DIR}/")
    print(f"All plots saved to {PLOTS_DIR}/")


if __name__ == "__main__":
    main()
