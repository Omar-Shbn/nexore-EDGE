"""Experiment grid: speed x direction x seed x policy, with CIs (plan §9.6).

Everything is seeded and logged; results.csv is the single source every
figure and table is generated from (plots are never drawn by hand).
Runs parallelise across traces with ProcessPoolExecutor.
"""
from __future__ import annotations

import csv
import json
import os
import pickle
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np

from .scenario import Scenario, SPEEDS_KMH, DIRECTIONS, TECH, T_PROBE
from . import traces
from .simulator import run as sim_run
from .metrics import compute
from .policy import make_policy, POLICIES, ABLATIONS, T_HOLD
from .predictor import Forecaster, Oracle, Heuristic, training_routes
from . import model as amodel

KEY_METRICS = (
    "int_total_s", "int_avoid_s", "cov_hole_s", "int_max_s", "int_count",
    "ho_gap_mean_s", "ho_gap_p95_s",
    "voip_loss_pct", "voip_delay_p95_ms", "mos_mean", "mos_worst",
    "telem_dlv_pct", "telem_gap_s", "video_stalls", "video_stall_s",
    "video_delay_p95_ms", "overhead_pct", "sat_mb", "bulk_mb",
    "dup_window_s", "ho_events", "handovers", "ping_pongs",
)


def eval_scenarios(n_seeds: int, speeds=None) -> list:
    """EVAL grid: layout_seed=0, run seeds 1..n — disjoint from training
    (layout 101-103, seeds 500+)."""
    speeds = speeds or SPEEDS_KMH
    return [Scenario(speed_kmh=v, direction=d, seed=s, layout_seed=0)
            for v in speeds for d in DIRECTIONS
            for s in range(1, n_seeds + 1)]


class NoisyForecaster:
    """Deliberately degraded predictor for the robustness sweep (plan §9.4:
    'how bad can the predictor get before P stops beating B1?')."""

    def __init__(self, base, sigma_s: float, seed: int = 0):
        self.base = base
        self.sigma = sigma_s
        self.rng = np.random.default_rng(31337 + seed)

    def forecast(self, feats, i=None):
        out = {}
        for tech, (t_life, p) in self.base.forecast(feats, i).items():
            t = max(0.0, min(10.0, t_life + self.rng.normal(0, self.sigma)))
            out[tech] = (t, p)
        return out


def train_predictor(outdir: Path, calib=None, quick=False) -> Forecaster:
    n = 4 if quick else 8
    routes = training_routes(n + 2, calib=calib)
    fc = Forecaster().fit(routes[:n], routes[n:])
    fc.save(outdir / "predictor.pkl")
    json.dump({
        "gbr": fc.gbr, "linear": fc.linear,
        "gbr_mae_per_tech_s": fc.per_tech,
        "winner": fc.winner,
        "shipped_mu_s": fc.mu, "shipped_sigma_s": fc.sigma,
    }, open(outdir / "predictor_stats.json", "w"), indent=2)
    return fc


def derive_t_guard(fc: Forecaster) -> dict:
    """T_guard = T_ho - mu + z*sigma (plan §9.3), never hand-tuned.
    T_ho uses the worst-case partner (LEO attach) + one probe round."""
    t_ho = TECH["leo"].attach_s + T_PROBE
    t_guard = amodel.recommend_t_guard(t_ho, fc.mu, fc.sigma, p=0.90)
    return {"mu_s": fc.mu, "sigma_s": fc.sigma, "t_ho_s": t_ho,
            "t_hold_s": T_HOLD, "t_guard_s": float(t_guard)}


# ------------------------------------------------------------ parallel glue --
_FC_CACHE = {}


def _get_fc(path: str) -> Forecaster:
    if path not in _FC_CACHE:
        _FC_CACHE[path] = Forecaster.load(path)
    return _FC_CACHE[path]


def _one(rt, name, fc, t_guard, seed):
    if name == "P-oracle":
        pol = make_policy(name, oracle=Oracle(rt), t_guard=t_guard)
    elif name == "P-heur":
        pol = make_policy("P", predictor=Heuristic(), t_guard=t_guard)
    else:
        pol = make_policy(name, predictor=fc, t_guard=t_guard)
    log = sim_run(rt, pol, seed=seed)
    return compute(log, rt), log


def _worker(args):
    """One trace, all policies. Runs in a child process."""
    scd, policies, t_guard, calib, fc_path, jam = args
    sc = Scenario(**scd)
    fc = _get_fc(fc_path)
    rt = traces.generate(sc, calib_5g=calib, jam=jam)
    rows = []
    for name in policies:
        m, _ = _one(rt, name, fc, t_guard, sc.seed)
        rows.append({"policy": name, "speed": sc.speed_kmh,
                     "direction": sc.direction, "seed": sc.seed,
                     "jam": int(jam), "t_guard": t_guard, **m})
    return rows


def _init_worker():
    # one BLAS/OpenMP thread per worker — the parallelism is across traces
    for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ[var] = "1"


def _run_parallel(tasks, jobs):
    out = []
    if jobs <= 1:
        for k, t in enumerate(tasks):
            out.extend(_worker(t))
            print(f"  [{k+1:3d}/{len(tasks)}] done", flush=True)
        return out
    with ProcessPoolExecutor(max_workers=jobs, initializer=_init_worker) as ex:
        futs = {ex.submit(_worker, t): t for t in tasks}
        for k, f in enumerate(as_completed(futs)):
            out.extend(f.result())
            if (k + 1) % 10 == 0 or k + 1 == len(futs):
                print(f"  [{k+1:3d}/{len(futs)}] traces done", flush=True)
    return out


