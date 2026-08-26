"""
Comparison Analysis: Absolute vs Battery-Relative Normalized Features

Loads results from both LOBO experiments and generates:
1. Side-by-side metrics comparison
2. Per-battery breakdown
3. Aggregate statistics
4. Most informative plots
"""

import os
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
RESULTS_DIR = os.path.join(ROOT, "results", "experiments", "battery_relative_normalization")
PLOTS_DIR = os.path.join(RESULTS_DIR, "plots")
os.makedirs(PLOTS_DIR, exist_ok=True)

# Baseline results
BASELINE_CSV = os.path.join(ROOT, "results", "lobo_cv_results.csv")


def load_results():
    """Load all result files."""
    abs_lobo = pd.read_csv(os.path.join(RESULTS_DIR, "lobo_results_absolute.csv"))
    norm_lobo = pd.read_csv(os.path.join(RESULTS_DIR, "lobo_results_normalized.csv"))
    baseline = pd.read_csv(BASELINE_CSV)
    return abs_lobo, norm_lobo, baseline


def print_comparison_table(abs_lobo, norm_lobo, baseline):
    """Print side-by-side comparison."""
    print("\n" + "=" * 120)
    print("RESULTS COMPARISON: ABSOLUTE vs BATTERY-RELATIVE NORMALIZED FEATURES")
    print("=" * 120)

    # Verify absolute results match baseline
    print("\n1. VERIFYING BASELINE REPRODUCTION (Absolute vs Original)")
    print("-" * 80)
    for _, row in abs_lobo.iterrows():
        battery = row["test_battery"]
        base_row = baseline[baseline["test_battery"] == battery].iloc[0]
        match = (
            abs(row["soh_RMSE"] - base_row["soh_RMSE"]) < 1e-6 and
            abs(row["rul_RMSE"] - base_row["rul_RMSE"]) < 1e-6
        )
        print(f"  {battery}: {'MATCH' if match else 'MISMATCH'}")

    # Per-battery comparison
    print("\n2. PER-BATTERY PERFORMANCE COMPARISON")
    print("-" * 80)

    metrics = ["soh_RMSE", "soh_MAE", "soh_R2", "eol_Accuracy", "eol_F1",
               "rul_RMSE", "rul_MAE", "rul_R2"]

    for battery in ["B0005", "B0006", "B0018"]:
        abs_row = abs_lobo[abs_lobo["test_battery"] == battery].iloc[0]
        norm_row = norm_lobo[norm_lobo["test_battery"] == battery].iloc[0]

        print(f"\n{battery}:")
        print(f"  {'Metric':<20} {'Absolute':>12} {'Normalized':>12} {'Delta':>12} {'Change%':>10}")
        print(f"  {'-'*66}")

        for metric in metrics:
            abs_val = abs_row[metric]
            norm_val = norm_row[metric]
            delta = norm_val - abs_val

            # Compute percentage change (lower is better for RMSE/MAE)
            if "RMSE" in metric or "MAE" in metric:
                change_pct = (delta / abs_val) * 100 if abs_val != 0 else 0
            elif "R2" in metric or "Accuracy" in metric or "F1" in metric:
                change_pct = (delta / abs_val) * 100 if abs_val != 0 else 0
            else:
                change_pct = 0

            # Mark improvements
            if "RMSE" in metric or "MAE" in metric:
                improved = delta < 0
            else:
                improved = delta > 0

            indicator = "[+]" if improved else "[-]" if abs(delta) > 0.001 else "[=]"
            print(f"  {metric:<20} {abs_val:>12.4f} {norm_val:>12.4f} {delta:>+12.4f} {change_pct:>+9.1f}% {indicator}")

    # Aggregate comparison
    print("\n3. AGGREGATE COMPARISON (Mean ± Std across 3 folds)")
    print("-" * 80)

    print(f"\n  {'Metric':<20} {'Absolute':>20} {'Normalized':>20} {'Improvement':>20}")
    print(f"  {'-'*80}")

    for metric in metrics:
        abs_mean = abs_lobo[metric].mean()
        abs_std = abs_lobo[metric].std()
        norm_mean = norm_lobo[metric].mean()
        norm_std = norm_lobo[metric].std()

        # Compute improvement percentage
        if "RMSE" in metric or "MAE" in metric:
            improvement = ((abs_mean - norm_mean) / abs_mean) * 100
        else:
            improvement = ((norm_mean - abs_mean) / abs_mean) * 100 if abs_mean != 0 else 0

        print(f"  {metric:<20} {abs_mean:>9.4f} ± {abs_std:<8.4f} "
              f"{norm_mean:>9.4f} ± {norm_std:<8.4f} {improvement:>+18.1f}%")

    return abs_lobo, norm_lobo


