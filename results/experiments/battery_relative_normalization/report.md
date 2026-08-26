# Battery-Relative Feature Normalization Experiment Report

**Date:** 2026-08-26  
**Objective:** Evaluate whether battery-relative feature normalization using initial calibration cycles can improve generalization to unseen batteries, particularly B0018.

---

## Executive Summary

Battery-relative normalization using a 10-cycle calibration window **partially improved B0018 RUL performance** but **degraded overall performance** on B0005 and B0006. The results show a trade-off: normalization helps the domain-shifted battery (B0018) at the cost of hurting the in-distribution batteries.

**Key Finding:** B0018 RUL R² improved from 0.277 to 0.351 (+26.7%), while B0018 RUL RMSE improved from 27.22 to 25.79 cycles (-5.3%). However, SoH performance degraded across all batteries.

---

## Methodology

### Normalization Procedure

**Calibration Window:** First 10 cycles of each battery

**Features Normalized (9):**
- Voltage_mean, Voltage_min, Voltage_max, Voltage_range
- Current_mean, Current_std
- Temperature_mean, Temperature_max, Temperature_rise

**Feature NOT Normalized (1):**
- Cycle_number (cycle index, not a sensor measurement)

**Normalization Formula:**
```
normalized_feature = (feature - baseline_mean) / baseline_std
```

Where `baseline_mean` and `baseline_std` are computed from the first 10 cycles of **only the test battery** in each LOBO fold.

### Anti-Leakage Guarantee

For each LOBO fold:
1. The calibration baseline is computed from **only the first 10 cycles** of the test battery
2. These cycles occur before any prediction is made for that battery
3. No future-cycle information is used
4. The baseline is computed from the test battery itself, never from training batteries

### Experimental Design

| Aspect | Absolute (Baseline) | Normalized |
|--------|---------------------|------------|
| Features | 10 original features | 9 normalized + Cycle_number |
| LOBO CV | 3 folds | 3 folds (identical) |
| Models | RandomForest (n=100, depth=10) | RandomForest (n=100, depth=10) |
| Random State | 42 | 42 |
| StandardScaler | Yes (fit on train) | Yes (fit on train) |

---

## Results

### 1. Did SoH Performance Improve?

**No.** SoH performance degraded across all three batteries.

| Battery | Metric | Absolute | Normalized | Change |
|---------|--------|----------|------------|--------|
| B0005 | RMSE | 3.35 | 4.46 | +33.4% (worse) |
| B0005 | R² | 0.876 | 0.779 | -11.1% (worse) |
| B0006 | RMSE | 4.82 | 6.17 | +28.0% (worse) |
| B0006 | R² | 0.853 | 0.759 | -11.0% (worse) |
| B0018 | RMSE | 5.16 | 5.50 | +6.6% (worse) |
| B0018 | R² | 0.552 | 0.491 | -11.0% (worse) |

**Aggregate SoH RMSE:** 4.44 → 5.38 (21% worse)

**Explanation:** Normalizing features removes absolute voltage/temperature information that is predictive of SoH. When a battery is at 85% SoH, its absolute voltage profile differs from when it was at 95% SoH. Normalizing to the first 10 cycles erases this degradation signal.

### 2. Did EOL Performance Improve?

**Mixed.** EOL classification improved for B0006 and B0018 but degraded slightly for B0005.

| Battery | Metric | Absolute | Normalized | Change |
|---------|--------|----------|------------|--------|
| B0005 | Accuracy | 0.905 | 0.893 | -1.3% (worse) |
| B0005 | F1 | 0.846 | 0.830 | -1.9% (worse) |
| B0006 | Accuracy | 0.833 | 0.917 | +10.0% (better) |
| B0006 | F1 | 0.811 | 0.896 | +10.4% (better) |
| B0018 | Accuracy | 0.727 | 0.788 | +8.3% (better) |
| B0018 | F1 | 0.000 | 0.364 | +36.4 pts (better) |

**Critical observation for B0018:** The baseline model predicted **zero** true positives for EOL detection (Precision=0, Recall=0, F1=0). The normalized model achieved F1=0.36 with Precision=1.0 and Recall=0.22, meaning it correctly identified 22% of EOL cycles with zero false positives.

**Aggregate EOL F1:** 0.55 → 0.70 (+26.1%)

### 3. Did RUL Performance Improve?

**For B0018, yes. For B0005/B0006, no.**

| Battery | Metric | Absolute | Normalized | Change |
|---------|--------|----------|------------|--------|
| B0005 | RMSE | 14.87 | 15.32 | +3.0% (worse) |
| B0005 | R² | 0.870 | 0.862 | -0.9% (worse) |
| B0006 | RMSE | 13.38 | 14.61 | +9.2% (worse) |
| B0006 | R² | 0.863 | 0.837 | -3.1% (worse) |
| B0018 | RMSE | 27.22 | 25.79 | -5.3% (better) |
| B0018 | R² | 0.277 | 0.351 | +26.7% (better) |

**Aggregate RUL RMSE:** 18.49 → 18.57 (essentially unchanged)

### 4. Did B0018 RUL Performance Improve?

