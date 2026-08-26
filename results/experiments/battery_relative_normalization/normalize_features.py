"""
Battery-Relative Feature Normalization

Normalizes sensor-derived features using each battery's initial calibration window
(first 10 cycles) as the baseline. This creates battery-relative features that
express deviations from each battery's own fresh-state reference.

Anti-leakage: For each battery, the baseline is computed ONLY from its first 10
cycles, which occur before any prediction is made for that battery. No future
cycle information is used.

Features normalized (9):
- Voltage_mean, Voltage_min, Voltage_max, Voltage_range
- Current_mean, Current_std
- Temperature_mean, Temperature_max, Temperature_rise

Feature NOT normalized (1):
- Cycle_number (cycle index, not a sensor measurement)

Output: battery_health_features_normalized.csv
"""

import os
import numpy as np
import pandas as pd

# Configuration
CALIBRATION_CYCLES = 10  # Number of initial cycles to use as baseline
SENSOR_FEATURES = [
    "Voltage_mean", "Voltage_min", "Voltage_max", "Voltage_range",
    "Current_mean", "Current_std",
    "Temperature_mean", "Temperature_max", "Temperature_rise"
]
# Cycle_number is excluded from normalization

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
IN_CSV = os.path.join(ROOT, "data", "processed", "battery_health_features.csv")
OUT_DIR = os.path.join(ROOT, "results", "experiments", "battery_relative_normalization")
OUT_CSV = os.path.join(OUT_DIR, "battery_health_features_normalized.csv")
OUT_BASELINE_STATS = os.path.join(OUT_DIR, "calibration_baseline_stats.csv")


def load_data():
    """Load the absolute features dataset."""
    df = pd.read_csv(IN_CSV)
    print(f"Loaded {IN_CSV}: {df.shape[0]} rows x {df.shape[1]} cols")
    print(f"  Batteries: {df['Battery_ID'].unique().tolist()}")
    print(f"  Rows per battery: {df.groupby('Battery_ID').size().to_dict()}")
    return df


def compute_calibration_baseline(df):
    """
    Compute calibration baseline statistics from first N cycles of each battery.

    Returns DataFrame with columns:
    - Battery_ID
    - feature (one per sensor feature)
    - baseline_mean
    - baseline_std
    - n_cycles (should equal CALIBRATION_CYCLES)
    """
    baselines = []

    for battery_id in df["Battery_ID"].unique():
        battery_df = df[df["Battery_ID"] == battery_id].sort_values("Cycle")

        # Select first CALIBRATION_CYCLES cycles
        cal_df = battery_df.head(CALIBRATION_CYCLES)

        if len(cal_df) < CALIBRATION_CYCLES:
            print(f"WARNING: {battery_id} has only {len(cal_df)} cycles, "
                  f"using all for calibration baseline")

        for feature in SENSOR_FEATURES:
            values = cal_df[feature].values
            baseline_mean = np.mean(values)
            baseline_std = np.std(values, ddof=1)  # sample std

            # Handle case where std is 0 (constant feature)
            if baseline_std == 0:
                baseline_std = 1.0
                print(f"  WARNING: {battery_id} {feature} has zero std in calibration, "
                      f"using std=1.0")

            baselines.append({
                "Battery_ID": battery_id,
                "feature": feature,
                "baseline_mean": baseline_mean,
                "baseline_std": baseline_std,
                "n_cycles": len(cal_df)
            })

    baseline_df = pd.DataFrame(baselines)
    return baseline_df


def apply_normalization(df, baseline_df):
    """
    Apply battery-relative normalization to all sensor features.

    For each battery and feature:
        normalized = (value - baseline_mean) / baseline_std

    Cycle_number is left unchanged.
    """
    df_norm = df.copy()

    for battery_id in df["Battery_ID"].unique():
        mask = df["Battery_ID"] == battery_id

        for feature in SENSOR_FEATURES:
            # Get baseline stats for this battery and feature
            base_row = baseline_df[
                (baseline_df["Battery_ID"] == battery_id) &
                (baseline_df["feature"] == feature)
            ]

            if len(base_row) == 0:
                raise ValueError(f"No baseline found for {battery_id} {feature}")

            baseline_mean = base_row["baseline_mean"].values[0]
            baseline_std = base_row["baseline_std"].values[0]

            # Normalize
            df_norm.loc[mask, feature] = (
                (df_norm.loc[mask, feature] - baseline_mean) / baseline_std
            )

    return df_norm


