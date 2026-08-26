# B0018 LOBO Cross-Validation Performance Analysis

**Date:** 2026-08-20  
**Objective:** Investigate why B0018 exhibits poor RUL prediction performance when used as the held-out battery in Leave-One-Battery-Out (LOBO) cross-validation.

---

## Executive Summary

B0018 shows dramatically poorer RUL prediction performance (R² = 0.277) compared to B0005 (R² = 0.870) and B0006 (R² = 0.863) when used as the held-out test battery in LOBO cross-validation. This analysis reveals **substantial distributional differences** in voltage and temperature features between B0018 and the training batteries (B0005/B0006), which likely explain the poor generalization.

**Key Finding:** B0018 operates in a significantly different voltage and temperature regime compared to B0005, while being more similar to B0006. However, the model trained on B0005+B0006 fails to generalize to B0018, suggesting domain shift in the feature space.

---

## Methodology

### Data
- **Source:** `data/processed/battery_health_features.csv`
- **Batteries:** B0005 (168 cycles), B0006 (168 cycles), B0018 (132 cycles)
- **Features Analyzed:** 10 engineered features (voltage, current, temperature characteristics)
- **Targets:** SoH (State of Health), RUL (Remaining Useful Life)

### Analysis Approach
1. **Summary statistics:** Mean, standard deviation, min, max, median per battery
2. **Effect size analysis:** Cohen's d to quantify distributional differences
3. **Statistical testing:** Mann-Whitney U tests for distribution comparisons
4. **Correlation analysis:** Feature-target correlations within each battery
5. **Degradation pattern comparison:** Time-series analysis of feature evolution
6. **Visualization:** Distribution plots, boxplots, degradation curves, correlation heatmaps

---

## Feature Distribution Comparison

### Large Effect Sizes (|d| > 0.8)

B0018 exhibits **dramatically different** feature distributions compared to B0005 for 6 out of 10 features:

#### 1. **Voltage_min** (Cohen's d = -4.57 vs B0005)
- **B0018 mean:** 2.412 V
- **B0005 mean:** 2.649 V
- **Difference:** -237 mV (9.5% lower)
- **Interpretation:** B0018 discharges to significantly lower minimum voltages, indicating deeper discharge cycles or different cutoff voltages.

#### 2. **Voltage_range** (Cohen's d = +4.51 vs B0005)
- **B0018 mean:** 1.776 V
- **B0005 mean:** 1.547 V
- **Difference:** +229 mV (14.8% higher)
- **Interpretation:** B0018 experiences much wider voltage swings during cycling, consistent with deeper discharge.

#### 3. **Voltage_max** (Cohen's d = -2.21 vs B0005)
- **B0018 mean:** 4.188 V
- **B0005 mean:** 4.196 V
- **Difference:** -8 mV
- **Interpretation:** B0018 charges to slightly lower peak voltages.

#### 4. **Temperature_mean** (Cohen's d = -2.93 vs B0005, -2.26 vs B0006)
- **B0018 mean:** 31.09°C
- **B0005 mean:** 32.79°C
- **B0006 mean:** 32.79°C
- **Difference:** -1.7°C (5.2% cooler)
- **Interpretation:** B0018 operates consistently cooler throughout its cycling.

#### 5. **Temperature_max** (Cohen's d = -2.35 vs B0005, -1.98 vs B0006)
- **B0018 mean:** 37.74°C
- **B0005 mean:** 39.85°C
- **B0006 mean:** 39.87°C
- **Difference:** -2.1°C (5.3% cooler)
- **Interpretation:** B0018 never reaches the peak temperatures seen in the training batteries.

#### 6. **Temperature_rise** (Cohen's d = -1.31 vs B0005, -1.15 vs B0006)
- **B0018 mean:** 14.45°C
- **B0005 mean:** 15.62°C
- **B0006 mean:** 15.68°C
- **Difference:** -1.2°C (7.5% lower)
- **Interpretation:** B0018 exhibits smaller thermal excursions during cycling.

