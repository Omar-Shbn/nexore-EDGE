"""One-command reproduction (plan §9.6): traces -> predictor -> grid ->
stress -> sweeps -> summary tables -> figures.

  python scripts/run_all.py --quick            # smoke run (~2 min)
  python scripts/run_all.py --jobs 8           # full grid, 8 processes
  python scripts/run_all.py --seeds 30         # more repetitions
  python scripts/run_all.py --no-dashboard     # skip the HTML replay

Raca real-trace calibration (v2 §9.4): drop UCC MISL 4G/5G dataset CSVs
into data/raca/ and the 5G capacity<->RSRP mapping is fitted from them.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from edgesim import experiment, plots, traces  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="smoke run")
    ap.add_argument("--seeds", type=int, default=20)
    ap.add_argument("--jobs", type=int,
                    default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--no-ablations", action="store_true")
    ap.add_argument("--no-sweeps", action="store_true")
    ap.add_argument("--no-stress", action="store_true")
    ap.add_argument("--no-dashboard", action="store_true")
    ap.add_argument("--out", default=str(ROOT / "results"))
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    # 0. optional Raca calibration (v2 delta: real traces)
    calib = None
    raca_files = sorted((ROOT / "data" / "raca").glob("*.csv"))
    if raca_files:
        print(f"[0/7] Raca calibration from {len(raca_files)} file(s)")
        calib = traces.raca_capacity_calibration(raca_files)
        json.dump({"rsrp": list(map(float, calib[0])),
                   "cap_mbps": list(map(float, calib[1]))},
                  open(out / "raca_calibration.json", "w"), indent=2)
    else:
        print("[0/7] no data/raca/*.csv — synthetic 5G capacity model "
              "(documented defaults)")

    print("[1/7] training predictor on disjoint TRAIN routes "
          "(GBR + linear baseline, both reported)")
    fc = experiment.train_predictor(out, calib=calib, quick=args.quick)
    print(f"      winner={fc.winner} (GBR MAE {fc.gbr['mae']:.2f}s vs "
          f"linear {fc.linear['mae']:.2f}s) | shipped eps: "
          f"mu={fc.mu:+.2f}s sigma={fc.sigma:.2f}s")

    tg = experiment.derive_t_guard(fc)
    json.dump(tg, open(out / "t_guard.json", "w"), indent=2)
    print(f"[2/7] T_guard = T_ho - mu + z*sigma = {tg['t_guard_s']:.2f} s "
          f"(T_ho={tg['t_ho_s']:.1f}, mu={tg['mu_s']:+.2f}, "
          f"sigma={tg['sigma_s']:.2f})")

    print(f"[3/7] main grid ({args.jobs} jobs)")
    fc_path = out / "predictor.pkl"
    rows = experiment.run_grid(out, fc_path, tg["t_guard_s"],
                               n_seeds=args.seeds,
                               with_ablations=not args.no_ablations,
                               quick=args.quick, calib=calib, jobs=args.jobs)
    experiment._write_csv(out / "summary.csv", experiment.summarize(rows))
    experiment._write_csv(
        out / "summary_by_condition.csv",
        experiment.summarize(rows, by=("policy", "speed", "direction")))

    if not args.no_stress:
        print("[4/7] §9.7 jamming stress scenario")
        experiment.run_stress(out, fc_path, tg["t_guard_s"],
                              n_seeds=3 if args.quick else 10,
                              calib=calib, jobs=args.jobs)
    else:
        print("[4/7] stress skipped")

    if not args.no_sweeps:
        print("[5/7] sweeps: T_guard validation + predictor robustness")
        n = 3 if args.quick else 8
        experiment.sweep_t_guard(out, fc_path,
                                 [0.5, 1.0, 2.0, 3.0, 4.0, 5.0, 6.5, 8.0],
                                 n_seeds=n, calib=calib, jobs=args.jobs)
        experiment.sweep_predictor_noise(out, fc, tg["t_guard_s"],
                                         [0.0, 0.5, 1.0, 2.0, 4.0, 6.0],
                                         n_seeds=n, calib=calib)
    else:
        print("[5/7] sweeps skipped")

    print("[6/7] figures")
    plots.all_figures(out)
    if not args.no_dashboard:
        print("[7/7] replay dashboard")
        import subprocess
        subprocess.run([sys.executable, str(ROOT / "scripts" / "make_dashboard.py"),
                        "--jobs", str(min(args.jobs, 4)),
                        "--out", str(out / "dashboard.html")], check=True)
    print(f"done in {time.time()-t0:.0f}s -> {out}")


if __name__ == "__main__":
    main()
