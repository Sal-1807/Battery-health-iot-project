"""
B0018 Exploratory Comparison Analysis

Investigates why B0018 performs poorly in LOBO cross-validation
by comparing its feature distributions and degradation patterns
against B0005 and B0006.

DO NOT modify training pipeline or models.
"""

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
from scipy import stats
from pathlib import Path

# Configuration
DATA_PATH = Path('data/processed/battery_health_features.csv')
OUTPUT_DIR = Path('results/analysis/b0018_comparison')
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Feature columns to analyze
FEATURE_COLS = [
    'Voltage_mean', 'Voltage_min', 'Voltage_max', 'Voltage_range',
    'Current_mean', 'Current_std',
    'Temperature_mean', 'Temperature_max', 'Temperature_rise',
    'Cycle_number'
]

TARGET_COLS = ['SoH', 'RUL']

def load_data():
    """Load and separate data by battery."""
    df = pd.read_csv(DATA_PATH)

    b0005 = df[df['Battery_ID'] == 'B0005'].copy()
    b0006 = df[df['Battery_ID'] == 'B0006'].copy()
    b0018 = df[df['Battery_ID'] == 'B0018'].copy()

    print(f"Loaded data:")
    print(f"  B0005: {len(b0005)} cycles")
    print(f"  B0006: {len(b0006)} cycles")
    print(f"  B0018: {len(b0018)} cycles")

    return b0005, b0006, b0018

def compute_summary_stats(b0005, b0006, b0018):
    """Compute per-battery summary statistics."""
    batteries = {'B0005': b0005, 'B0006': b0006, 'B0018': b0018}

    summary_stats = []

    for battery_name, battery_df in batteries.items():
        for feature in FEATURE_COLS + TARGET_COLS:
            stats_dict = {
                'Battery': battery_name,
                'Feature': feature,
                'Mean': battery_df[feature].mean(),
                'Std': battery_df[feature].std(),
                'Min': battery_df[feature].min(),
                'Max': battery_df[feature].max(),
                'Median': battery_df[feature].median()
            }
            summary_stats.append(stats_dict)

    summary_df = pd.DataFrame(summary_stats)
    summary_df.to_csv(OUTPUT_DIR / 'summary_statistics.csv', index=False)
    print(f"\nSaved summary statistics to {OUTPUT_DIR / 'summary_statistics.csv'}")

    return summary_df

def compute_effect_sizes(b0005, b0006, b0018):
    """Compute Cohen's d effect sizes comparing B0018 to others."""
    effect_sizes = []

    for feature in FEATURE_COLS + TARGET_COLS:
        # B0018 vs B0005
        mean_diff_05 = b0018[feature].mean() - b0005[feature].mean()
        pooled_std_05 = np.sqrt((b0018[feature].std()**2 + b0005[feature].std()**2) / 2)
        cohens_d_05 = mean_diff_05 / pooled_std_05 if pooled_std_05 > 0 else 0

        # B0018 vs B0006
        mean_diff_06 = b0018[feature].mean() - b0006[feature].mean()
        pooled_std_06 = np.sqrt((b0018[feature].std()**2 + b0006[feature].std()**2) / 2)
        cohens_d_06 = mean_diff_06 / pooled_std_06 if pooled_std_06 > 0 else 0

        # Statistical tests
        _, pval_05 = stats.mannwhitneyu(b0018[feature], b0005[feature], alternative='two-sided')
        _, pval_06 = stats.mannwhitneyu(b0018[feature], b0006[feature], alternative='two-sided')

        effect_sizes.append({
            'Feature': feature,
            'B0018_mean': b0018[feature].mean(),
            'B0005_mean': b0005[feature].mean(),
            'B0006_mean': b0006[feature].mean(),
            'Cohen_d_vs_B0005': cohens_d_05,
            'Cohen_d_vs_B0006': cohens_d_06,
            'p_value_vs_B0005': pval_05,
            'p_value_vs_B0006': pval_06
        })

    effect_df = pd.DataFrame(effect_sizes)
    effect_df.to_csv(OUTPUT_DIR / 'effect_sizes.csv', index=False)
    print(f"Saved effect sizes to {OUTPUT_DIR / 'effect_sizes.csv'}")

    return effect_df

