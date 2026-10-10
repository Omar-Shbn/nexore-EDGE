"""Markdown result tables for the deck, generated from results/*.csv only.

  python scripts/make_tables.py   ->  results/tables.md
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from edgesim.experiment import read_csv, summarize   # noqa: E402

OUT = ROOT / "results" / "tables.md"

HEAD = ("| Policy | Avoidable interruption (s) | Raw interruption (s) | "
        "Worst 10 s MOS | Overhead (%) | Satellite (MB) | Video stalls (s) | "
        "Handovers | Ping-pongs |")
SEP = "|" + "---|" * 9


def fmt(r):
    def pm(k):
        return f"{r[k]:.2f} ± {r[k + '_ci']:.2f}"
    return (f"| {r['policy']} | {pm('int_avoid_s')} | {pm('int_total_s')} | "
            f"{pm('mos_worst')} | {pm('overhead_pct')} | {r['sat_mb']:.0f} | "
            f"{pm('video_stall_s')} | {r['handovers']:.1f} | "
            f"{r['ping_pongs']:.1f} |")


def main():
    lines = ["# Results tables (generated — do not edit)", ""]
    rows = read_csv(ROOT / "results" / "results.csv")
    n = {r['policy'] for r in rows}
    lines += [f"Main grid: 4 speeds x 2 directions x "
              f"{len(rows) // max(len(n), 1) // 8} seeds, mean ± 95% CI.", "",
              "## Baselines and proposal", "", HEAD, SEP]
    order = ("B0", "B1", "B2", "P", "P-oracle", "P-nopred", "P-noclass",
             "P-nocost")
    summ = {r["policy"]: r for r in summarize(rows)}
    for p in order:
        if p in summ:
            lines.append(fmt(summ[p]))
    sp = ROOT / "results" / "stress.csv"
    if sp.exists():
        lines += ["", "## Jamming stress (§9.7 — abrupt 5G kill, "
                  "prediction expected to help less)", "", HEAD, SEP]
        for r in summarize(read_csv(sp)):
            lines.append(fmt(r))
    tg = ROOT / "results" / "t_guard.json"
    ps = ROOT / "results" / "predictor_stats.json"
    if tg.exists() and ps.exists():
        t = json.load(open(tg))
        p = json.load(open(ps))
        lines += ["", "## Derived constants", "",
                  f"- Predictor (held-out): GBR MAE {p['gbr']['mae']:.2f} s "
                  f"vs linear {p['linear']['mae']:.2f} s -> shipped "
                  f"**{p['winner']}** (mu={t['mu_s']:+.2f} s, "
                  f"sigma={t['sigma_s']:.2f} s)",
                  f"- T_guard = T_ho - mu + z*sigma = {t['t_ho_s']:.1f} "
                  f"- ({t['mu_s']:+.2f}) + 1.28 x {t['sigma_s']:.2f} = "
                  f"**{t['t_guard_s']:.2f} s** (90% target)"]
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