def _sc_dict(sc: Scenario) -> dict:
    return {"speed_kmh": sc.speed_kmh, "direction": sc.direction,
            "seed": sc.seed, "layout_seed": sc.layout_seed}


# -------------------------------------------------------------------- stages --
def run_grid(outdir: Path, fc_path: Path, t_guard: float,
             n_seeds: int = 20, with_ablations: bool = True,
             quick: bool = False, calib=None, jobs: int = 1):
    policies = list(POLICIES) + (list(ABLATIONS) if with_ablations else [])
    scens = eval_scenarios(2 if quick else n_seeds)
    tasks = [(_sc_dict(sc), policies, t_guard, calib, str(fc_path), False)
             for sc in scens]
    t0 = time.time()
    rows = _run_parallel(tasks, jobs)
    print(f"  grid: {len(rows)} runs in {time.time()-t0:.0f}s")
    _write_csv(outdir / "results.csv", rows)
    # matched B0/P pair on one trace for the demo & timeline figure
    fc = _get_fc(str(fc_path))
    sc = Scenario(120.0, "outbound", seed=1, layout_seed=0)
    rt = traces.generate(sc, calib_5g=calib)
    demo = {}
    for name in ("B0", "P"):
        _, log = _one(rt, name, fc, t_guard, sc.seed)
        demo[name] = (log, rt)
    pickle.dump(demo, open(outdir / "demo_logs.pkl", "wb"))
    return rows


def run_stress(outdir: Path, fc_path: Path, t_guard: float,
               n_seeds: int = 10, calib=None, jobs: int = 1):
    """§9.7 jamming stress: abrupt 5G kill, prediction expected to help
    less. P-nocost is included because keeping LEO warm is exactly what
    the jam rewards — the price of cost-awareness, measured."""
    pols = list(POLICIES) + ["P-nocost"]
    scens = [Scenario(90.0, "outbound", seed=s, layout_seed=0)
             for s in range(1, n_seeds + 1)]
    tasks = [(_sc_dict(sc), pols, t_guard, calib, str(fc_path), True)
             for sc in scens]
    rows = _run_parallel(tasks, jobs)
    _write_csv(outdir / "stress.csv", rows)
    _write_csv(outdir / "stress_summary.csv", summarize(rows))
    return rows


def sweep_t_guard(outdir: Path, fc_path: Path, guards, n_seeds=8, calib=None,
                  jobs: int = 1):
    """Simulated E[interruption](T_guard) to validate the §9.3 model."""
    tasks = []
    for s in range(1, n_seeds + 1):
        sc = Scenario(90.0, "outbound", seed=s, layout_seed=0)
        for g in guards:
            tasks.append((_sc_dict(sc), ["P"], g, calib, str(fc_path), False))
    rows = _run_parallel(tasks, jobs)   # each row carries its t_guard
    _write_csv(outdir / "sweep_tguard.csv", rows)
    return rows


def sweep_predictor_noise(outdir: Path, fc: Forecaster, t_guard, sigmas,
                          n_seeds=8, calib=None):
    """Robustness: degrade the predictor until the idea stops paying.
    Serial (NoisyForecaster state is cheap; sweep is small)."""
    rows = []
    for s in range(1, n_seeds + 1):
        sc = Scenario(90.0, "outbound", seed=s, layout_seed=0)
        rt = traces.generate(sc, calib_5g=calib)
        for sig in sigmas:
            noisy = NoisyForecaster(fc, sig, seed=s)
            pol = make_policy("P", predictor=noisy, t_guard=t_guard)
            log = sim_run(rt, pol, seed=s)
            rows.append({"sigma": sig, "seed": s, **compute(log, rt)})
        # linear heuristic forecaster as the no-ML reference point
        pol = make_policy("P", predictor=Heuristic(), t_guard=t_guard)
        log = sim_run(rt, pol, seed=s)
        rows.append({"sigma": -1.0, "seed": s, **compute(log, rt)})
    _write_csv(outdir / "sweep_noise.csv", rows)
    return rows


# ---------------------------------------------------------------- summaries --
def ci95(x):
    x = np.asarray(x, float)
    m = float(np.mean(x))
    half = 1.96 * float(np.std(x, ddof=1)) / np.sqrt(len(x)) if len(x) > 1 else 0.0
    return m, half


def summarize(rows, by=("policy",)):
    groups = {}
    for r in rows:
        groups.setdefault(tuple(r[k] for k in by), []).append(r)
    out = []
    for key, rs in sorted(groups.items(), key=lambda kv: str(kv[0])):
        row = dict(zip(by, key))
        row["n"] = len(rs)
        for met in KEY_METRICS:
            vals = [r[met] for r in rs if met in r]
            if not vals:
                continue
            m, h = ci95(vals)
            row[met] = round(m, 4)
            row[met + "_ci"] = round(h, 4)
        out.append(row)
    return out


def _write_csv(path: Path, rows: list):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    cols = []
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    print(f"  wrote {path} ({len(rows)} rows)")


def read_csv(path) -> list:
    rows = []
    for r in csv.DictReader(open(path)):
        rows.append({k: (v if k in ("policy", "direction") else float(v))
                     for k, v in r.items() if v != ""})
    return rows
