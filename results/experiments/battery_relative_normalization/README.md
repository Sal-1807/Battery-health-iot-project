# Battery-Relative Feature Normalization Experiment

**Date:** 2026-08-20  
**Objective:** Investigate whether battery-relative feature normalization using initial calibration cycles can improve generalization to unseen batteries, particularly B0018.

---

## Hypothesis

Battery-relative features (normalized by each battery's initial calibration baseline) may reduce domain shift by expressing features as deviations from each battery's own reference state, making them more comparable across batteries with different operating conditions.

---

## Methodology

### Initial Calibration Baseline

For each battery, the initial calibration baseline is computed from its **first 10 cycles**. This window represents the battery's "fresh" state before significant degradation occurs.

**Why 10 cycles?**
- Sufficient to compute stable statistics (mean, std)
- Early enough to represent the battery's initial operating condition
- Before significant capacity fade begins (all batteries start at ~92-102% SoH)

**Anti-leakage guarantee:**
- For each LOBO fold, the normalization baseline is computed **only from the first 10 cycles of the test battery**
- These cycles occur before any prediction is made for that battery
- No future-cycle information is used in the baseline calculation
- The baseline is computed **within the test set**, not from training batteries

### Normalization Procedure

For each feature (except Cycle_number):

1. **Compute baseline statistics** from first 10 cycles of the test battery:
   - `baseline_mean` = mean of feature values in cycles 1-10
   - `baseline_std` = std of feature values in cycles 1-10

2. **Normalize all cycles** of that battery:
   - `normalized_feature = (feature - baseline_mean) / baseline_std`

3. **Cycle_number** is left unnormalized (it's a cycle index, not a sensor measurement)

### Features Normalized

All 9 sensor-derived features are normalized:
- Voltage_mean, Voltage_min, Voltage_max, Voltage_range
- Current_mean, Current_std
- Temperature_mean, Temperature_max, Temperature_rise

### What This Achieves

- **Removes battery-specific offsets**: If B0018 operates at 31°C and B0005 at 33°C, normalization expresses both relative to their own baseline
- **Preserves degradation trends**: The normalized features still capture how each battery deviates from its initial state
- **Reduces domain shift**: Features become more comparable across batteries

---

## Experimental Design

### Version A: Absolute Features (Baseline)
- Original 10 features as-is
- StandardScaler applied (fit on train, transform on train/test)

### Version B: Battery-Relative Normalized Features
- 9 normalized features + Cycle_number
- StandardScaler applied (fit on train, transform on train/test)

### Evaluation Protocol
- **Identical LOBO CV**: Same 3-fold Leave-One-Battery-Out as baseline
- **Same models**: RandomForest (n_estimators=100, max_depth=10, min_samples_split=5)
- **Same evaluation**: SoH regression, EOL classification, RUL two-stage prediction
- **Same metrics**: RMSE, MAE, R² for regression; Accuracy, Precision, Recall, F1 for classification

---

## Key Questions

1. Did SoH performance improve?
2. Did EOL performance improve?
3. Did RUL performance improve?
4. Did B0018 RUL performance improve?
5. Did normalization reduce domain shift?
6. What are the limitations?

---

## Files

- `normalize_features.py` - Feature normalization implementation
- `experiment.py` - LOBO CV experiment with both versions
- `results_comparison.csv` - Side-by-side metrics comparison
- `per_battery_results.csv` - Per-battery breakdown
- `report.md` - Detailed analysis and answers to key questions
- `plots/` - Comparison visualizations

---

## Results Summary

*[To be filled after experiment execution]*