def plot_feature_distributions(b0005, b0006, b0018):
    """Create distribution comparison plots for all features."""
    n_features = len(FEATURE_COLS)
    n_cols = 3
    n_rows = (n_features + n_cols - 1) // n_cols

    fig, axes = plt.subplots(n_rows, n_cols, figsize=(15, 4*n_rows))
    axes = axes.flatten()

    for idx, feature in enumerate(FEATURE_COLS):
        ax = axes[idx]

        ax.hist(b0005[feature], bins=30, alpha=0.5, label='B0005', color='blue', density=True)
        ax.hist(b0006[feature], bins=30, alpha=0.5, label='B0006', color='green', density=True)
        ax.hist(b0018[feature], bins=30, alpha=0.5, label='B0018', color='red', density=True)

        ax.set_xlabel(feature)
        ax.set_ylabel('Density')
        ax.set_title(f'{feature} Distribution')
        ax.legend()
        ax.grid(True, alpha=0.3)

    # Hide unused subplots
    for idx in range(n_features, len(axes)):
        axes[idx].axis('off')

    plt.tight_layout()
    plt.savefig(OUTPUT_DIR / 'feature_distributions.png', dpi=300, bbox_inches='tight')
    plt.close()
    print(f"Saved feature distributions to {OUTPUT_DIR / 'feature_distributions.png'}")

def plot_boxplots(b0005, b0006, b0018):
    """Create boxplot comparisons."""
    n_features = len(FEATURE_COLS)
    n_cols = 3
    n_rows = (n_features + n_cols - 1) // n_cols

    fig, axes = plt.subplots(n_rows, n_cols, figsize=(15, 4*n_rows))
    axes = axes.flatten()

    for idx, feature in enumerate(FEATURE_COLS):
        ax = axes[idx]

        data_to_plot = [b0005[feature], b0006[feature], b0018[feature]]
        bp = ax.boxplot(data_to_plot, tick_labels=['B0005', 'B0006', 'B0018'], patch_artist=True)

        colors = ['blue', 'green', 'red']
        for patch, color in zip(bp['boxes'], colors):
            patch.set_facecolor(color)
            patch.set_alpha(0.5)

        ax.set_ylabel(feature)
        ax.set_title(f'{feature} Box Plot')
        ax.grid(True, alpha=0.3, axis='y')

    # Hide unused subplots
    for idx in range(n_features, len(axes)):
        axes[idx].axis('off')

    plt.tight_layout()
    plt.savefig(OUTPUT_DIR / 'feature_boxplots.png', dpi=300, bbox_inches='tight')
    plt.close()
    print(f"Saved feature boxplots to {OUTPUT_DIR / 'feature_boxplots.png'}")