### Medium Effect Sizes (0.5 < |d| < 0.8)

#### 7. **Voltage_mean** (Cohen's d = -0.65 vs B0005, +0.67 vs B0006)
- **B0018 mean:** 3.496 V
- **B0005 mean:** 3.517 V  
- **B0006 mean:** 3.464 V
- **Interpretation:** B0018's average voltage lies between B0005 and B0006, but closer to B0006.

### Small Effect Sizes (|d| < 0.5)

**Current features** (Current_mean, Current_std), **Cycle_number**, **SoH**, and **RUL** show relatively small distributional differences, suggesting B0018's capacity degradation trajectory is similar despite different operating conditions.

---

## Statistical Significance

All voltage and temperature features show **highly statistically significant differences** (p < 0.001) when comparing B0018 to B0005 via Mann-Whitney U test, confirming these are not random variations.

Comparison with B0006 shows mixed results:
- **Voltage features:** Some significant (Voltage_mean, Voltage_max, Voltage_range), others not (Voltage_min)
- **Temperature features:** All highly significant (p < 1e-31)

---

## Correlation Analysis

### B0018 vs Training Batteries: Structural Differences

The correlation structure between features and RUL differs substantially across batteries:

#### **Temperature_max correlation with RUL:**
- **B0005:** -0.935 (very strong negative)
- **B0006:** -0.876 (strong negative)
- **B0018:** -0.695 (moderate negative)

B0018 shows **weaker temperature-RUL relationships**, suggesting thermal behavior is less predictive of remaining life in this battery.

#### **Voltage_range correlation with SoH:**
- **B0005:** +0.487 (moderate positive)
- **B0006:** +0.499 (moderate positive)
- **B0018:** -0.094 (negligible negative)

B0018 exhibits an **opposite correlation direction** for Voltage_range, indicating fundamentally different degradation mechanisms.

#### **Voltage_min correlation with SoH:**
- **B0005:** -0.487 (moderate negative)
- **B0006:** -0.490 (moderate negative)
- **B0018:** +0.118 (negligible positive)

Again, B0018 shows a **reversed relationship**, suggesting its deeper discharge patterns interact differently with capacity fade.

---

## Degradation Behavior Comparison

### SoH Degradation
All three batteries show similar overall SoH degradation patterns (starting ~92%, ending ~65-70%), with comparable degradation rates. This explains why **SoH prediction performs acceptably** for B0018 (RMSE = 5.16, R² = 0.55).

### RUL Degradation
Despite similar SoH trajectories, the **relationship between features and RUL diverges significantly** for B0018:

1. **Voltage patterns:** B0018's wider voltage range and lower minimum voltages throughout its life create feature distributions that fall outside the training data manifold.

2. **Temperature patterns:** B0018's consistently cooler operation (1.7-2.1°C lower) means temperature-based RUL predictions calibrated on B0005/B0006 systematically miscalibrate.

3. **Feature-RUL coupling:** The model learns temperature and voltage relationships from B0005/B0006 that **do not hold** for B0018.

---

## Likely Explanations for Poor LOBO RUL Performance

### 1. **Domain Shift in Operating Conditions**
B0018 operates in a different voltage-temperature regime than the training batteries. The model trained on B0005+B0006 learns feature-RUL mappings that are **invalid for B0018's operating conditions**.

- **Voltage domain shift:** B0018's lower Voltage_min (2.41V vs 2.65V) and higher Voltage_range (1.78V vs 1.55V) place it outside the convex hull of training data.
- **Temperature domain shift:** B0018's cooler operation (31.1°C vs 32.8°C mean) creates a systematic bias in temperature-dependent predictions.

### 2. **Different Degradation Mechanisms**
The reversed correlation signs (Voltage_range, Voltage_min with SoH) suggest B0018 degrades via different electrochemical pathways:

