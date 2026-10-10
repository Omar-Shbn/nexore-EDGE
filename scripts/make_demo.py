"""Scripted side-by-side replay (plan §10): B0 vs P on the same trace,
rendered as an animated GIF for the 1-2 minute video. Built entirely from
logged runs — nothing is live, nothing is drawn by hand.

  python scripts/make_demo.py [--out results/demo.gif] [--fps 8] [--step 1.0]
"""
from __future__ import annotations

import argparse
import pickle
import sys
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation, PillowWriter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from edgesim.plots import TECH_C, C, INK, INK2, SURF   # noqa: E402

TECH_ROW = {"wifi": 0, "5g": 1, "leo": 2}


def trailing_delivery(arrivals: np.ndarray, t: float, w: float = 5.0) -> float:
    """Voice packets delivered in the trailing w seconds vs expected."""
    got = np.count_nonzero((arrivals > t - w) & (arrivals <= t))
    return min(1.0, got / (w * 50.0))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--logs", default=str(ROOT / "results" / "demo_logs.pkl"))
    ap.add_argument("--out", default=str(ROOT / "results" / "demo.gif"))
    ap.add_argument("--fps", type=int, default=8)
    ap.add_argument("--step", type=float, default=1.0,
                    help="simulated seconds per frame")
    args = ap.parse_args()

    demo = pickle.load(open(args.logs, "rb"))
    t_end = float(demo["P"][0].t[-1])
    frames = np.arange(0, t_end, args.step)

    fig, axes = plt.subplots(2, 1, figsize=(9, 5.2), sharex=True)
    fig.patch.set_facecolor(SURF)
    artists = {}
    arr = {}
    for ax, name in zip(axes, ("B0", "P")):
        log, rt = demo[name]
        arr[name] = np.sort(np.array(log.voip_arrivals))
        t = log.t
        for tech, k in TECH_ROW.items():
            us = log.truth_usable[tech]
            ax.fill_between(t, k, k + 0.8, where=us, step="mid",
                            color=TECH_C[tech], alpha=0.15, lw=0)
        act = np.array([TECH_ROW.get(a, -1) for a in log.active])
        for tech, k in TECH_ROW.items():
            ax.fill_between(t, k, k + 0.8, where=act == k, step="mid",
                            color=TECH_C[tech], alpha=0.85, lw=0)
        if log.window.any():
            ax.fill_between(t, -0.4, 3.2, where=log.window, step="mid",
                            color=C["P"], alpha=0.12, lw=0)
        gaps = np.diff(np.concatenate([[0], arr[name]]))
        for g0, g in zip(np.concatenate([[0], arr[name]])[:-1], gaps):
            if g > 0.3:
                ax.plot([g0, g0 + g], [3.05, 3.05], color="#e34948", lw=4,
                        solid_capstyle="butt")
        ax.set_yticks([0.4, 1.4, 2.4], ["Wi-Fi", "5G", "LEO"])
        ax.set_ylim(-0.4, 3.5)
        ax.set_facecolor(SURF)
        ax.grid(False)
        cursor = ax.axvline(0, color=INK, lw=1.2)
        status = ax.text(0.995, 0.96, "", transform=ax.transAxes, ha="right",
                         va="top", fontsize=10, color=INK)
        title = {"B0": "B0 — what a normal device does",
                 "P": "P — predictive selective redundancy (ours)"}[name]
        ax.set_title(title, loc="left", fontsize=11, color=INK)
        artists[name] = (cursor, status)
    axes[1].set_xlabel("journey time (s)")
    fig.suptitle("Same journey, same traffic — red bars are audible voice outages",
                 fontsize=12, color=INK)
    fig.tight_layout()

    def update(tf):
        out = []
        for name in ("B0", "P"):
            log, _ = demo[name]
            cursor, status = artists[name]
            cursor.set_xdata([tf, tf])
            i = min(int(tf / 0.1), log.n - 1)
            dv = trailing_delivery(arr[name], tf)
            dup = "DUP ON " if (name == "P" and log.window[i]) else ""
            status.set_text(f"{dup}link: {log.active[i] or '—':5s}  "
                            f"voice delivery {dv * 100:3.0f}%")
            status.set_color("#e34948" if dv < 0.7 else INK)
            out += [cursor, status]
        return out

    anim = FuncAnimation(fig, update, frames=frames, blit=True)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    anim.save(args.out, writer=PillowWriter(fps=args.fps), dpi=80)
    dur = len(frames) / args.fps
    print(f"wrote {args.out}: {len(frames)} frames, {dur:.0f}s at {args.fps} fps")


if __name__ == "__main__":
    main()
