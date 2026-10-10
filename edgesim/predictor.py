"""Link-lifetime prediction (plan §6).

Target: T_life = time until the link becomes *unusable*, where unusable is
the critical-class definition of §6: loss >= 5% over a 500 ms window, or
RTT >= 300 ms, or link down — sustained for >= 300 ms. Labels are computed
offline from emulator ground truth; features come only from the monitor
(observability rule §9.6).

Outputs per link: t_life_hat (regression, capped at 10 s) and p_fail(H=3 s)
derived from it (plan §5.2 Prediction).

Two models, both reported (plan §6, Q3): a linear slope extrapolation
(explainable baseline) and sklearn's HistGradientBoostingRegressor; the
held-out winner ships. Error mean (mu) and s.d. (sigma) on held-out traces
feed the analytical model (§9.3). No LSTM.
"""
from __future__ import annotations

import math
import pickle
from pathlib import Path

import numpy as np

from .scenario import DT, TECHS, Scenario
from .monitor import Monitor, RadioSample, FEATURES, _norm_quality
from .traces import RouteTrace, generate
from .simulator import unusable_mask, probe_link

T_MAX = 10.0   # horizon clip for t_life (s)
H_FAIL = 3.0   # horizon for p_fail (plan §5.2)


# ------------------------------------------------------------- ground truth --
def label_t_life(lt) -> np.ndarray:
    """Seconds until the link next becomes unusable (clipped at T_MAX)."""
    un = unusable_mask(lt)
    n = len(un)
    t = np.full(n, T_MAX)
    dist = np.inf
    for i in range(n - 1, -1, -1):
        dist = 0.0 if un[i] else (dist + DT if np.isfinite(dist) else np.inf)
        t[i] = min(dist, T_MAX) if np.isfinite(dist) else T_MAX
    return t


# --------------------------------------------------------- measurement drive --
def measurement_drive(rt: RouteTrace, seed: int = 0, attached: bool = True):
    """Replay a route through the real Monitor, yielding runtime-identical
    features. Two regimes: probes on (attached instrumentation vehicle) and
    scan-only (so the model also learns unattached links)."""
    rng = np.random.default_rng(555 + seed + (0 if attached else 10_000))
    mon = Monitor()
    n = rt.n
    X = {t: np.zeros((n, len(FEATURES))) for t in TECHS}
    y = {t: label_t_life(rt.links[t]) for t in TECHS}
    for i in range(n):
        samples, probes = {}, {}
        for tech in TECHS:
            lt = rt.links[tech]
            samples[tech] = RadioSample(tech, bool(lt.avail[i]),
                                        float(lt.signal[i] + rng.normal(0, 0.5)),
                                        attached)
            probes[tech] = probe_link(lt, i, rng) if attached else (None, None)
        mon.push(samples, probes, float(rt.speed_kmh[i]))
        feats = mon.features()
        for tech in TECHS:
            X[tech][i] = Monitor.vector(feats[tech])
    return X, y