- **Deeper discharge cycles** (indicated by wider voltage range) may invoke different aging mechanisms (e.g., lithium plating, anode-side SEI growth) not captured by the training data.
- **Lower operating temperatures** may alter degradation kinetics, making thermally-parameterized models inaccurate.

### 3. **Feature Engineering Misalignment**
The current feature set (means, mins, maxes, ranges) captures **average operating conditions** but fails to capture the **shape of distributions** or **trajectory dynamics** that differ between batteries:

- B0005 and B0006 share similar voltage min distributions (narrow, centered around 2.65V).
- B0018's voltage min distribution is shifted and broader, but this is collapsed into a single mean value.
- The model cannot distinguish "different operating regime" from "different degradation state."

### 4. **Insufficient Training Diversity**
With only 2 training batteries (B0005, B0006) that are highly similar to each other, the model **overfits to their shared characteristics** and fails to generalize to B0018's outlier profile.

---

## Limitations

### 1. **Dataset Size**
- Only 3 batteries total (468 cycles combined)
- LOBO with n=2 training batteries provides minimal diversity
- Cannot distinguish battery-specific variation from fundamental domain shifts

### 2. **Feature Space Coverage**
- Current features may not capture critical degradation indicators
- No explicit features for discharge depth, C-rate, or cycle-to-cycle variability
- Missing electrochemical indicators (impedance, capacity fade rate)

### 3. **Analysis Scope**
- Focused on distributional differences; did not explore sequential/temporal dynamics
- Did not investigate non-linear feature interactions
- Correlation analysis cannot establish causality

### 4. **Ground Truth Uncertainty**
- Assumes provided SoH/RUL labels are accurate
- Cannot verify if B0018's different patterns reflect measurement artifacts or real degradation differences

---

## Conclusion

**B0018's poor LOBO RUL prediction performance (R² = 0.277 vs 0.87 for others) is explained by substantial distributional shifts in voltage and temperature features relative to the training batteries.**

Specifically:
1. **Voltage_min** is 9.5% lower (Cohen's d = -4.57)
2. **Voltage_range** is 14.8% wider (Cohen's d = +4.51)
3. **Temperature_mean** is 5.2% cooler (Cohen's d = -2.93)
4. **Temperature_max** is 5.3% cooler (Cohen's d = -2.35)

These differences create a **domain shift** where B0018 operates outside the feature space covered by training batteries B0005/B0006. The model learns temperature-voltage-RUL relationships that **do not generalize** to B0018's cooler, deeper-discharge operating regime.

The evidence **plausibly explains** the poor LOBO result: RUL is predicted from features whose distributions and correlations with degradation differ fundamentally between B0018 and the training set. This is a **generalization failure due to insufficient training diversity**, not a model architecture problem.

### Recommendations (Out of Scope)
While this analysis does not propose solutions, potential mitigations would include:
- Data augmentation or domain adaptation techniques
- Collecting more batteries with diverse operating profiles
- Engineering features invariant to operating conditions (e.g., capacity fade rate)
- Normalizing features by battery-specific baselines
- Using battery ID as a categorical feature or training separate models

---

## Files Generated

### Data Outputs
- `summary_statistics.csv` - Per-battery summary stats for all features
- `effect_sizes.csv` - Cohen's d effect sizes and p-values
- `feature_correlations.csv` - Feature-target correlations per battery

### Visualizations
- `feature_distributions.png` - Histogram overlays for 10 features
- `feature_boxplots.png` - Box plots comparing distributions
- `degradation_patterns.png` - SoH, RUL, Voltage_mean, Temperature_max vs Cycle
- `additional_degradation.png` - Current_mean, Voltage_range, Temperature_rise, Current_std vs Cycle
- `correlation_heatmaps.png` - Feature correlation matrices per battery
- `rul_vs_soh.png` - RUL vs SoH scatter plots per battery

---

**Analysis Date:** 2026-08-20  
**Analyst:** Automated exploratory analysis  
**Code:** `src/analyze_b0018.py`
