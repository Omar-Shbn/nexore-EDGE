"""All figures, generated from results files only — never drawn by hand
(plan §7.5). Static light-mode PNGs destined for the 10-page PDF deck.

Palette: validated reference categorical set (dataviz skill); color follows
the entity — each policy keeps its hue in every figure.
"""
from __future__ import annotations

import pickle
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from .scenario import DT
from . import model as amodel

# fixed entity -> hue assignment (never cycled)
C = {
    "P": "#2a78d6",        # blue — ours
    "B0": "#eb6834",       # orange
    "B1": "#1baf7a",       # aqua
    "B2": "#eda100",       # yellow (low contrast -> always direct-labeled)
    "P-oracle": "#e87ba4",  # magenta
    "P-nopred": "#008300",  # green
    "P-noclass": "#6250d6",  # violet
    "P-nocost": "#e34948",  # red
}
TECH_C = {"wifi": "#1baf7a", "5g": "#6250d6", "leo": "#eda100"}
INK = "#0b0b0b"
INK2 = "#52514e"
GRID = "#e5e4e0"
SURF = "#fcfcfb"

plt.rcParams.update({
    "figure.facecolor": SURF, "axes.facecolor": SURF,
    "axes.edgecolor": GRID, "axes.labelcolor": INK, "text.color": INK,
    "xtick.color": INK2, "ytick.color": INK2,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8,
    "axes.spines.top": False, "axes.spines.right": False,
    "font.size": 11, "axes.titlesize": 12, "figure.dpi": 150,
    "savefig.bbox": "tight", "savefig.facecolor": SURF,
})

BASELINES = ("B0", "B1", "B2", "P")


def _agg(rows, policy, met):
    v = [r[met] for r in rows if r["policy"] == policy]
    if not v:
        return np.nan, 0.0
    m = float(np.mean(v))
    h = 1.96 * float(np.std(v, ddof=1)) / np.sqrt(len(v)) if len(v) > 1 else 0.0
    return m, h


def fig_tradeoff(rows, out: Path):
    """Headline (plan §8): interruption vs bandwidth overhead."""
    fig, ax = plt.subplots(figsize=(7, 5))
    for p in BASELINES:
        x, xh = _agg(rows, p, "overhead_pct")
        y, yh = _agg(rows, p, "int_avoid_s")
        ax.errorbar(x, y, xerr=xh, yerr=yh, fmt="o", ms=9, lw=1.5, capsize=3,
                    color=C[p], mec="white", mew=1.5)
        label = {"B0": "B0 single link", "B1": "B1 minRTT",
                 "B2": "B2 duplicate all", "P": "P (ours)"}[p]
        dx, dy, ha = {"B0": (10, 6, "left"), "B1": (26, 2, "left"),
                      "B2": (-10, 10, "right"), "P": (10, 40, "left")}[p]
        ax.annotate(f"{label}\n{y:.2f} s / {x:.1f}%", (x, y),
                    textcoords="offset points", xytext=(dx, dy),
                    ha=ha, fontsize="medium", color=INK)
    ax.set_xscale("symlog", linthresh=10)
    ax.set_xticks([0, 10, 50])
    ax.set_xticklabels(["0%", "10%", "50%"])
    ax.set_xlabel("Extra traffic sent (overhead)")
    ax.set_ylabel("Avoidable voice interruption per journey (s)")
    ax.set_title("Avoidable voice interruption vs extra traffic\n"
                 "mean ± 95% CI, 160 simulated journeys per gateway",
                 fontsize="medium")
    fig.savefig(out / "fig1_tradeoff.png")
    plt.close(fig)