def plot_degradation_patterns(b0005, b0006, b0018):
    """Compare degradation behavior over cycles."""
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))

    # SoH vs Cycle
    ax = axes[0, 0]
    ax.plot(b0005['Cycle_number'], b0005['SoH'], 'o-', label='B0005', alpha=0.7, markersize=3)
    ax.plot(b0006['Cycle_number'], b0006['SoH'], 's-', label='B0006', alpha=0.7, markersize=3)
    ax.plot(b0018['Cycle_number'], b0018['SoH'], '^-', label='B0018', alpha=0.7, markersize=3, color='red')
    ax.set_xlabel('Cycle Number')
    ax.set_ylabel('SoH (%)')
    ax.set_title('State of Health vs Cycle')
    ax.legend()
    ax.grid(True, alpha=0.3)

    # RUL vs Cycle
    ax = axes[0, 1]
    ax.plot(b0005['Cycle_number'], b0005['RUL'], 'o-', label='B0005', alpha=0.7, markersize=3)
    ax.plot(b0006['Cycle_number'], b0006['RUL'], 's-', label='B0006', alpha=0.7, markersize=3)
    ax.plot(b0018['Cycle_number'], b0018['RUL'], '^-', label='B0018', alpha=0.7, markersize=3, color='red')
    ax.set_xlabel('Cycle Number')
    ax.set_ylabel('RUL (cycles)')
    ax.set_title('Remaining Useful Life vs Cycle')
    ax.legend()
    ax.grid(True, alpha=0.3)

    # Voltage_mean vs Cycle
    ax = axes[1, 0]
    ax.plot(b0005['Cycle_number'], b0005['Voltage_mean'], 'o-', label='B0005', alpha=0.7, markersize=3)
    ax.plot(b0006['Cycle_number'], b0006['Voltage_mean'], 's-', label='B0006', alpha=0.7, markersize=3)
    ax.plot(b0018['Cycle_number'], b0018['Voltage_mean'], '^-', label='B0018', alpha=0.7, markersize=3, color='red')
    ax.set_xlabel('Cycle Number')
    ax.set_ylabel('Voltage Mean (V)')
    ax.set_title('Mean Voltage vs Cycle')
    ax.legend()
    ax.grid(True, alpha=0.3)

    # Temperature_max vs Cycle
    ax = axes[1, 1]
    ax.plot(b0005['Cycle_number'], b0005['Temperature_max'], 'o-', label='B0005', alpha=0.7, markersize=3)
    ax.plot(b0006['Cycle_number'], b0006['Temperature_max'], 's-', label='B0006', alpha=0.7, markersize=3)
    ax.plot(b0018['Cycle_number'], b0018['Temperature_max'], '^-', label='B0018', alpha=0.7, markersize=3, color='red')
    ax.set_xlabel('Cycle Number')
    ax.set_ylabel('Max Temperature (°C)')
    ax.set_title('Maximum Temperature vs Cycle')
    ax.legend()
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig(OUTPUT_DIR / 'degradation_patterns.png', dpi=300, bbox_inches='tight')
    plt.close()
    print(f"Saved degradation patterns to {OUTPUT_DIR / 'degradation_patterns.png'}")

def plot_additional_degradation(b0005, b0006, b0018):
    """Additional degradation plots for key features."""
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))

    # Current_mean vs Cycle
    ax = axes[0, 0]
    ax.plot(b0005['Cycle_number'], b0005['Current_mean'], 'o-', label='B0005', alpha=0.7, markersize=3)
    ax.plot(b0006['Cycle_number'], b0006['Current_mean'], 's-', label='B0006', alpha=0.7, markersize=3)
    ax.plot(b0018['Cycle_number'], b0018['Current_mean'], '^-', label='B0018', alpha=0.7, markersize=3, color='red')
    ax.set_xlabel('Cycle Number')
    ax.set_ylabel('Current Mean (A)')
    ax.set_title('Mean Current vs Cycle')
    ax.legend()
    ax.grid(True, alpha=0.3)

    # Voltage_range vs Cycle
    ax = axes[0, 1]
    ax.plot(b0005['Cycle_number'], b0005['Voltage_range'], 'o-', label='B0005', alpha=0.7, markersize=3)
    ax.plot(b0006['Cycle_number'], b0006['Voltage_range'], 's-', label='B0006', alpha=0.7, markersize=3)
    ax.plot(b0018['Cycle_number'], b0018['Voltage_range'], '^-', label='B0018', alpha=0.7, markersize=3, color='red')
    ax.set_xlabel('Cycle Number')
    ax.set_ylabel('Voltage Range (V)')
    ax.set_title('Voltage Range vs Cycle')
    ax.legend()
    ax.grid(True, alpha=0.3)

    # Temperature_rise vs Cycle
    ax = axes[1, 0]
    ax.plot(b0005['Cycle_number'], b0005['Temperature_rise'], 'o-', label='B0005', alpha=0.7, markersize=3)
    ax.plot(b0006['Cycle_number'], b0006['Temperature_rise'], 's-', label='B0006', alpha=0.7, markersize=3)
    ax.plot(b0018['Cycle_number'], b0018['Temperature_rise'], '^-', label='B0018', alpha=0.7, markersize=3, color='red')
    ax.set_xlabel('Cycle Number')
    ax.set_ylabel('Temperature Rise (°C)')
    ax.set_title('Temperature Rise vs Cycle')
    ax.legend()
    ax.grid(True, alpha=0.3)

    # Current_std vs Cycle
    ax = axes[1, 1]
    ax.plot(b0005['Cycle_number'], b0005['Current_std'], 'o-', label='B0005', alpha=0.7, markersize=3)
    ax.plot(b0006['Cycle_number'], b0006['Current_std'], 's-', label='B0006', alpha=0.7, markersize=3)
    ax.plot(b0018['Cycle_number'], b0018['Current_std'], '^-', label='B0018', alpha=0.7, markersize=3, color='red')
    ax.set_xlabel('Cycle Number')
    ax.set_ylabel('Current Std (A)')
    ax.set_title('Current Standard Deviation vs Cycle')
    ax.legend()
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig(OUTPUT_DIR / 'additional_degradation.png', dpi=300, bbox_inches='tight')
    plt.close()
    print(f"Saved additional degradation plots to {OUTPUT_DIR / 'additional_degradation.png'}")

