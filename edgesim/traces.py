"""Trace generation and loading.

A *trace* is the ground-truth time series the emulator replays onto each
link: availability, radio metric, capacity, base RTT, loss probability —
sampled every DT along the route (plan §7.1).

Two sources:
  1. `generate(scenario)` — synthetic traces from a mobility model + path-loss
     + shadowing, with per-technology parameters taken from published
     measurement studies (citations in README §Traces).
  2. `load_raca(csv)` — adapter for the Raca et al. 4G/5G production-network
     datasets (UCC MISL; v2 delta "real traces"). Raca rows carry RSRP/RSRQ/
     SNR/CQI + DL bitrate at 1 Hz on real drive routes; we resample to DT and
     use them to (a) calibrate the synthetic 5G capacity<->RSRP mapping and
     (b) run a "real-trace" condition for the 5G link.

The decision stack NEVER sees these arrays (observability rule §5.4): the
simulator replays them; the monitor observes only radio samples + its own
probe measurements.
"""
from __future__ import annotations

import csv
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .scenario import DT, TECHS, Scenario, ROUTE_KM, leg_at


@dataclass
class LinkTrace:
    """Ground truth for one link, arrays indexed by tick."""
    tech: str
    avail: np.ndarray       # bool — radio reachable at all
    signal: np.ndarray      # dBm-ish primary metric (RSSI / RSRP / SNR)
    cap_mbps: np.ndarray    # usable capacity if attached
    rtt_ms: np.ndarray      # unloaded path RTT
    loss: np.ndarray        # per-packet random loss probability
    extra: dict = field(default_factory=dict)  # e.g. serving cell id, obstruction


@dataclass
class RouteTrace:
    scenario: Scenario
    t: np.ndarray           # seconds
    km: np.ndarray          # position along route
    speed_kmh: np.ndarray
    links: dict             # tech -> LinkTrace

    @property
    def n(self) -> int:
        return len(self.t)


# ----------------------------------------------------------------- mobility --
def _mobility(sc: Scenario, rng: np.random.Generator):
    """Integrate position along the route with small speed noise."""
    km_list, v_list = [], []
    km = 0.0
    v = 0.0
    while km < sc.route_km:
        v_target = sc.speed_profile(km)
        # first-order lag toward target + jitter
        v += 0.4 * (v_target - v) * DT + rng.normal(0, 0.3)
        v = max(5.0, v)
        km += v / 3600.0 * DT
        km_list.append(min(km, sc.route_km))
        v_list.append(v)
    km_arr = np.array(km_list)
    if sc.direction == "return":
        km_arr = sc.route_km - km_arr  # same physical layout, traversed backwards
    return km_arr, np.array(v_list)


def _ar1(n: int, rng, sigma: float, rho: float = 0.995) -> np.ndarray:
    """Correlated shadowing (AR(1), ~decorrelates over tens of metres)."""
    x = np.zeros(n)
    innov = rng.normal(0, sigma * math.sqrt(1 - rho**2), n)
    for i in range(1, n):
        x[i] = rho * x[i - 1] + innov[i]
    return x


# ------------------------------------------------------------------- layout --
def _layout(sc: Scenario, rng_layout: np.random.Generator):
    """AP/cell placement. Depends only on layout_seed => train/eval routes
    with different layout_seed are disjoint (plan §9.6)."""
    # Wi-Fi: depot AP + two roadside hotspots on the highway leg.
    wifi_aps = [0.15] + sorted(rng_layout.uniform(2.0, 5.0, 2).tolist())
    # 5G: dense urban, regular highway, sparse fringe, none in the last ~1 km.
    cells = []
    cells += sorted(rng_layout.uniform(0.0, 1.5, 3).tolist())          # urban
    cells += [1.5 + i * 1.0 + rng_layout.uniform(-0.15, 0.15) for i in range(5)]  # highway
    cells += [6.0 + rng_layout.uniform(-0.2, 0.2)]                     # lone fringe cell
    cells = [c for c in cells if c < 6.5]
    return wifi_aps, sorted(cells)


