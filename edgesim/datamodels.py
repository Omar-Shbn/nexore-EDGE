"""Frozen data models (plan §5.2) — the interfaces between the four owners.

These shapes are shared by the simulator, the emulation tunnel and the logs;
JSON log records are these dataclasses serialised field-for-field.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict


@dataclass
class LinkState:            # monitor output, one per link per 100 ms
    t: float
    link: str               # "wifi" | "5g" | "leo"
    signal: float           # normalised 0..1 (RSSI / RSRP / SNR mapped per tech)
    rtt_ms: float
    jitter_ms: float
    loss: float             # 0..1, sliding window
    delivery_mbps: float
    signal_slope: float
    rtt_slope: float
    speed_kmh: float
    heading: float
    attached: bool
    cost_per_mb: float      # static per tech


@dataclass
class Prediction:
    t: float
    link: str
    t_life_hat: float       # seconds until unusable, capped at 10 s
    p_fail_h: float         # P(unusable within H = 3 s)


@dataclass
class ClassProfile:         # one row per traffic class (used by the utility)
    name: str               # "critical" | "interactive" | "bulk"
    max_rtt_ms: float
    max_loss: float
    min_mbps: float
    w_latency: float
    w_loss: float
    w_bandwidth: float
    w_cost: float


@dataclass
class FlowDecision:
    t: float
    flow_id: int
    cls: str
    paths: list             # one link = steer, two links = duplicate
    rate_cap_mbps: float | None = None   # set for deferred bulk


# Class profiles (plan §6: the critical limits are what define "unusable";
# weights are the APSH utility weights, reported in the deck).
PROFILES = {
    "critical": ClassProfile("critical", max_rtt_ms=300.0, max_loss=0.05,
                             min_mbps=0.1, w_latency=0.40, w_loss=0.40,
                             w_bandwidth=0.10, w_cost=0.10),
    "interactive": ClassProfile("interactive", max_rtt_ms=250.0, max_loss=0.02,
                                min_mbps=3.0, w_latency=0.30, w_loss=0.30,
                                w_bandwidth=0.30, w_cost=0.10),
    "bulk": ClassProfile("bulk", max_rtt_ms=1000.0, max_loss=0.10,
                         min_mbps=1.0, w_latency=0.10, w_loss=0.20,
                         w_bandwidth=0.50, w_cost=0.20),
}


def to_json_record(obj) -> dict:
    return asdict(obj)