def analyze_correlations(b0005, b0006, b0018):
    """Analyze feature correlations with SoH and RUL for each battery."""
    batteries = {'B0005': b0005, 'B0006': b0006, 'B0018': b0018}

    correlation_results = []

    for battery_name, battery_df in batteries.items():
        for target in TARGET_COLS:
            for feature in FEATURE_COLS:
                corr = battery_df[feature].corr(battery_df[target])
                correlation_results.append({
                    'Battery': battery_name,
                    'Target': target,
                    'Feature': feature,
                    'Correlation': corr
                })

    corr_df = pd.DataFrame(correlation_results)
    corr_df.to_csv(OUTPUT_DIR / 'feature_correlations.csv', index=False)
    print(f"Saved feature correlations to {OUTPUT_DIR / 'feature_correlations.csv'}")

    # Create correlation heatmaps
    fig, axes = plt.subplots(1, 3, figsize=(18, 5))

    for idx, (battery_name, battery_df) in enumerate(batteries.items()):
        features_for_corr = FEATURE_COLS + TARGET_COLS
        corr_matrix = battery_df[features_for_corr].corr()

        sns.heatmap(corr_matrix, annot=True, fmt='.2f', cmap='coolwarm', center=0,
                   ax=axes[idx], cbar_kws={'shrink': 0.8}, square=True, vmin=-1, vmax=1)
        axes[idx].set_title(f'{battery_name} Correlation Matrix')

    plt.tight_layout()
    plt.savefig(OUTPUT_DIR / 'correlation_heatmaps.png', dpi=300, bbox_inches='tight')
    plt.close()
    print(f"Saved correlation heatmaps to {OUTPUT_DIR / 'correlation_heatmaps.png'}")

    return corr_df

def plot_rul_soh_relationships(b0005, b0006, b0018):
    """Plot RUL vs SoH relationships."""
    fig, axes = plt.subplots(1, 3, figsize=(15, 4))

    batteries = [('B0005', b0005, 'blue'), ('B0006', b0006, 'green'), ('B0018', b0018, 'red')]

    for idx, (battery_name, battery_df, color) in enumerate(batteries):
        ax = axes[idx]
        ax.scatter(battery_df['SoH'], battery_df['RUL'], alpha=0.6, s=20, c=color)
        ax.set_xlabel('SoH (%)')
        ax.set_ylabel('RUL (cycles)')
        ax.set_title(f'{battery_name}: RUL vs SoH')
        ax.grid(True, alpha=0.3)

        # Add correlation coefficient
        corr = battery_df['SoH'].corr(battery_df['RUL'])
        ax.text(0.05, 0.95, f'Corr: {corr:.3f}', transform=ax.transAxes,
               verticalalignment='top', bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5))

    plt.tight_layout()
    plt.savefig(OUTPUT_DIR / 'rul_vs_soh.png', dpi=300, bbox_inches='tight')
    plt.close()
    print(f"Saved RUL vs SoH plots to {OUTPUT_DIR / 'rul_vs_soh.png'}")

