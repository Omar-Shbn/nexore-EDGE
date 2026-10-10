"""Link monitor — the ONLY view the decision stack has of the world.

Strict observability rule (plan §9.6):
  Permitted : replayed radio metrics (what the modem/driver would report,
              including scan results for unattached links), self-measured
              RTT/loss from 10 Hz probes on attached links, link age, speed.
  Forbidden : true capacity, any future trace value, coverage-map knowledge.

The simulator enforces this structurally: it hands the monitor a RadioSample
per link per tick and answers probe transactions; the trace arrays themselves
never cross this boundary.

Features follow plan §6: signal + slopes/variance over 1 s and 3 s windows,
RTT + slope + jitter, loss over short and long windows, link age, speed.
`probed` distinguishes measured loss/RTT from scan-only knowledge.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np

from .scenario import DT, TECHS

# sliding windows (plan §6: 1 s and 3 s)
W_SHORT = int(1.0 / DT)
W_LONG = int(3.0 / DT)

FEATURES = (
    "q", "sig", "sig_slope1", "sig_slope3", "sig_var", "rtt", "rtt_slope",
    "jitter", "loss1", "loss_rate", "probed", "age", "speed",
)


@dataclass
class RadioSample:
    """What a real device could read from its drivers at this instant."""
    tech: str
    radio_visible: bool   # beacon/SSB/satellite detectable in a scan
    signal: float         # RSSI / RSRP / SNR as the driver reports it
    attached: bool


def _norm_quality(tech: str, sig: float, loss_rate: float, rtt: float) -> float:
    """Normalised 0-1 quality so different technologies are comparable."""
    lo, hi = {"wifi": (-88.0, -55.0), "5g": (-118.0, -85.0), "leo": (2.0, 12.0)}[tech]
    q_sig = min(1.0, max(0.0, (sig - lo) / (hi - lo)))
    q = q_sig * (1.0 - min(1.0, 2.0 * loss_rate))
    if rtt > 150.0:
        q *= 0.7
    return q


class LinkMonitor:
    """Per-link sliding-window state."""

    def __init__(self, tech: str):
        self.tech = tech
        self.sig = deque(maxlen=W_LONG)
        self.rtt = deque(maxlen=W_LONG)
        self.probe_ok = deque(maxlen=W_LONG)   # 1 delivered / 0 lost
        self.age = 0.0
        self.visible = False

    def push(self, s: RadioSample, probe_rtt_ms: float | None, probe_lost: bool | None):
        self.visible = s.radio_visible
        if s.radio_visible:
            self.sig.append(s.signal)
            self.age += DT
        else:
            self.sig.append(-999.0)
            self.age = 0.0
        if probe_lost is None:
            # not attached: probe history goes stale, eventually empty
            if self.probe_ok:
                self.probe_ok.popleft()
            if self.rtt and len(self.probe_ok) == 0:
                self.rtt.clear()
        else:
            self.probe_ok.append(0.0 if probe_lost else 1.0)
            if not probe_lost and probe_rtt_ms is not None:
                self.rtt.append(probe_rtt_ms)

    # -- derived, observable-only features ------------------------------------
    @staticmethod
    def _slope(dq: deque, w: int) -> float:
        """Least-squares slope per second, closed form (no polyfit)."""
        xs = [v for v in list(dq)[-w:] if v > -500]
        n = len(xs)
        if n < max(3, w // 3):
            return 0.0
        x = np.asarray(xs)
        t = np.arange(n, dtype=float)
        tm = (n - 1) / 2.0
        denom = float(((t - tm) ** 2).sum()) * DT
        return float(((t - tm) * (x - x.mean())).sum() / denom)

    def features(self, speed_kmh: float) -> dict:
        sig_now = self.sig[-1] if self.sig and self.sig[-1] > -500 else -999.0
        probed = len(self.probe_ok) >= 3
        if probed:
            loss_rate = 1.0 - float(np.mean(self.probe_ok))
            short = list(self.probe_ok)[-W_SHORT:]
            loss1 = 1.0 - float(np.mean(short))
        else:
            loss_rate = loss1 = 0.0        # scan-only: no loss knowledge
        rtt = float(np.median(self.rtt)) if self.rtt else 999.0
        jitter = float(np.std(list(self.rtt)[-W_SHORT:])) if len(self.rtt) > 3 else 0.0
        vis_sig = [v for v in self.sig if v > -500]
        q = _norm_quality(self.tech, sig_now, loss_rate if probed else 0.0,
                          rtt if probed else 0.0) if self.visible else 0.0
        return {
            "q": q,
            "sig": sig_now if self.visible else -999.0,
            "sig_slope1": self._slope(self.sig, W_SHORT),
            "sig_slope3": self._slope(self.sig, W_LONG),
            "sig_var": float(np.var(vis_sig[-W_SHORT:])) if len(vis_sig) >= 3 else 0.0,
            "rtt": rtt,
            "rtt_slope": self._slope(self.rtt, W_SHORT) if len(self.rtt) > 3 else 0.0,
            "jitter": jitter,
            "loss1": loss1,
            "loss_rate": loss_rate,
            "probed": 1.0 if probed else 0.0,
            "age": self.age,
            "speed": speed_kmh,
        }


class Monitor:
    """All links + feature vector assembly for the predictor."""

    def __init__(self):
        self.links = {tech: LinkMonitor(tech) for tech in TECHS}
        self._speed = 0.0

    def push(self, samples: dict, probes: dict, speed_kmh: float):
        """samples: tech->RadioSample; probes: tech->(rtt_ms|None, lost|None)."""
        self._speed = speed_kmh
        for tech, s in samples.items():
            rtt, lost = probes.get(tech, (None, None))
            self.links[tech].push(s, rtt, lost)

    def features(self) -> dict:
        return {tech: lm.features(self._speed) for tech, lm in self.links.items()}

    @staticmethod
    def vector(feat: dict) -> np.ndarray:
        """Flatten one link's feature dict in canonical FEATURES order."""
        return np.array([feat[k] for k in FEATURES], dtype=float)