def verify_normalization(df_norm, baseline_df):
    """Verify that normalization worked correctly."""
    print("\n" + "=" * 80)
    print("NORMALIZATION VERIFICATION")
    print("=" * 80)

    for battery_id in df_norm["Battery_ID"].unique():
        battery_df = df_norm[df_norm["Battery_ID"] == battery_id].sort_values("Cycle")

        # Check first CALIBRATION_CYCLES cycles: should have mean ~0, std ~1
        cal_df = battery_df.head(CALIBRATION_CYCLES)

        print(f"\n{battery_id} - First {CALIBRATION_CYCLES} cycles (calibration window):")
        for feature in SENSOR_FEATURES:
            mean_val = cal_df[feature].mean()
            std_val = cal_df[feature].std()
            print(f"  {feature}: mean={mean_val:.4f}, std={std_val:.4f}")

            # Verify near zero mean and unit std
            if abs(mean_val) > 0.01:
                print(f"    WARNING: mean not near 0!")
            if abs(std_val - 1.0) > 0.01:
                print(f"    WARNING: std not near 1!")

    # Check full range
    print("\nFull range after normalization:")
    print(df_norm[SENSOR_FEATURES].describe().T[["min", "max", "mean", "std"]].to_string())


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    print("=" * 80)
    print("BATTERY-RELATIVE FEATURE NORMALIZATION")
    print("=" * 80)
    print(f"Calibration window: first {CALIBRATION_CYCLES} cycles per battery")
    print(f"Features normalized: {len(SENSOR_FEATURES)} sensor features")
    print(f"Features NOT normalized: Cycle_number")

    # Load data
    df = load_data()

    # Compute calibration baselines
    print(f"\nComputing calibration baselines from first {CALIBRATION_CYCLES} cycles...")
    baseline_df = compute_calibration_baseline(df)
    print(f"  Computed baselines for {len(baseline_df)} battery-feature pairs")

    # Save baseline statistics for documentation
    baseline_df.to_csv(OUT_BASELINE_STATS, index=False)
    print(f"  Saved baseline stats to {OUT_BASELINE_STATS}")

    # Apply normalization
    print("\nApplying battery-relative normalization...")
    df_norm = apply_normalization(df, baseline_df)

    # Verify
    verify_normalization(df_norm, baseline_df)

    # Keep original columns + normalized features + labels
    output_cols = ["Battery_ID", "Cycle"] + SENSOR_FEATURES + ["Cycle_number", "SoH", "RUL", "Reached_EOL"]
    df_norm = df_norm[output_cols]

    # Round numeric columns
    num_cols = df_norm.select_dtypes(include=[np.number]).columns
    df_norm[num_cols] = df_norm[num_cols].round(6)

    # Save
    df_norm.to_csv(OUT_CSV, index=False)
    print(f"\nSaved normalized features to {OUT_CSV}")
    print(f"  Shape: {df_norm.shape[0]} rows x {df_norm.shape[1]} cols")

    # Show sample
    print("\nFirst 3 rows per battery:")
    for battery_id in df_norm["Battery_ID"].unique():
        subset = df_norm[df_norm["Battery_ID"] == battery_id].head(3)
        print(f"\n{battery_id}:")
        print(subset[["Battery_ID", "Cycle"] + SENSOR_FEATURES[:3] + ["Cycle_number"]].to_string(index=False))

    print("\n" + "=" * 80)
    print("NORMALIZATION COMPLETE")
    print("=" * 80)


if __name__ == "__main__":
    main()