# ---------------------------------------------------------------- generators --
def _wifi(km, rng, aps):
    n = len(km)
    d = np.min(np.abs(km[:, None] - np.array(aps)[None, :]), axis=1) * 1000.0  # m
    # log-distance path loss, tx ~20 dBm, PL(1m)=40 dB, n=3.0 + shadowing
    rssi = 20 - 40 - 30.0 * np.log10(np.maximum(d, 1.0)) + _ar1(n, rng, 3.0)
    avail = rssi > -88.0
    # Shannon-ish mapping clipped to 802.11ac-class rates
    snr = rssi + 92.0
    cap = np.clip(2.2 * snr, 0, 300.0) * avail
    rtt = np.full(n, 12.0) + _ar1(n, rng, 1.0)
    loss = np.clip((-78.0 - rssi) / 10.0, 0, 0.6) * 0.1
    loss = np.where(avail, np.clip(loss + 0.001, 0, 0.5), 1.0)
    return LinkTrace("wifi", avail, rssi, cap, np.maximum(rtt, 5.0), loss)


def _5g(km, rng, cells, calib=None):
    n = len(km)
    carr = np.array(cells)
    d = np.abs(km[:, None] - carr[None, :]) * 1000.0
    serving = np.argmin(d, axis=1)
    dmin = d[np.arange(n), serving]
    # Effective 3.5 GHz macro budget: ~-75 dBm near a cell, ~-97 at a highway
    # cell edge (500 m), weak but alive out to ~1 km. The rural fringe does
    # not fade politely: a terrain ridge just past the last cell adds a
    # knife-edge diffraction drop (~28 dB over ~120 m), so the 5G collapse
    # is sharp and its warning time scales with vehicle speed — which is
    # precisely where prediction has to earn its keep (plan §6.1 rationale).
    rsrp = -75.0 - 42.0 * np.log10(np.maximum(dmin, 150.0) / 150.0) \
        + _ar1(n, rng, 4.0)
    ridge = 28.0 / (1.0 + np.exp(-(km - 7.05) / 0.035))
    rsrp = rsrp - ridge
    avail = rsrp > -118.0
    sinr = np.clip((rsrp + 115.0) / 2.2 + _ar1(n, rng, 1.5), -5, 30)
    if calib is not None:
        # Raca-calibrated mapping, tapered to zero below the data's
        # RSRP domain instead of clamping to its left-most bin
        g = np.concatenate([[-125.0], calib[0]])
        c = np.concatenate([[0.0], calib[1]])
        cap = np.interp(rsrp, g, c)
    else:
        cap = np.clip(26.0 * np.log2(1 + 10 ** (sinr / 10.0)), 0, 400.0)
    cap = cap * avail
    rtt = np.full(n, 28.0) + _ar1(n, rng, 3.0)
    loss = np.where(avail, 0.002 + np.clip((-108.0 - rsrp) / 40.0, 0, 0.3), 1.0)
    # micro-outage at serving-cell change (HO interruption ~80-200 ms)
    ho = np.flatnonzero(np.diff(serving) != 0) + 1
    for i in ho:
        j = min(n, i + int(rng.uniform(0.08, 0.2) / DT) + 1)
        loss[i:j] = np.maximum(loss[i:j], 0.9)
    return LinkTrace("5g", avail, rsrp, cap, np.maximum(rtt, 15.0), loss,
                     extra={"serving": serving, "sinr": sinr})


