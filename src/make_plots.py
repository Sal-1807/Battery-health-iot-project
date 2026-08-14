"""
Capacity-vs-discharge-cycle plots for visual dataset assessment.

Source: results/capacity_series_long.csv + results/battery_diagnostics.csv
NO smoothing, NO interpolation, NO imputation, NO outlier removal.
Every point present in capacity_series_long.csv is plotted exactly as stored.
"""

import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

EOL = 1.4
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repo root (src/ -> ..)
BASE = sys.argv[1] if len(sys.argv) > 1 else ROOT
RESULTS = os.path.join(BASE, "results")
OUT = os.path.join(RESULTS, "plots")

# --- design tokens (dataviz reference palette, light surface) ----------------
SURFACE   = "#fcfcfb"
INK       = "#0b0b0b"
INK_2     = "#52514e"
MUTED     = "#898781"
GRID      = "#e1e0d9"
AXIS      = "#c3c2b7"
# categorical slots 1-5 (validated: all checks PASS, light mode)
CAT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"]
# status palette - reserved for threshold/anomaly meaning only
CRITICAL, WARNING, SERIOUS = "#d03b3b", "#fab219", "#ec835a"
# surface-coloured backing so a label stays legible over dense line traffic
LABEL_BG = dict(facecolor=SURFACE, edgecolor="none", pad=1.6, alpha=0.88)

plt.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["Segoe UI", "DejaVu Sans", "sans-serif"],
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE,
    "savefig.facecolor": SURFACE,
    "axes.edgecolor": AXIS, "axes.linewidth": 0.8,
    "axes.labelcolor": INK_2, "text.color": INK,
    "xtick.color": MUTED, "ytick.color": MUTED,
    "xtick.labelsize": 9, "ytick.labelsize": 9,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.7,
    "grid.linestyle": "-", "axes.axisbelow": True,
    "legend.frameon": False,
})

GROUPS = {
    "Usable for RUL":                       (["B0005","B0006","B0018","B0046","B0047","B0048"], CAT[0]),
    "No valid EOL / never reaches 1.4 Ah":  (["B0007","B0025","B0026","B0027","B0028","B0029",
                                              "B0030","B0031","B0032","B0036"], CAT[1]),
    "Regime break / spurious EOL":          (["B0033","B0034","B0042","B0043","B0044"], CAT[2]),
    "Cold (4 C): never healthy":            (["B0041","B0045","B0053","B0054","B0055","B0056"], CAT[3]),
    "Non-monotonic / too short":            (["B0038","B0039","B0040","B0049","B0050","B0051",
                                              "B0052"], CAT[4]),
}
GROUP_OF = {b: (g, c) for g, (bs, c) in GROUPS.items() for b in bs}

INDIVIDUAL_HIGHLIGHT = ["B0005","B0006","B0007","B0018","B0033","B0034","B0036",
                        "B0042","B0043","B0044","B0046","B0047","B0048"]


def parse_breaks(s):
    """'41-86;140-147' -> [(41,86),(140,147)] in 1-based discharge-cycle numbers."""
    if not isinstance(s, str) or not s.strip():
        return []
    out = []
    for part in s.split(";"):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-")
            out.append((int(a), int(b)))
    return out


def threshold_line(ax, label=True):
    """Threshold label sits at the LEFT edge - series endpoints and crossing
    callouts live on the right, so this keeps the two label families apart."""
    ax.axhline(EOL, color=CRITICAL, lw=1.4, ls=(0, (5, 3)), zorder=4)
    if label:
        ax.annotate("EOL threshold 1.4 Ah", xy=(0.008, EOL), xycoords=("axes fraction", "data"),
                    xytext=(0, 5), textcoords="offset points", ha="left", va="bottom",
                    fontsize=8.5, color=CRITICAL, fontweight="600", zorder=6,
                    bbox=LABEL_BG)


def callout(ax, x, y, text, color, prefer_up=True):
    """Place a callout so it never runs into the x-axis band or the right spine."""
    x0, x1 = ax.get_xlim()
    y0, y1 = ax.get_ylim()
    fx = (x - x0) / (x1 - x0)
    fy = (y - y0) / (y1 - y0)
    up = prefer_up if 0.18 < fy < 0.82 else (fy <= 0.18)   # force up near the floor
    dy, va = (18, "bottom") if up else (-18, "top")
    dx, ha = (-10, "right") if fx > 0.72 else (10, "left")
    ax.annotate(text, xy=(x, y), xytext=(dx, dy), textcoords="offset points",
                ha=ha, va=va, fontsize=8.5, color=color, fontweight="600", zorder=7,
                bbox=LABEL_BG)