def main():
    """Run complete B0018 comparison analysis."""
    print("="*70)
    print("B0018 Exploratory Comparison Analysis")
    print("="*70)

    # Load data
    print("\n1. Loading data...")
    b0005, b0006, b0018 = load_data()

    # Summary statistics
    print("\n2. Computing summary statistics...")
    summary_df = compute_summary_stats(b0005, b0006, b0018)

    # Effect sizes
    print("\n3. Computing effect sizes and statistical tests...")
    effect_df = compute_effect_sizes(b0005, b0006, b0018)

    # Distribution plots
    print("\n4. Creating distribution plots...")
    plot_feature_distributions(b0005, b0006, b0018)
    plot_boxplots(b0005, b0006, b0018)

    # Degradation patterns
    print("\n5. Analyzing degradation patterns...")
    plot_degradation_patterns(b0005, b0006, b0018)
    plot_additional_degradation(b0005, b0006, b0018)

    # Correlations
    print("\n6. Computing correlations...")
    corr_df = analyze_correlations(b0005, b0006, b0018)

    # RUL vs SoH
    print("\n7. Plotting RUL vs SoH relationships...")
    plot_rul_soh_relationships(b0005, b0006, b0018)

    print("\n" + "="*70)
    print("Analysis complete!")
    print(f"All results saved to: {OUTPUT_DIR}")
    print("="*70)

    # Print key findings
    print("\n" + "="*70)
    print("KEY FINDINGS - Effect Sizes (Cohen's d)")
    print("="*70)
    print("\nLarge effects (|d| > 0.8) comparing B0018 to others:")
    large_effects = effect_df[
        (effect_df['Cohen_d_vs_B0005'].abs() > 0.8) |
        (effect_df['Cohen_d_vs_B0006'].abs() > 0.8)
    ]
    if len(large_effects) > 0:
        for _, row in large_effects.iterrows():
            print(f"\n{row['Feature']}:")
            print(f"  B0018 mean: {row['B0018_mean']:.4f}")
            print(f"  B0005 mean: {row['B0005_mean']:.4f} (d = {row['Cohen_d_vs_B0005']:.3f})")
            print(f"  B0006 mean: {row['B0006_mean']:.4f} (d = {row['Cohen_d_vs_B0006']:.3f})")
    else:
        print("No features with large effect sizes found.")

    print("\nMedium effects (0.5 < |d| < 0.8) comparing B0018 to others:")
    medium_effects = effect_df[
        ((effect_df['Cohen_d_vs_B0005'].abs() > 0.5) & (effect_df['Cohen_d_vs_B0005'].abs() <= 0.8)) |
        ((effect_df['Cohen_d_vs_B0006'].abs() > 0.5) & (effect_df['Cohen_d_vs_B0006'].abs() <= 0.8))
    ]
    if len(medium_effects) > 0:
        for _, row in medium_effects.iterrows():
            print(f"\n{row['Feature']}:")
            print(f"  B0018 mean: {row['B0018_mean']:.4f}")
            print(f"  vs B0005: d = {row['Cohen_d_vs_B0005']:.3f}")
            print(f"  vs B0006: d = {row['Cohen_d_vs_B0006']:.3f}")
    else:
        print("No features with medium effect sizes found.")

if __name__ == '__main__':
    main()
