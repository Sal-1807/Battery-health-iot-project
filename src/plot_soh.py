"""SoH degradation curves for B0005, B0006, B0018 (raw values, no smoothing)."""

import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.lines import Line2D

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repo root (src/ -> ..)
OUT = os.path.join(ROOT, "results", "plots")

SURFACE, INK, INK_2, MUTED = "#fcfcfb", "#0b0b0b", "#52514e", "#898781"
GRID, AXIS, CRITICAL = "#e1e0d9", "#c3c2b7", "#d03b3b"
# categorical slots 1-3 (validated all-pairs, light mode)
COLORS = {"B0005": "#2a78d6", "B0006": "#eb6834", "B0018": "#1baf7a"}
LABEL_BG = dict(facecolor=SURFACE, edgecolor="none", pad=1.6, alpha=0.88)

plt.rcParams.update({
    "font.family": "sans-serif", "font.sans-serif": ["Segoe UI", "DejaVu Sans", "sans-serif"],
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "axes.edgecolor": AXIS, "axes.linewidth": 0.8, "axes.labelcolor": INK_2, "text.color": INK,
    "xtick.color": MUTED, "ytick.color": MUTED, "xtick.labelsize": 9, "ytick.labelsize": 9,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.7, "grid.linestyle": "-",
    "axes.axisbelow": True, "legend.frameon": False,
})


def main():
    os.makedirs(OUT, exist_ok=True)
    df = pd.read_csv(os.path.join(ROOT, "data", "processed", "battery_health_features.csv"))

    fig, ax = plt.subplots(figsize=(11, 6.4))
    for b, g in df.groupby("Battery_ID"):
        g = g.sort_values("Cycle")
        ax.plot(g.Cycle, g.SoH, color=COLORS[b], lw=1.8, zorder=3, solid_capstyle="round")

        eol = g.loc[g.RUL == 0]
        if len(eol):
            x, y = eol.Cycle.iloc[0], eol.SoH.iloc[0]
            ax.plot([x], [y], "o", ms=9, mfc=SURFACE, mec=COLORS[b], mew=2.2, zorder=6)
            ax.annotate(f"EOL  cycle {int(x)}\nSoH {y:.1f}%", xy=(x, y), xytext=(-10, -14),
                        textcoords="offset points", ha="right", va="top", fontsize=8.5,
                        color=COLORS[b], fontweight="600", zorder=7, bbox=LABEL_BG)
        # direct label at the end of each series
        ax.annotate(b, xy=(g.Cycle.iloc[-1], g.SoH.iloc[-1]), xytext=(6, 0),
                    textcoords="offset points", va="center", fontsize=9,
                    color=INK_2, fontweight="600", zorder=7, bbox=LABEL_BG)

    # NB: SoH is normalised per cell to its OWN first capacity, so this 70% line is
    # NOT the 1.4 Ah EOL - the actual EOL crossings (rings) land at a different SoH
    # for each battery. Keep the two ideas visually distinct.
    ax.axhline(70, color=CRITICAL, lw=1.4, ls=(0, (5, 3)), zorder=4)
    ax.annotate("SoH = 70% of initial capacity  (NOT the 1.4 Ah EOL)", xy=(0.008, 70),
                xycoords=("axes fraction", "data"), xytext=(0, 5), textcoords="offset points",
                ha="left", va="bottom", fontsize=8.5, color=CRITICAL, fontweight="600",
                zorder=6, bbox=LABEL_BG)

    ax.set_xlabel("Discharge cycle", fontsize=10, labelpad=8)
    ax.set_ylabel("State of Health (%)", fontsize=10)
    ax.set_xlim(0, 180)
    ax.set_ylim(50, 105)
    ax.spines[["top", "right"]].set_visible(False)
    ax.set_title("SoH degradation — B0005, B0006, B0018", fontsize=14, fontweight="600",
                 color=INK, loc="left", pad=30)
    ax.annotate("SoH = capacity / first discharge capacity x 100. Raw per-cycle values, no smoothing. "
                "Rings mark the 1.4 Ah EOL crossing, which falls at a different SoH per cell.",
                xy=(0, 1.012), xycoords="axes fraction", fontsize=9, color=MUTED, va="bottom")
    ax.legend(handles=[Line2D([], [], color=COLORS[b], lw=2.4, label=b) for b in COLORS] +
                      [Line2D([], [], color=CRITICAL, lw=1.4, ls=(0, (5, 3)),
                              label="SoH = 70% (reference)")],
              loc="lower left", fontsize=9, labelcolor=INK_2, handlelength=2.4)
    fig.tight_layout()
    p = os.path.join(OUT, "soh_degradation.png")
    fig.savefig(p, dpi=200)
    print("wrote", p)


if __name__ == "__main__":
    main()