def fig_timeline(demo_pkl: Path, out: Path):
    """Demo-style figure: the same journey under B0 and under P."""
    demo = pickle.load(open(demo_pkl, "rb"))
    fig, axes = plt.subplots(2, 1, figsize=(10, 5.6), sharex=True)
    for ax, name in zip(axes, ("B0", "P")):
        log, rt = demo[name]
        t = log.t
        # faint rows: ground-truth usability per tech
        for k, tech in enumerate(("wifi", "5g", "leo")):
            us = log.truth_usable[tech]
            ax.fill_between(t, k, k + 0.82, where=us, step="mid",
                            color=TECH_C[tech], alpha=0.16, lw=0)
        # saturated: which link the policy actually used
        act = np.array([{"wifi": 0, "5g": 1, "leo": 2}.get(a, -1)
                        for a in log.active])
        for k, tech in enumerate(("wifi", "5g", "leo")):
            ax.fill_between(t, k, k + 0.82, where=act == k, step="mid",
                            color=TECH_C[tech], alpha=0.85, lw=0)
        # duplication window (P only)
        if log.window.any():
            ax.fill_between(t, -0.5, 3.0, where=log.window, step="mid",
                            color=C["P"], alpha=0.12, lw=0)
        # audible interruptions
        arr = np.sort(np.array(log.voip_arrivals))
        gaps = np.diff(np.concatenate([[0], arr]))
        for g0, g in zip(np.concatenate([[0], arr])[:-1], gaps):
            if g > 0.3:
                ax.plot([g0, g0 + g], [3.08, 3.08], color="#e34948", lw=4,
                        solid_capstyle="butt")
        ax.set_yticks([0.4, 1.4, 2.4], ["Wi-Fi", "5G", "LEO"])
        ax.set_ylim(-0.1, 3.4)
        ttl = "B0 — break-before-make (red = audible voice outage)" \
            if name == "B0" else \
            "P — predicted window (shaded blue = duplication active)"
        ax.set_title(ttl, loc="left", fontsize=11)
        ax.grid(False)
    axes[1].set_xlabel("Journey time (s)")
    fig.suptitle("Same journey, two gateways — pale = link usable, solid = link carrying traffic",
                 y=1.02, fontsize=12)
    fig.savefig(out / "fig2_timeline.png")
    plt.close(fig)


def _bars(ax, rows, policies, met, title, unit, fmt="{:.2f}"):
    ys, hs = [], []
    for p in policies:
        m, h = _agg(rows, p, met)
        ys.append(m)
        hs.append(h)
    xs = np.arange(len(policies))
    ax.bar(xs, ys, 0.62, yerr=hs, capsize=3,
           color=[C[p] for p in policies], edgecolor="white", linewidth=1)
    for x, y in zip(xs, ys):
        ax.annotate(fmt.format(y), (x, y), textcoords="offset points",
                    xytext=(0, 5), ha="center", fontsize=9, color=INK)
    ax.set_xticks(xs, policies, rotation=20 if len(policies) > 4 else 0)
    ax.set_title(title, loc="left", fontsize=11)
    ax.set_ylabel(unit)
    ax.grid(axis="x", visible=False)


def fig_qoe(rows, out: Path):
    fig, axes = plt.subplots(1, 3, figsize=(11, 3.6))
    _bars(axes[0], rows, BASELINES, "mos_worst", "Worst 10 s voice MOS", "MOS")
    axes[0].set_ylim(1, 4.6)
    _bars(axes[1], rows, BASELINES, "video_stall_s",
          "Video stall time", "s per journey", "{:.1f}")
    _bars(axes[2], rows, BASELINES, "sat_mb",
          "Satellite data used", "MB per journey", "{:.1f}")
    fig.suptitle("QoE and cost per policy — mean ± 95% CI", y=1.04)
    fig.savefig(out / "fig3_qoe.png")
    plt.close(fig)


def fig_model(sweep_rows, t_ho, mu, sigma, out: Path):
    """Analytical model vs simulation (plan §9.3: agreement is a result)."""
    guards = sorted({r["t_guard"] for r in sweep_rows})
    sim_m, sim_h = [], []
    for g in guards:
        v = [r["ho_gap_mean_s"] for r in sweep_rows if r["t_guard"] == g]
        sim_m.append(np.mean(v))
        sim_h.append(1.96 * np.std(v, ddof=1) / np.sqrt(len(v)))
    gg, EI, _ = amodel.curve(t_ho, mu, sigma, np.linspace(0, max(guards), 80))
    fig, ax = plt.subplots(figsize=(7.5, 4.6))
    ax.plot(gg, EI + 0.1, color=INK2, lw=2, ls="--",
            label=f"analytical worst case: link dies the instant the forecast "
                  f"expires\n(μ={mu:.2f}s, σ={sigma:.1f}s, T_ho={t_ho:.1f}s)")
    ax.errorbar(guards, sim_m, yerr=sim_h, fmt="o", color=C["P"], ms=7,
                capsize=3, lw=1.5, label="simulated per-event gap (mean ± CI)")
    tg = amodel.recommend_t_guard(t_ho, mu, sigma, 0.90)
    ax.axvline(tg, color=C["P"], lw=1, alpha=0.5)
    ax.annotate(f"chosen T_guard = {tg:.1f} s", (tg, ax.get_ylim()[1] * 0.55),
                xytext=(6, 0), textcoords="offset points", fontsize=10)
    ax.set_xlabel("T_guard (s)")
    ax.set_ylabel("Interruption per handover event (s)")
    ax.set_title("Guard-time choice: the model is a conservative envelope\n"
                 "(links degrade gracefully in simulation; the envelope binds "
                 "under hard death — see the jamming stress)", fontsize=11)
    ax.legend(frameon=False, fontsize=9, loc="center right")
    fig.savefig(out / "fig4_model.png")
    plt.close(fig)


