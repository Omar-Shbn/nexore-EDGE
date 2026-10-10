"""Build the self-contained replay dashboard (plan §10).

Reruns 8 journeys (30/60/90/120 km/h x outbound/return, seed 1) under
B0 / B1 / B2 / P through the simulator and embeds the logged series in one
HTML file. Nothing is live and nothing is invented: every number on the page
comes from RunLog arrays or metrics.compute(), and the fleet columns come
from results/results.csv.

  python scripts/make_dashboard.py            ->  results/dashboard.html
  python scripts/make_dashboard.py --jobs 6
"""
from __future__ import annotations

import os

# One BLAS/OpenMP thread per worker: the parallelism is across journeys.
# Must be set before numpy/sklearn load; spawned workers inherit it. Without
# this, 4 workers x all-core GBR predict thrash and nothing finishes.
for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[_v] = "1"

import argparse
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from edgesim.scenario import (DT, TECHS, Scenario, SPEEDS_KMH,   # noqa: E402
                              DIRECTIONS, LEG_BOUNDS_KM, ROUTE_KM)
from edgesim import traces                                        # noqa: E402
from edgesim.policy import make_policy                            # noqa: E402
from edgesim.simulator import run as sim_run, usable              # noqa: E402
from edgesim.metrics import (compute, mos_from, _gaps, GAP_THRESH,  # noqa: E402
                             JITTER_BUF_MS, CODEC_MS, video_playout)
from edgesim.predictor import Forecaster                          # noqa: E402

POLICIES = ("B0", "B1", "B2", "P")
SUMMARY_KEYS = ("int_total_s", "int_avoid_s", "int_max_s", "mos_mean",
                "mos_worst", "voip_loss_pct", "telem_gap_s", "video_stalls",
                "video_stall_s", "overhead_pct", "sat_mb", "bulk_mb",
                "dup_window_s", "handovers", "ping_pongs", "cov_hole_s")
FLEET_KEYS = ("int_avoid_s", "int_total_s", "mos_worst", "overhead_pct",
              "sat_mb", "video_stall_s", "handovers")
ACT = {"wifi": 0, "5g": 1, "leo": 2, "": 3}
VOIP_PPS = 50.0


def _debounce(u: np.ndarray, min_len: int = 20) -> np.ndarray:
    """Absorb interior runs shorter than min_len ticks (2 s) so the event
    feed reports real transitions, not flicker."""
    u = u.copy()
    while True:
        runs, start = [], 0
        for k in range(1, len(u) + 1):
            if k == len(u) or u[k] != u[start]:
                runs.append((start, k, bool(u[start])))
                start = k
        short = [r for r in range(1, len(runs) - 1)
                 if runs[r][1] - runs[r][0] < min_len]
        if not short:
            return u
        a, b, _ = runs[short[0]]
        u[a:b] = runs[short[0] - 1][2]


