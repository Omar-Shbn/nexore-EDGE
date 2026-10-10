"""Evaluation metrics (plan §8) — results, not inputs.

All computed from the RunLog after the fact; nothing here feeds decisions.
"""
from __future__ import annotations

import numpy as np

from .scenario import DT

GAP_NOMINAL = 0.02      # voip inter-packet time at 50 pps
GAP_THRESH = 0.3        # a silence longer than this is an audible interruption
JITTER_BUF_MS = 60.0
CODEC_MS = 25.0
VIDEO_KBPS = 2500.0
PREBUF_S = 1.0
REBUF_S = 0.5


# ------------------------------------------------------------------ E-model --
def mos_from(loss_pct: float, delay_ms: float) -> float:
    """Simplified ITU-T G.107 E-model, G.711 (Ie=0, Bpl=25; plan §9.2)."""
    d = delay_ms
    Id = 0.024 * d + (0.11 * (d - 177.3) if d > 177.3 else 0.0)
    Ie_eff = 0.0 + (95.0 - 0.0) * loss_pct / (loss_pct + 25.0)
    R = 93.2 - Id - Ie_eff
    if R < 0:
        return 1.0
    if R > 100:
        return 4.5
    return 1.0 + 0.035 * R + 7e-6 * R * (R - 60.0) * (100.0 - R)


def video_playout(log):
    """Receiver playout-buffer model for the live video feed.

    Returns (stalls, stall_time_s, stalled) where `stalled` is a per-tick
    bool array: True while the player is not playing (initial buffering
    included). Shared by compute() and the replay dashboard so both show
    the same stalls."""
    rate_Bps = VIDEO_KBPS * 125.0
    buf = 0.0                      # seconds of media buffered
    playing = False
    stalls, stall_time = 0, 0.0
    stalled = np.zeros(log.n, bool)
    for i in range(log.n):
        buf += log.video_bytes[i] / rate_Bps
        if playing:
            buf -= DT
            if buf <= 0:
                buf = 0.0
                playing = False
                stalls += 1
        else:
            stall_time += DT
            if buf >= (PREBUF_S if stalls == 0 else REBUF_S):
                playing = True
        stalled[i] = not playing
    return stalls, stall_time, stalled


def _gaps(arrivals: np.ndarray, t_end: float):
    if len(arrivals) < 2:
        return np.array([t_end]), np.array([0.0])
    a = np.sort(arrivals)
    gaps = np.diff(np.concatenate([[0.0], a, [t_end]]))
    starts = np.concatenate([[0.0], a])
    return gaps, starts


def compute(log, rt) -> dict:
    t_end = float(log.t[-1] + DT)
    arr = np.array(log.voip_arrivals)
    dly = np.array(log.voip_delays)
    out = {}

    # --- interruption (headline, plan §9.2) -----------------------------------
    gaps, gstarts = _gaps(arr, t_end)
    out["int_max_s"] = float(gaps.max())
    bad = gaps > GAP_THRESH
    out["int_total_s"] = float((gaps[bad] - GAP_NOMINAL).sum())
    out["int_count"] = int(bad.sum())

    # avoidable interruption: the part of each gap during which SOME link was
    # usable (ground truth). A coverage hole — e.g. a satellite obstruction at
    # the remote site with nothing else in the air — is unavoidable for every
    # gateway (B2 proves the floor); what a mechanism can be blamed for is
    # the remainder. Both are reported.
    any_up = np.zeros(log.n, bool)
    for tech, us in log.truth_usable.items():
        any_up |= us
    hole = ~any_up
    out["cov_hole_s"] = float(hole.sum() * DT)
    avoid = 0.0
    for j in np.flatnonzero(bad):
        i0 = max(0, int(gstarts[j] / DT))
        i1 = min(log.n, int((gstarts[j] + gaps[j]) / DT) + 1)
        unavoidable = hole[i0:i1].sum() * DT
        avoid += max(0.0, gaps[j] - GAP_NOMINAL - unavoidable)
    out["int_avoid_s"] = float(avoid)

    # per ground-truth transition event: worst voip gap within +/-2 s
    ev_times = [t for (t, kind, _) in log.events
                if kind in ("switch", "link_drop")]
    per_ev = []
    for te in ev_times:
        m = (gstarts + gaps > te - 2.0) & (gstarts < te + 2.0)
        per_ev.append(gaps[m].max() if m.any() else 0.0)
    out["ho_events"] = len(ev_times)
    out["ho_gap_mean_s"] = float(np.mean(per_ev)) if per_ev else 0.0
    out["ho_gap_p95_s"] = float(np.percentile(per_ev, 95)) if per_ev else 0.0

    # --- voice loss & MOS -----------------------------------------------------
    out["voip_loss_pct"] = 100.0 * (1 - len(arr) / max(log.voip_sent, 1))
    out["voip_delay_p50_ms"] = float(np.percentile(dly, 50)) if len(dly) else 999.0
    out["voip_delay_p95_ms"] = float(np.percentile(dly, 95)) if len(dly) else 999.0
    # windowed MOS (10 s)
    mos_w = []
    for w0 in np.arange(0, t_end - 10, 10.0):
        sent_w = 10.0 / GAP_NOMINAL
        got = (arr >= w0) & (arr < w0 + 10)
        loss_pct = 100.0 * max(0.0, 1 - got.sum() / sent_w)
        d_m = float(np.mean(dly[got])) if got.any() else 400.0
        mos_w.append(mos_from(loss_pct, d_m + JITTER_BUF_MS + CODEC_MS))
    out["mos_mean"] = float(np.mean(mos_w)) if mos_w else 1.0
    out["mos_worst"] = float(np.min(mos_w)) if mos_w else 1.0

    # --- telemetry: delivery + longest silent period (plan §9.2) ---------------
    out["telem_dlv_pct"] = 100.0 * log.telem_delivered / max(log.telem_sent, 1)
    tgaps, _ = _gaps(np.array(log.telem_arrivals), t_end)
    out["telem_gap_s"] = float(tgaps.max())

    # --- handovers & ping-pongs (plan §9.2; ping-pong = return within T_dwell) -
    switches = [(t, d) for (t, kind, d) in log.events if kind == "switch"]
    out["handovers"] = len(switches)
    pp = 0
    for k in range(1, len(switches)):
        t1, d1 = switches[k]
        t0, d0 = switches[k - 1]
        a0, b0 = d0.split("->")
        a1, b1 = d1.split("->")
        if b1 == a0 and (t1 - t0) <= 5.0:
            pp += 1
    out["ping_pongs"] = pp

    # --- video stalls (playout buffer model) -----------------------------------
    stalls, stall_time, _ = video_playout(log)
    out["video_stalls"] = stalls
    out["video_stall_s"] = float(stall_time - PREBUF_S)  # exclude startup
    vd = log.video_delay_ms[np.isfinite(log.video_delay_ms)]
    out["video_delay_p95_ms"] = float(np.percentile(vd, 95)) if len(vd) else 999.0

    # --- cost side of the trade-off (plan §8: overhead, satellite bytes) -------
    sent_total = sum(log.sent_bytes.values())
    out["overhead_pct"] = 100.0 * (sent_total / max(log.app_bytes_delivered, 1) - 1)
    out["sat_mb"] = log.sent_bytes["leo"] / 1e6
    out["bulk_mb"] = float(log.bulk_bytes.sum() / 1e6)
    out["dup_window_s"] = float(log.window.sum() * DT)
    return out