def fig_ablation(rows, out: Path):
    pols = ("P", "P-oracle", "P-nopred", "P-noclass", "P-nocost")
    have = [p for p in pols if any(r["policy"] == p for r in rows)]
    fig, axes = plt.subplots(1, 3, figsize=(12, 3.8))
    _bars(axes[0], rows, have, "int_avoid_s", "Avoidable voice interruption", "s", "{:.2f}")
    _bars(axes[1], rows, have, "overhead_pct", "Bandwidth overhead", "%", "{:.1f}")
    _bars(axes[2], rows, have, "sat_mb", "Satellite data", "MB", "{:.1f}")
    fig.suptitle("Ablations: what each ingredient of P buys", y=1.04)
    fig.savefig(out / "fig5_ablation.png")
    plt.close(fig)


def fig_noise(noise_rows, base_rows, out: Path):
    """Robustness (plan §9.4): forecast error converts into duplication
    cost, not into outage — the mechanism fails safe."""
    sig = sorted({r["sigma"] for r in noise_rows if r["sigma"] >= 0})

    def series(metric):
        m, h = [], []
        for s in sig:
            v = [r[metric] for r in noise_rows if r["sigma"] == s]
            m.append(np.mean(v))
            h.append(1.96 * np.std(v, ddof=1) / np.sqrt(len(v)))
        return m, h

    fig, axes = plt.subplots(1, 2, figsize=(10.5, 4.2))
    m, h = series("int_avoid_s")
    axes[0].errorbar(sig, m, yerr=h, fmt="-o", color=C["P"], lw=2, ms=6,
                     capsize=3, label="P, degraded predictor")
    b1, _ = _agg(base_rows, "B1", "int_avoid_s")
    axes[0].axhline(b1, color=C["B1"], lw=2, ls="--")
    axes[0].annotate("B1 minRTT (no prediction)", (sig[-1], b1 + 0.02),
                     ha="right", va="bottom", fontsize=9, color=INK)
    hv = [r["int_avoid_s"] for r in noise_rows if r["sigma"] < 0]
    if hv:
        axes[0].scatter([sig[-1] * 0.55], [np.mean(hv)], marker="D", s=50,
                        color=C["P-nopred"], zorder=5)
        axes[0].annotate("linear (no-ML) forecaster",
                         (sig[-1] * 0.55, np.mean(hv)),
                         textcoords="offset points", xytext=(8, 4), fontsize=9)
    axes[0].set_xlabel("Injected forecast error σ (s)")
    axes[0].set_ylabel("Avoidable voice interruption (s)")
    axes[0].set_title("Outage barely moves…", loc="left", fontsize=11)
    axes[0].legend(frameon=False, fontsize=9, loc="upper left")

    m, h = series("dup_window_s")
    axes[1].errorbar(sig, m, yerr=h, fmt="-o", color=C["P"], lw=2, ms=6,
                     capsize=3)
    axes[1].set_xlabel("Injected forecast error σ (s)")
    axes[1].set_ylabel("Duplication-window time (s)")
    axes[1].set_title("…the error is paid in duplication instead",
                      loc="left", fontsize=11)
    fig.suptitle("How good must the predictor be? Forecast error fails safe "
                 "(plan §9.4)", y=1.03)
    fig.savefig(out / "fig6_noise.png")
    plt.close(fig)


def all_figures(outdir: Path):
    from .experiment import read_csv
    outdir = Path(outdir)
    figdir = outdir / "figures"
    figdir.mkdir(parents=True, exist_ok=True)
    rows = read_csv(outdir / "results.csv")
    fig_tradeoff(rows, figdir)
    fig_qoe(rows, figdir)
    fig_ablation(rows, figdir)
    if (outdir / "demo_logs.pkl").exists():
        fig_timeline(outdir / "demo_logs.pkl", figdir)
    if (outdir / "sweep_tguard.csv").exists():
        import json
        tg = json.load(open(outdir / "t_guard.json"))
        fig_model(read_csv(outdir / "sweep_tguard.csv"),
                  tg["t_ho_s"], tg["mu_s"], tg["sigma_s"], figdir)
    if (outdir / "sweep_noise.csv").exists():
        fig_noise(read_csv(outdir / "sweep_noise.csv"), rows, figdir)
    print(f"  figures -> {figdir}")