def build_scenario(args):
    speed, direction, fc_path, t_guard, calib = args
    fc = Forecaster.load(fc_path)
    sc = Scenario(speed, direction, seed=1, layout_seed=0)
    rt = traces.generate(sc, calib_5g=calib)
    n = rt.n
    nsec = int(np.ceil(n * DT))
    sec_tick = np.minimum(np.arange(nsec) * 10 + 9, n - 1)   # last tick of s
    first_tick = np.minimum(np.arange(nsec) * 10, n - 1)

    us = {t: usable(rt.links[t]) for t in TECHS}
    use = np.zeros(n, int)
    for k, t in enumerate(TECHS):
        use |= us[t].astype(int) << k

    env = []                                    # debounced usability events
    for t in TECHS:
        d = _debounce(us[t])
        for k in np.flatnonzero(np.diff(d.astype(int)) != 0) + 1:
            env.append([round(k * DT, 1), t, int(d[k])])
    env.sort()

    out = {
        "n": n, "dur": round(n * DT, 1),
        "use": use.tolist(),
        "km": np.round(rt.km[::10], 3).tolist(),
        "spd": np.round(rt.speed_kmh[::10]).astype(int).tolist(),
        "env": env,
        "runs": {},
    }

    for name in POLICIES:
        pol = make_policy(name, predictor=fc, t_guard=t_guard)
        log = sim_run(rt, pol, seed=1)
        m = compute(log, rt)
        _, _, stalled = video_playout(log)

        arr = np.array(log.voip_arrivals)
        dly = np.array(log.voip_delays)
        keep = arr < n * DT
        arr, dly = arr[keep], dly[keep]
        cnt_tick = np.minimum(np.bincount(
            np.minimum((arr / DT).astype(int), n - 1), minlength=n), 7)
        sec_i = np.minimum(arr.astype(int), nsec - 1)
        cnt_sec = np.bincount(sec_i, minlength=nsec).astype(float)
        dsum_sec = np.bincount(sec_i, weights=dly, minlength=nsec)

        mos = np.empty(nsec)
        for s in range(nsec):                       # trailing 10 s window
            lo = max(0, s - 9)
            got = cnt_sec[lo:s + 1].sum()
            w = s + 1 - lo
            loss = max(0.0, 100.0 * (1.0 - got / (VOIP_PPS * w)))
            d = dsum_sec[lo:s + 1].sum() / got if got else 400.0
            mos[s] = mos_from(loss, d + JITTER_BUF_MS + CODEC_MS)

        g, gs = _gaps(np.sort(arr), float(log.t[-1] + DT))
        gaps = [[round(float(gs[j]), 2), round(float(g[j]), 2)]
                for j in np.flatnonzero(g > GAP_THRESH)]

        act = np.array([ACT[a] for a in log.active])
        dup = (log.dup_link.astype(int) + 1)             # 0 none, 1..3 tech+1
        code = (act + 4 * log.window.astype(int) + 8 * cnt_tick
                + 64 * stalled.astype(int) + 128 * dup)

        sent_total = log.sent_cum.sum(axis=1)
        ovh = np.where(log.useful_cum > 1e6,
                       (sent_total / np.maximum(log.useful_cum, 1) - 1) * 100,
                       0.0)
        run = {
            "code": code.tolist(),
            "gaps": gaps,
            "sec": {
                "mos": np.round(mos, 2).tolist(),
                "sat": np.round(log.sent_cum[sec_tick, 2] / 1e6, 2).tolist(),
                "ovh": np.round(ovh[sec_tick], 1).tolist(),
                "bulk": np.round(np.cumsum(log.bulk_bytes)[sec_tick] / 1e6
                                 ).astype(int).tolist(),
            },
            "sum": {k: round(float(m[k]), 3) for k in SUMMARY_KEYS},
        }
        if name == "P":
            tl = log.t_life[first_tick]
            run["sec"]["tlife"] = [None if not np.isfinite(v) else round(float(v), 1)
                                   for v in tl]
        out["runs"][name] = run
    return f"{int(speed)}_{direction}", out


def fleet_summary():
    p = ROOT / "results" / "results.csv"
    if not p.exists():
        return None
    from edgesim.experiment import read_csv, summarize
    rows = read_csv(p)
    out = {"n": 0}
    for r in summarize(rows):
        if r["policy"] in POLICIES:
            out[r["policy"]] = {k: [r[k], r[k + "_ci"]] for k in FLEET_KEYS}
            out["n"] = int(r["n"])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--out", default=str(ROOT / "results" / "dashboard.html"))
    args = ap.parse_args()

    import json as _json
    tg = _json.load(open(ROOT / "results" / "t_guard.json"))
    raca = sorted((ROOT / "data" / "raca").glob("*.csv"))
    calib = traces.raca_capacity_calibration(raca) if raca else None
    fc_path = str(ROOT / "results" / "predictor.pkl")

    tasks = [(v, d, fc_path, tg["t_guard_s"], calib)
             for v in SPEEDS_KMH for d in DIRECTIONS]
    print(f"building {len(tasks)} journeys x {len(POLICIES)} policies "
          f"({args.jobs} jobs)", flush=True)
    scenarios = {}
    with ProcessPoolExecutor(max_workers=args.jobs) as ex:
        for k, (key, sc) in enumerate(ex.map(build_scenario, tasks)):
            scenarios[key] = sc
            print(f"  [{k + 1}/{len(tasks)}] {key}: {sc['n']} ticks", flush=True)

    data = {
        "meta": {
            "tGuard": round(tg["t_guard_s"], 2),
            "dt": DT, "routeKm": ROUTE_KM,
            "legs": [{"id": k.split("_")[0], "name": k.split("_", 1)[1],
                      "a": a, "b": b} for k, (a, b) in LEG_BOUNDS_KM.items()],
            "speeds": list(SPEEDS_KMH), "dirs": list(DIRECTIONS),
        },
        "fleet": fleet_summary(),
        "scenarios": scenarios,
    }
    tpl = (ROOT / "dashboard" / "template.html").read_text(encoding="utf-8")
    assert "/*__DATA__*/null" in tpl
    html = tpl.replace("/*__DATA__*/null",
                       json.dumps(data, separators=(",", ":")))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size / 1e6:.2f} MB)")


if __name__ == "__main__":
    main()