**Yes.** B0018 showed improvement in RUL prediction:

- **RMSE:** 27.22 → 25.79 cycles (-5.3%)
- **MAE:** 23.93 → 22.96 cycles (-4.0%)
- **R²:** 0.277 → 0.351 (+26.7%)

While still poor compared to B0005/B0006 (R² ~0.86), the normalized features reduced the domain-shift impact on RUL prediction.

### 5. Did Normalization Reduce the Domain-Shift Problem?

**Partially, for RUL.** The normalization reduced the R² gap between B0018 and the other batteries:

| Metric | B0005/B0006 (avg) | B0018 | Gap |
|--------|-------------------|-------|-----|
| **Absolute RUL R²** | 0.867 | 0.277 | 0.590 |
| **Normalized RUL R²** | 0.849 | 0.351 | 0.498 |

The gap narrowed from 0.590 to 0.498 (16% reduction).

However, for SoH, normalization increased the gap:

| Metric | B0005/B0006 (avg) | B0018 | Gap |
|--------|-------------------|-------|-----|
| **Absolute SoH R²** | 0.864 | 0.552 | 0.312 |
| **Normalized SoH R²** | 0.769 | 0.491 | 0.278 |

SoH gap narrowed slightly (11% reduction), but absolute performance dropped for all batteries.

### 6. What Are the Limitations?

**a) Calibration Window Choice**
- First 10 cycles may not represent a stable baseline
- Early cycles can have measurement artifacts
- No hyperparameter tuning was performed on window size

**b) Information Loss**
- Normalizing removes absolute voltage/temperature information
- This absolute information is predictive of degradation state
- The model cannot distinguish "hot battery at 80% SoH" from "cool battery at 80% SoH"

**c) Small Dataset**
- Only 3 batteries, 468 total samples
- Results may not generalize to larger datasets
- High variance in LOBO estimates

**d) Feature Engineering Trade-off**
- Features like Voltage_range normalized differently across batteries
- B0018's wider voltage range (deeper discharge) becomes "normal" after normalization
- But deeper discharge is a degradation signal that is erased

**e) Model Architecture**
- Same RandomForest model used; normalization effects may differ with other models
- No calibration of predictions (isotonic regression, Platt scaling)

**f) SoH vs RUL Divergence**
- Normalization helped RUL but hurt SoH
- This suggests different optimal feature representations for different tasks

---

## Conclusions

1. **Battery-relative normalization improved B0018 RUL prediction** (R²: 0.277 → 0.351) but degraded SoH prediction across all batteries.

2. **EOL classification improved** for B0006 and B0018, with B0018 going from zero true positives to meaningful detection.

3. **The domain-shift problem was partially reduced for RUL** but the overall performance trade-off is unfavorable.

4. **Normalization erased absolute degradation signals** that are valuable for SoH estimation. A battery's absolute voltage profile encodes its degradation state.

5. **The approach is not recommended as a general solution** because:
   - SoH performance degraded significantly (21% higher RMSE)
   - RUL improvement for B0018 is modest relative to the performance cost
   - The fundamental issue (only 3 batteries for training) is not addressed

---

## Recommendations

1. **Do not deploy this normalization approach** as the default. The SoH degradation is too severe.

2. **Consider task-specific features:**
   - For SoH: Use absolute features (current approach)
   - For RUL/EOL: Consider normalized features as an ensemble member

3. **Alternative approaches to explore:**
   - Domain adaptation techniques (CORAL, MMD)
   - Feature augmentation (add battery-specific baseline features alongside normalized)
   - Meta-learning for quick battery adaptation
   - Collect more batteries with diverse operating profiles

4. **Calibration window investigation:**
   - Try different window sizes (5, 15, 20 cycles)
   - Use sliding window for online adaptation
   - Weight recent cycles more heavily

---

## Files Created

```
results/experiments/battery_relative_normalization/
├── README.md                              # Experiment documentation
├── normalize_features.py                  # Feature normalization code
├── experiment.py                          # LOBO CV experiment
├── compare_results.py                     # Comparison analysis
├── report.md                              # This report
├── battery_health_features_normalized.csv # Normalized features
├── calibration_baseline_stats.csv         # Baseline statistics
├── lobo_results_absolute.csv             # Absolute feature LOBO results
├── lobo_results_normalized.csv           # Normalized feature LOBO results
├── predictions_absolute.csv              # Absolute feature predictions
├── predictions_normalized.csv            # Normalized feature predictions
└── plots/
    ├── aggregate_comparison.png          # Overall metrics comparison
    ├── b0018_comparison.png              # B0018-specific comparison
    └── b0018_residuals.png               # B0018 residual analysis
```

---

## Reproducibility

To reproduce this experiment:

```bash
# Generate normalized features
python results/experiments/battery_relative_normalization/normalize_features.py

# Run LOBO experiment
python results/experiments/battery_relative_normalization/experiment.py

# Generate comparison and plots
python results/experiments/battery_relative_normalization/compare_results.py
```

All random seeds are fixed at `RANDOM_STATE=42`. Results are deterministic.

---

**Experiment Date:** 2026-08-26  
**Analysis By:** Claude Code