def mark_breaks(ax, breaks, label=True):
    for i, (a, b) in enumerate(breaks):
        ax.axvspan(a, b, color=WARNING, alpha=0.16, zorder=0, lw=0)
        ax.axvline(a, color=WARNING, lw=1.0, zorder=1)
        ax.axvline(b, color=WARNING, lw=1.0, zorder=1)
        if label and i == 0:
            ax.annotate(f"regime break\ncycles {a}-{b}", xy=((a + b) / 2, 0.965),
                        xycoords=("data", "axes fraction"), ha="center", va="top",
                        fontsize=8.5, color="#8a6100", fontweight="600", zorder=6)


def main():
    os.makedirs(OUT, exist_ok=True)
    series = pd.read_csv(os.path.join(RESULTS, "capacity_series_long.csv"))
    diag = pd.read_csv(os.path.join(RESULTS, "battery_diagnostics.csv")).set_index("battery_id")
    ids = sorted(series.battery_id.unique())

    data = {b: g.sort_values("discharge_cycle") for b, g in series.groupby("battery_id")}

    # ---------------------------------------------------------------- overview
    fig, ax = plt.subplots(figsize=(13, 7.5))
    for b in ids:
        g = data[b]
        _, color = GROUP_OF[b]
        ax.plot(g.discharge_cycle, g.capacity_Ah, color=color, lw=1.3, alpha=0.75,
                solid_capstyle="round", zorder=3)
    threshold_line(ax)

    # selective direct labels at series endpoints (never a label on every point)
    for b in ["B0005", "B0006", "B0018", "B0007", "B0042", "B0036", "B0033", "B0041", "B0055"]:
        g = data[b]
        x, y = g.discharge_cycle.iloc[-1], g.capacity_Ah.iloc[-1]
        ax.annotate(b, xy=(x, y), xytext=(5, 0), textcoords="offset points",
                    fontsize=8.5, color=INK_2, va="center", fontweight="600", zorder=7,
                    bbox=LABEL_BG)

    ax.set_xlabel("Discharge cycle (original order)", fontsize=10)
    ax.set_ylabel("Capacity (Ah)", fontsize=10)
    ax.set_title("NASA Li-ion battery aging — capacity trajectories, all 34 unique batteries",
                 fontsize=14, fontweight="600", color=INK, pad=32, loc="left")
    ax.annotate("Raw discharge capacity as stored — no smoothing, interpolation or outlier removal. "
                "Colour = dataset-assessment group.",
                xy=(0, 1.012), xycoords="axes fraction", fontsize=9, color=MUTED, va="bottom")
    ax.set_xlim(0, 205); ax.set_ylim(0, 2.75)
    ax.spines[["top", "right"]].set_visible(False)
    handles = [Line2D([], [], color=c, lw=2.4, label=f"{g}  (n={len(bs)})")
               for g, (bs, c) in GROUPS.items()]
    handles.append(Line2D([], [], color=CRITICAL, lw=1.4, ls=(0, (5, 3)), label="1.4 Ah EOL threshold"))
    ax.legend(handles=handles, loc="upper right", fontsize=9, labelcolor=INK_2,
              handlelength=2.4, borderaxespad=0.8)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "overview_all_batteries.png"), dpi=200)
    plt.close(fig)

    # -------------------------------------------------- small multiples (all 34)
    ncol, nrow = 6, 6
    fig, axes = plt.subplots(nrow, ncol, figsize=(18, 15), sharex=False, sharey=True)
    for ax_, b in zip(axes.ravel(), ids):
        g = data[b]
        grp, color = GROUP_OF[b]
        brk = parse_breaks(diag.loc[b, "regime_break_cycles"])
        if brk:
            mark_breaks(ax_, brk, label=False)
        ax_.plot(g.discharge_cycle, g.capacity_Ah, color=color, lw=1.4, zorder=3)
        ax_.axhline(EOL, color=CRITICAL, lw=1.1, ls=(0, (4, 3)), zorder=4)
        amb = diag.loc[b, "ambient_temp_C"]
        ax_.set_title(f"{b}   ({amb} °C, n={len(g)})", fontsize=10, fontweight="600",
                      color=INK, loc="left", pad=5)
        ax_.set_ylim(0, 2.75)
        ax_.tick_params(labelsize=8)
        ax_.spines[["top", "right"]].set_visible(False)
    for ax_ in axes.ravel()[len(ids):]:
        ax_.set_visible(False)
    # label the last VISIBLE panel in each column, not just the bottom row
    for col in range(ncol):
        vis = [axes[r, col] for r in range(nrow) if axes[r, col].get_visible()]
        if vis:
            vis[-1].set_xlabel("Discharge cycle", fontsize=9)
    for ax_ in axes[:, 0]:
        ax_.set_ylabel("Capacity (Ah)", fontsize=9)
    fig.suptitle("Capacity vs discharge cycle — every unique battery (shared y-axis, 1.4 Ah threshold dashed)",
                 fontsize=15, fontweight="600", color=INK, x=0.005, ha="left", y=0.997)
    fig.legend(handles=[Patch(facecolor=WARNING, alpha=0.3, label="regime-break cycles"),
                        Line2D([], [], color=CRITICAL, lw=1.2, ls=(0, (4, 3)), label="1.4 Ah EOL")],
               loc="lower right", fontsize=10, labelcolor=INK_2, ncol=2,
               bbox_to_anchor=(0.995, 0.005))
    fig.tight_layout(rect=[0, 0.012, 1, 0.985])
    fig.savefig(os.path.join(OUT, "overview_small_multiples.png"), dpi=170)
    plt.close(fig)

    # ------------------------------------------------------- individual plots
    for b in ids:
        g = data[b]
        d = diag.loc[b]
        x = g.discharge_cycle.values
        y = g.capacity_Ah.values
        brk = parse_breaks(d["regime_break_cycles"])

        fig, ax = plt.subplots(figsize=(10, 5.8))
        if brk:
            mark_breaks(ax, brk)
        ax.plot(x, y, color=CAT[0], lw=1.7, zorder=3, solid_capstyle="round")
        ax.plot(x, y, "o", ms=3.0, color=CAT[0], mec=SURFACE, mew=0.6, zorder=3.5)
        threshold_line(ax)

        notes = []
        lit = d["EOL_cycle_literal"]
        conf = d["EOL_cycle_confirmed"]
        ax.set_ylim(0, max(2.75, float(np.nanmax(y)) * 1.12))
        ax.set_xlim(0, len(x) + max(3, len(x) * 0.06))

        if pd.notna(lit):
            lit = int(lit)
            ax.plot([lit], [y[lit - 1]], "o", ms=9, mfc="none", mec=CRITICAL, mew=2.0, zorder=6)
            callout(ax, lit, y[lit - 1], f"first crossing\ncycle {lit}", CRITICAL, prefer_up=False)
        if pd.notna(conf) and (pd.isna(lit) or int(conf) != lit):
            c = int(conf)
            ax.plot([c], [y[c - 1]], "D", ms=7, mfc="none", mec=SERIOUS, mew=2.0, zorder=6)
            callout(ax, c, y[c - 1], f"sustained crossing\ncycle {c}", "#a24f22", prefer_up=True)
        if bool(d["first_cycle_artifact"]):
            ax.plot([x[0]], [y[0]], "s", ms=8, mfc="none", mec=SERIOUS, mew=2.0, zorder=6)
            callout(ax, x[0], y[0], "cycle-1 conditioning\ndischarge (artifact)",
                    "#a24f22", prefer_up=True)

        dropped = int(d["capacity_zero"]) + int(d["capacity_missing_or_empty"])
        if dropped:
            notes.append(f"{dropped} discharge cycle(s) had zero/empty Capacity and are absent "
                         f"from the source CSV (not removed by this plot)")
        ax.set_xlabel("Discharge cycle (original order)", fontsize=10, labelpad=8)
        ax.set_ylabel("Capacity (Ah)", fontsize=10)
        ax.spines[["top", "right"]].set_visible(False)

        usable = "usable for RUL" if bool(d["EOL_reliable"]) and b in GROUPS["Usable for RUL"][0] \
                 else "excluded"
        ax.set_title(f"{b} — capacity vs discharge cycle", fontsize=14, fontweight="600",
                     color=INK, loc="left", pad=30)
        sub = (f"{d['ambient_temp_C']} °C · {len(x)} valid discharge cycles · "
               f"first {y[0]:.3f} Ah · min {y.min():.3f} Ah · final {y[-1]:.3f} Ah · {usable}")
        ax.annotate(sub, xy=(0, 1.015), xycoords="axes fraction", fontsize=9,
                    color=MUTED, va="bottom")
        if notes:
            ax.annotate(" · ".join(notes), xy=(0, -0.155), xycoords="axes fraction",
                        fontsize=8, color=MUTED, va="top")
        fig.tight_layout()
        tag = "" if b in INDIVIDUAL_HIGHLIGHT else ""
        fig.savefig(os.path.join(OUT, f"{b}{tag}.png"), dpi=200, bbox_inches="tight")
        plt.close(fig)

    print(f"wrote {len(os.listdir(OUT))} files to {os.path.abspath(OUT)}")
    for f in sorted(os.listdir(OUT)):
        print("  ", f)


if __name__ == "__main__":
    main()