def _leo(km, t, rng):
    n = len(km)
    snr = 9.0 + _ar1(n, rng, 1.2)
    # obstruction events: short dips; more likely in urban canyon (leg A)
    loss = np.full(n, 0.005)
    avail = np.ones(n, bool)
    i = 0
    while i < n:
        urban = leg_at(km[i]) == "A_urban"
        p = (0.004 if urban else 0.0012)  # events per tick
        if rng.random() < p:
            dur = int(rng.uniform(1.0, 6.0) / DT)
            snr[i:i + dur] -= rng.uniform(6, 12)
            loss[i:i + dur] = np.clip(loss[i:i + dur] + rng.uniform(0.3, 0.9), 0, 1)
            i += dur
        i += 1
    avail = snr > 2.0
    cap = np.clip(12.0 + 2.5 * snr, 0, 60.0) * avail
    # LEO RTT: ~45 ms base with 15 s handover sawtooth jitter
    rtt = 45.0 + 8.0 * np.abs(np.sin(2 * np.pi * t / 15.0)) + _ar1(n, rng, 3.0)
    loss = np.where(avail, loss, 1.0)
    return LinkTrace("leo", avail, snr, cap, np.maximum(rtt, 35.0), loss)


def generate(sc: Scenario, calib_5g=None, jam: bool = False) -> RouteTrace:
    """Generate the full ground-truth trace for one scenario run.

    jam=True adds the §9.7 stress case: an abrupt, non-gradual 5G outage on
    the highway leg — signal stays healthy, traffic dies. Prediction should
    help less here; honest reporting of that is part of the method."""
    rng = np.random.default_rng(10_000 + sc.seed * 7)
    rng_layout = np.random.default_rng(999 + sc.layout_seed)
    km, v = _mobility(sc, rng)
    n = len(km)
    t = np.arange(n) * DT
    aps, cells = _layout(sc, rng_layout)
    links = {
        "wifi": _wifi(km, rng, aps),
        "5g": _5g(km, rng, cells, calib=calib_5g),
        "leo": _leo(km, t, rng),
    }
    if jam:
        g = links["5g"]
        jam_rng = np.random.default_rng(4242 + sc.seed)
        on_hw = np.flatnonzero((km > 2.0) & (km < 5.0))
        if len(on_hw):
            j0 = int(jam_rng.choice(on_hw))
            j1 = min(n, j0 + int(30.0 / DT))
            g.loss[j0:j1] = 1.0          # abrupt: no signal degradation lead
    return RouteTrace(sc, t, km, v, links)


# ------------------------------------------------------------- Raca adapter --
RACA_COLMAP = {
    # canonical -> candidate column names in the UCC MISL 4G/5G CSVs
    "ts": ["Timestamp", "timestamp"],
    "speed": ["Speed", "speed"],
    "rsrp": ["RSRP", "NRxRSRP", "rsrp"],
    "rsrq": ["RSRQ", "NRxRSRQ", "rsrq"],
    "snr": ["SNR", "snr"],
    "cqi": ["CQI", "cqi"],
    "dl_kbps": ["DL_bitrate", "DL_bitrate(kbps)", "dl_bitrate"],
    "state": ["State", "state"],
}


def load_raca(path: str | Path) -> dict:
    """Parse one Raca 4G/5G dataset CSV into canonical 1 Hz arrays."""
    rows = list(csv.DictReader(open(path, newline="", encoding="utf-8-sig")))
    if not rows:
        raise ValueError(f"empty Raca file: {path}")

    def col(name, cast=float, default=np.nan):
        for cand in RACA_COLMAP[name]:
            if cand in rows[0]:
                out = []
                for r in rows:
                    v = r.get(cand, "")
                    try:
                        out.append(cast(v))
                    except (TypeError, ValueError):
                        out.append(default)
                return np.array(out)
        return None

    out = {k: col(k) for k in ("speed", "rsrp", "rsrq", "snr", "cqi", "dl_kbps")}
    out = {k: v for k, v in out.items() if v is not None}
    if "rsrp" not in out or "dl_kbps" not in out:
        raise ValueError(f"{path}: no RSRP/DL_bitrate columns — not a Raca trace?")
    return out