def plot_b0018_comparison(abs_preds, norm_preds):
    """Plot B0018 specific comparison."""
    b0018_abs = abs_preds[abs_preds["test_battery"] == "B0018"]
    b0018_norm = norm_preds[norm_preds["test_battery"] == "B0018"]

    fig, axes = plt.subplots(2, 2, figsize=(14, 10))

    # SoH: Predicted vs Actual
    ax = axes[0, 0]
    ax.scatter(b0018_abs["SoH_true"], b0018_abs["SoH_pred"],
               alpha=0.7, label="Absolute", c="blue", s=40)
    ax.scatter(b0018_norm["SoH_true"], b0018_norm["SoH_pred"],
               alpha=0.7, label="Normalized", c="green", s=40)
    ax.plot([60, 100], [60, 100], "r--", lw=2, label="Perfect")
    ax.set_xlabel("Actual SoH (%)")
    ax.set_ylabel("Predicted SoH (%)")
    ax.set_title("B0018 SoH Prediction")
    ax.legend()
    ax.grid(True, alpha=0.3)

    # RUL: Predicted vs Actual
    ax = axes[0, 1]
    ax.scatter(b0018_abs["RUL_true"], b0018_abs["RUL_pred"],
               alpha=0.7, label="Absolute", c="blue", s=40)
    ax.scatter(b0018_norm["RUL_true"], b0018_norm["RUL_pred"],
               alpha=0.7, label="Normalized", c="green", s=40)
    ax.plot([0, 100], [0, 100], "r--", lw=2, label="Perfect")
    ax.set_xlabel("Actual RUL (cycles)")
    ax.set_ylabel("Predicted RUL (cycles)")
    ax.set_title("B0018 RUL Prediction")
    ax.legend()
    ax.grid(True, alpha=0.3)

    # SoH: Time series
    ax = axes[1, 0]
    b0018_sorted = b0018_abs.sort_values("Cycle")
    ax.plot(b0018_sorted["Cycle"], b0018_sorted["SoH_true"],
            "o-", label="Actual", alpha=0.7, markersize=4)
    ax.plot(b0018_abs.sort_values("Cycle")["Cycle"],
            b0018_abs.sort_values("Cycle")["SoH_pred"],
            "s-", label="Absolute", alpha=0.7, markersize=4)
    b0018_norm_sorted = b0018_norm.sort_values("Cycle")
    ax.plot(b0018_norm_sorted["Cycle"], b0018_norm_sorted["SoH_pred"],
            "^-", label="Normalized", alpha=0.7, markersize=4)
    ax.set_xlabel("Cycle")
    ax.set_ylabel("SoH (%)")
    ax.set_title("B0018 SoH Time Series")
    ax.legend()
    ax.grid(True, alpha=0.3)

    # RUL: Time series
    ax = axes[1, 1]
    ax.plot(b0018_sorted["Cycle"], b0018_sorted["RUL_true"],
            "o-", label="Actual", alpha=0.7, markersize=4)
    ax.plot(b0018_abs.sort_values("Cycle")["Cycle"],
            b0018_abs.sort_values("Cycle")["RUL_pred"],
            "s-", label="Absolute", alpha=0.7, markersize=4)
    ax.plot(b0018_norm_sorted["Cycle"], b0018_norm_sorted["RUL_pred"],
            "^-", label="Normalized", alpha=0.7, markersize=4)
    ax.set_xlabel("Cycle")
    ax.set_ylabel("RUL (cycles)")
    ax.set_title("B0018 RUL Time Series")
    ax.legend()
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig(os.path.join(PLOTS_DIR, "b0018_comparison.png"), dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Saved: b0018_comparison.png")


def plot_aggregate_comparison(abs_lobo, norm_lobo):
    """Plot aggregate metrics comparison."""
    fig, axes = plt.subplots(2, 2, figsize=(12, 10))

    # SoH RMSE by battery
    ax = axes[0, 0]
    batteries = ["B0005", "B0006", "B0018"]
    x = np.arange(len(batteries))
    width = 0.35
    abs_vals = [abs_lobo[abs_lobo["test_battery"] == b]["soh_RMSE"].values[0] for b in batteries]
    norm_vals = [norm_lobo[norm_lobo["test_battery"] == b]["soh_RMSE"].values[0] for b in batteries]
    ax.bar(x - width/2, abs_vals, width, label="Absolute", color="blue", alpha=0.7)
    ax.bar(x + width/2, norm_vals, width, label="Normalized", color="green", alpha=0.7)
    ax.set_ylabel("RMSE")
    ax.set_title("SoH RMSE by Battery")
    ax.set_xticks(x)
    ax.set_xticklabels(batteries)
    ax.legend()
    ax.grid(True, alpha=0.3, axis="y")

    # RUL RMSE by battery
    ax = axes[0, 1]
    abs_vals = [abs_lobo[abs_lobo["test_battery"] == b]["rul_RMSE"].values[0] for b in batteries]
    norm_vals = [norm_lobo[norm_lobo["test_battery"] == b]["rul_RMSE"].values[0] for b in batteries]
    ax.bar(x - width/2, abs_vals, width, label="Absolute", color="blue", alpha=0.7)
    ax.bar(x + width/2, norm_vals, width, label="Normalized", color="green", alpha=0.7)
    ax.set_ylabel("RMSE")
    ax.set_title("RUL RMSE by Battery")
    ax.set_xticks(x)
    ax.set_xticklabels(batteries)
    ax.legend()
    ax.grid(True, alpha=0.3, axis="y")

    # R² by battery
    ax = axes[1, 0]
    abs_vals = [abs_lobo[abs_lobo["test_battery"] == b]["rul_R2"].values[0] for b in batteries]
    norm_vals = [norm_lobo[norm_lobo["test_battery"] == b]["rul_R2"].values[0] for b in batteries]
    ax.bar(x - width/2, abs_vals, width, label="Absolute", color="blue", alpha=0.7)
    ax.bar(x + width/2, norm_vals, width, label="Normalized", color="green", alpha=0.7)
    ax.set_ylabel("R²")
    ax.set_title("RUL R² by Battery")
    ax.set_xticks(x)
    ax.set_xticklabels(batteries)
    ax.legend()
    ax.grid(True, alpha=0.3, axis="y")

    # Aggregate metrics
    ax = axes[1, 1]
    agg_metrics = ["soh_RMSE", "rul_RMSE", "rul_R2"]
    agg_labels = ["SoH RMSE", "RUL RMSE", "RUL R²"]
    abs_agg = [abs_lobo[m].mean() for m in agg_metrics]
    norm_agg = [norm_lobo[m].mean() for m in agg_metrics]

    x2 = np.arange(len(agg_metrics))
    ax.bar(x2 - width/2, abs_agg, width, label="Absolute", color="blue", alpha=0.7)
    ax.bar(x2 + width/2, norm_agg, width, label="Normalized", color="green", alpha=0.7)
    ax.set_xticks(x2)
    ax.set_xticklabels(agg_labels)
    ax.set_title("Aggregate Metrics")
    ax.legend()
    ax.grid(True, alpha=0.3, axis="y")

    plt.tight_layout()
    plt.savefig(os.path.join(PLOTS_DIR, "aggregate_comparison.png"), dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Saved: aggregate_comparison.png")


def plot_residuals_comparison(abs_preds, norm_preds):
    """Plot residual comparison for B0018."""
    b0018_abs = abs_preds[abs_preds["test_battery"] == "B0018"]
    b0018_norm = norm_preds[norm_preds["test_battery"] == "B0018"]

    fig, axes = plt.subplots(2, 2, figsize=(14, 10))

    # SoH residuals - Absolute
    ax = axes[0, 0]
    residuals_abs = b0018_abs["SoH_true"] - b0018_abs["SoH_pred"]
    ax.scatter(b0018_abs["SoH_pred"], residuals_abs, alpha=0.7, c="blue", s=40)
    ax.axhline(0, color="r", linestyle="--", lw=2)
    ax.set_xlabel("Predicted SoH")
    ax.set_ylabel("Residual (True - Pred)")
    ax.set_title(f"B0018 SoH Residuals (Absolute)\nMean={residuals_abs.mean():.3f}, Std={residuals_abs.std():.3f}")
    ax.grid(True, alpha=0.3)

    # SoH residuals - Normalized
    ax = axes[0, 1]
    residuals_norm = b0018_norm["SoH_true"] - b0018_norm["SoH_pred"]
    ax.scatter(b0018_norm["SoH_pred"], residuals_norm, alpha=0.7, c="green", s=40)
    ax.axhline(0, color="r", linestyle="--", lw=2)
    ax.set_xlabel("Predicted SoH")
    ax.set_ylabel("Residual (True - Pred)")
    ax.set_title(f"B0018 SoH Residuals (Normalized)\nMean={residuals_norm.mean():.3f}, Std={residuals_norm.std():.3f}")
    ax.grid(True, alpha=0.3)

    # RUL residuals - Absolute
    ax = axes[1, 0]
    residuals_abs_rul = b0018_abs["RUL_true"] - b0018_abs["RUL_pred"]
    ax.scatter(b0018_abs["RUL_pred"], residuals_abs_rul, alpha=0.7, c="blue", s=40)
    ax.axhline(0, color="r", linestyle="--", lw=2)
    ax.set_xlabel("Predicted RUL")
    ax.set_ylabel("Residual (True - Pred)")
    ax.set_title(f"B0018 RUL Residuals (Absolute)\nMean={residuals_abs_rul.mean():.3f}, Std={residuals_abs_rul.std():.3f}")
    ax.grid(True, alpha=0.3)

    # RUL residuals - Normalized
    ax = axes[1, 1]
    residuals_norm_rul = b0018_norm["RUL_true"] - b0018_norm["RUL_pred"]
    ax.scatter(b0018_norm["RUL_pred"], residuals_norm_rul, alpha=0.7, c="green", s=40)
    ax.axhline(0, color="r", linestyle="--", lw=2)
    ax.set_xlabel("Predicted RUL")
    ax.set_ylabel("Residual (True - Pred)")
    ax.set_title(f"B0018 RUL Residuals (Normalized)\nMean={residuals_norm_rul.mean():.3f}, Std={residuals_norm_rul.std():.3f}")
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig(os.path.join(PLOTS_DIR, "b0018_residuals.png"), dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Saved: b0018_residuals.png")


def main():
    print("Loading results...")
    abs_lobo, norm_lobo, baseline = load_results()

    print("\nPrinting comparison...")
    print_comparison_table(abs_lobo, norm_lobo, baseline)

    print("\nLoading predictions for plots...")
    abs_preds = pd.read_csv(os.path.join(RESULTS_DIR, "predictions_absolute.csv"))
    norm_preds = pd.read_csv(os.path.join(RESULTS_DIR, "predictions_normalized.csv"))

    print("\nGenerating plots...")
    plot_aggregate_comparison(abs_lobo, norm_lobo)
    plot_b0018_comparison(abs_preds, norm_preds)
    plot_residuals_comparison(abs_preds, norm_preds)

    print(f"\nAll plots saved to: {PLOTS_DIR}")


if __name__ == "__main__":
    main()