def _phi(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


# ------------------------------------------------------------------ learner --
class Forecaster:
    """HistGradientBoostingRegressor over monitor features + tech one-hot.
    Keeps held-out error stats (mu, sigma) for §9.3 and for p_fail."""

    def __init__(self):
        self.model = None
        self.winner = "gbr"           # "gbr" | "linear" — held-out choice (§6 Q3)
        self.mu = 0.0                 # winner's mean of eps = T_life - T_life_hat
        self.sigma = 1.0              # winner's s.d. of eps, near failures
        self.gbr = {}                 # held-out stats for the GBR
        self.linear = {}              # held-out stats for the slope baseline
        self.per_tech = {}            # tech -> {"mae"} (GBR)
        self.heuristic_mae = None     # kept for convenience
        self._heur = Heuristic()

    @staticmethod
    def _xmat(X: dict) -> np.ndarray:
        rows = []
        for ti, tech in enumerate(TECHS):
            onehot = np.zeros((len(X[tech]), len(TECHS)))
            onehot[:, ti] = 1.0
            rows.append(np.hstack([X[tech], onehot]))
        return np.vstack(rows)

    def fit(self, routes: list, val_routes: list):
        from sklearn.ensemble import HistGradientBoostingRegressor
        Xs, ys = [], []
        for rt in routes:
            for att in (True, False):      # probes on + scan-only regimes
                X, y = measurement_drive(rt, seed=rt.scenario.seed, attached=att)
                Xs.append(self._xmat(X))
                ys.append(np.concatenate([y[t] for t in TECHS]))
        self.model = HistGradientBoostingRegressor(
            max_iter=200, learning_rate=0.1, random_state=0
        ).fit(np.vstack(Xs), np.concatenate(ys))

        # held-out error stats near failures (labels < T_MAX), both models
        heur = Heuristic()
        eps_gbr, eps_lin = [], []
        err_t = {t: [] for t in TECHS}
        for rt in val_routes:
            X, y = measurement_drive(rt, seed=900 + rt.scenario.seed)
            for ti, tech in enumerate(TECHS):
                onehot = np.zeros((len(X[tech]), len(TECHS)))
                onehot[:, ti] = 1.0
                pred = np.clip(self.model.predict(
                    np.hstack([X[tech], onehot])), 0, T_MAX)
                m = y[tech] < T_MAX
                if m.sum() > 10:
                    eps_gbr.append(y[tech][m] - pred[m])
                    err_t[tech].append(np.abs(y[tech][m] - pred[m]))
                    hv = np.array([heur._t_life_row(X[tech][j], tech)
                                   for j in np.flatnonzero(m)])
                    eps_lin.append(y[tech][m] - hv)

        def stats(chunks):
            e = np.concatenate(chunks) if chunks else np.array([0.0])
            return {"mae": float(np.mean(np.abs(e))),
                    "mu": float(np.mean(e)),
                    "sigma": float(max(np.std(e), 0.1))}

        self.gbr = stats(eps_gbr)
        self.linear = stats(eps_lin)
        for tech in TECHS:
            e = np.concatenate(err_t[tech]) if err_t[tech] else np.array([0.0])
            self.per_tech[tech] = {"mae": float(np.mean(e))}
        self.heuristic_mae = self.linear["mae"]
        # ship whichever wins on held-out traces (plan §6, Q3)
        self.winner = "gbr" if self.gbr["mae"] <= self.linear["mae"] else "linear"
        w = self.gbr if self.winner == "gbr" else self.linear
        self.mu, self.sigma = w["mu"], w["sigma"]
        return self

    def forecast(self, feats: dict, i: int | None = None) -> dict:
        """tech -> (t_life_hat, p_fail_h). Raw forecasts: the error margin
        lives in T_guard (§9.3), not here. Dispatches to the held-out winner."""
        if self.winner == "linear":
            out = self._heur.forecast(feats, i)
            return {t: (tl, _phi((H_FAIL - tl) / self.sigma))
                    for t, (tl, _) in out.items()}
        out = {}
        rows, idx = [], []
        for ti, tech in enumerate(TECHS):
            f = feats[tech]
            if f["sig"] <= -500:           # radio not even visible
                out[tech] = (0.0, 1.0)
                continue
            rows.append(np.concatenate([Monitor.vector(f),
                                        np.eye(len(TECHS))[ti]]))
            idx.append(tech)
        if rows:
            t_hat = np.clip(self.model.predict(np.vstack(rows)), 0, T_MAX)
            for k, tech in enumerate(idx):
                th = float(t_hat[k])
                p_fail = _phi((H_FAIL - th) / self.sigma)
                out[tech] = (th, p_fail)
        return out

    def save(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        pickle.dump(self, open(path, "wb"))

    @staticmethod
    def load(path) -> "Forecaster":
        return pickle.load(open(path, "rb"))


# ------------------------------------------------------------------- oracle --
class Oracle:
    """Perfect forecasts from the trace itself — the P-oracle ablation's
    upper bound. Lives entirely on the simulator side of the boundary."""

    def __init__(self, rt: RouteTrace):
        self.t_life = {t: label_t_life(rt.links[t]) for t in TECHS}

    def forecast(self, feats: dict, i: int | None = None) -> dict:
        j = 0 if i is None else i
        return {t: (float(self.t_life[t][j]),
                    1.0 if self.t_life[t][j] < H_FAIL else 0.0)
                for t in TECHS}


class Heuristic:
    """Linear slope extrapolation to the technology's floor — the
    explainable baseline predictor of plan §6."""
    FLOOR = {"wifi": -88.0, "5g": -118.0, "leo": 2.0}
    _I = {k: FEATURES.index(k) for k in
          ("sig", "sig_slope3", "loss1", "probed", "q")}

    def _t_life_row(self, row: np.ndarray, tech: str) -> float:
        sig = row[self._I["sig"]]
        if sig <= -500:
            return 0.0
        slope = row[self._I["sig_slope3"]]
        head = sig - self.FLOOR[tech]
        t = T_MAX if slope >= -1e-3 else min(T_MAX, max(0.0, head / -slope))
        if row[self._I["probed"]] > 0.5 and row[self._I["loss1"]] > 0.3:
            t = min(t, 1.0)
        return t

    def forecast(self, feats: dict, i: int | None = None) -> dict:
        out = {}
        for tech in TECHS:
            t = self._t_life_row(Monitor.vector(feats[tech]), tech)
            out[tech] = (t, 1.0 if t < H_FAIL else 0.0)
        return out


# --------------------------------------------------------------- route sets --
def training_routes(n: int = 6, calib=None) -> list:
    """TRAIN routes: different layout seeds AND run seeds than eval
    (plan §9.6: predictor train/eval disjoint by trace). Same Raca
    calibration as eval, so train and runtime share the link physics."""
    out = []
    for k in range(n):
        sc = Scenario(speed_kmh=(30.0, 60.0, 90.0, 120.0)[k % 4],
                      direction=("outbound", "return")[(k // 2) % 2],
                      seed=500 + k, layout_seed=101 + (k % 3))
        out.append(generate(sc, calib_5g=calib))
    return out
