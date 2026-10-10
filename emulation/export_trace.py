"""Export a simulator RouteTrace to the replayer's CSV format.

Columns: t, link, avail, signal, rate_mbps, delay_ms, loss_pct
The replayer is the ONLY process that reads this file (observability rule).

  python emulation/export_trace.py --speed 90 --direction outbound \
      --seed 1 --out emulation/trace_v90_out_s1.csv [--compress 3.0]

--compress N keeps every Nth tick, shortening wall-clock replay time while
preserving every transition (plan §8: ~3 minute compressed routes).
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from edgesim.scenario import Scenario, DT, TECHS   # noqa: E402
from edgesim import traces                         # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--speed", type=float, default=90.0)
    ap.add_argument("--direction", default="outbound")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--layout-seed", type=int, default=0)
    ap.add_argument("--jam", action="store_true")
    ap.add_argument("--compress", type=float, default=1.0,
                    help="keep every Nth tick (time compression)")
    ap.add_argument("--no-raca", action="store_true",
                    help="skip the Raca 5G calibration even if data exists")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    calib = None
    raca = sorted((ROOT / "data" / "raca").glob("*.csv"))
    if raca and not args.no_raca:
        calib = traces.raca_capacity_calibration(raca)
        print(f"5G capacity calibrated from {len(raca)} Raca file(s)")
    sc = Scenario(args.speed, args.direction, args.seed,
                  layout_seed=args.layout_seed)
    rt = traces.generate(sc, calib_5g=calib, jam=args.jam)
    step = max(1, int(args.compress))
    with open(args.out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t", "link", "avail", "signal", "rate_mbps",
                    "delay_ms", "loss_pct"])
        out_t = 0.0
        for i in range(0, rt.n, step):
            for tech in TECHS:
                lt = rt.links[tech]
                w.writerow([f"{out_t:.1f}", tech, int(lt.avail[i]),
                            f"{lt.signal[i]:.1f}",
                            f"{max(lt.cap_mbps[i], 0.01):.2f}",
                            f"{lt.rtt_ms[i] / 2:.1f}",
                            f"{min(lt.loss[i] * 100, 100):.2f}"])
            out_t += DT
    print(f"wrote {args.out}: {rt.n // step} ticks x {len(TECHS)} links "
          f"({rt.n // step * DT:.0f}s replay)")


if __name__ == "__main__":
    main()