def raca_capacity_calibration(files: list) -> tuple:
    """Fit a monotone RSRP -> capacity (Mbps) curve from Raca drive traces.

    Returns (rsrp_grid, cap_grid) usable with np.interp. This is how the real
    data enters the synthetic 5G model (v2: 'real traces ... calibrate').
    Throughput samples are app- and demand-limited, so the p90 of DL bitrate
    per RSRP bin is used as the achievable-capacity proxy (median would give
    the app rate, not the link)."""
    rsrp_all, cap_all = [], []
    for f in files:
        d = load_raca(f)
        m = np.isfinite(d["rsrp"]) & np.isfinite(d["dl_kbps"]) & (d["dl_kbps"] > 0)
        rsrp_all.append(d["rsrp"][m])
        cap_all.append(d["dl_kbps"][m] / 1000.0)
    rsrp = np.concatenate(rsrp_all)
    cap = np.concatenate(cap_all)
    grid = np.arange(-125, -70, 2.5)
    pct = []
    for g in grid:
        m = (rsrp >= g) & (rsrp < g + 2.5)
        pct.append(np.percentile(cap[m], 90) if m.sum() >= 20 else np.nan)
    pct = np.array(pct)
    ok = np.isfinite(pct)
    g, m = grid[ok] + 1.25, pct[ok]
    m = np.maximum.accumulate(m)  # enforce monotone non-decreasing
    return g, m


def raca_5g_linktrace(path: str | Path, n_ticks: int, rng=None) -> LinkTrace:
    """Replay a real Raca drive segment as the 5G link (real-trace condition).

    1 Hz source resampled to DT with linear interpolation; loss inferred from
    bitrate collapses + RSRP floor, since the dataset logs no per-packet loss.
    """
    d = load_raca(path)
    src_n = len(d["rsrp"])
    src_t = np.arange(src_n, dtype=float)
    t = np.linspace(0, src_n - 1, n_ticks)
    rsrp = np.interp(t, src_t, np.nan_to_num(d["rsrp"], nan=-125.0))
    cap = np.interp(t, src_t, np.nan_to_num(d["dl_kbps"], nan=0.0)) / 1000.0
    avail = rsrp > -120.0
    loss = np.where(avail, 0.002 + np.clip((-110.0 - rsrp) / 40.0, 0, 0.3), 1.0)
    rtt = np.full(n_ticks, 35.0)
    if "snr" in d:
        snr = np.interp(t, src_t, np.nan_to_num(d["snr"], nan=0.0))
    else:
        snr = np.zeros(n_ticks)
    return LinkTrace("5g", avail, rsrp, cap * avail, rtt, loss,
                     extra={"sinr": snr, "serving": np.zeros(n_ticks, int)})


# ----------------------------------------------------------------- save/load --
def save(rt: RouteTrace, path: str | Path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    arrs = {"t": rt.t, "km": rt.km, "speed": rt.speed_kmh}
    for tech, lt in rt.links.items():
        for f in ("avail", "signal", "cap_mbps", "rtt_ms", "loss"):
            arrs[f"{tech}__{f}"] = getattr(lt, f)
        for k, v in lt.extra.items():
            arrs[f"{tech}__x_{k}"] = v
    np.savez_compressed(path, **arrs)


def load(path: str | Path, sc: Scenario) -> RouteTrace:
    z = np.load(path)
    links = {}
    for tech in TECHS:
        extra = {k.split("__x_")[1]: z[k] for k in z.files
                 if k.startswith(f"{tech}__x_")}
        links[tech] = LinkTrace(tech, z[f"{tech}__avail"], z[f"{tech}__signal"],
                                z[f"{tech}__cap_mbps"], z[f"{tech}__rtt_ms"],
                                z[f"{tech}__loss"], extra)
    return RouteTrace(sc, z["t"], z["km"], z["speed"], links)
