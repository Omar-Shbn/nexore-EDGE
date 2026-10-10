"""Scenario definition: route legs, coverage layout, technology constants.

One route crosses every technology (plan §2). Wired exists at the depot in
the narrative but is NOT a live data-plane link (v2 delta, ChatGPT review):
the vehicle is parked for seconds and wired teaches nothing about handover.

Distances are kilometres along the route; times are seconds.
"""
from __future__ import annotations

from dataclasses import dataclass, field

DT = 0.1  # simulation tick, seconds (100 ms, plan §5.3)

TECHS = ("wifi", "5g", "leo")

# --- Route ------------------------------------------------------------------
# Compressed route containing every transition (plan §8 does the same for
# emulation runs); legs keep the proportions of plan §2.
LEG_BOUNDS_KM = {
    "A_urban": (0.0, 1.5),      # depot + urban
    "B_highway": (1.5, 5.5),    # highway
    "C_fringe": (5.5, 7.2),     # rural fringe: 5G gives out
    "D_remote": (7.2, 8.0),     # remote site: LEO only
}
ROUTE_KM = 8.0

# Per-leg speed ceilings (km/h). The scenario "speed" scales the highway leg;
# urban traffic and the remote approach stay slow regardless.
LEG_SPEED_KMH = {
    "A_urban": 40.0,
    "B_highway": 100.0,   # scaled by scenario speed factor
    "C_fringe": 70.0,
    "D_remote": 30.0,
}

# The simulator sweeps 30/60/90/120 km/h, both directions (plan §2).
SPEEDS_KMH = (30.0, 60.0, 90.0, 120.0)   # highway-leg target speeds
DIRECTIONS = ("outbound", "return")       # depot->remote and reverse


# --- Technology constants -----------------------------------------------------
@dataclass(frozen=True)
class TechParams:
    """Static per-technology parameters (costs justified in README refs)."""
    attach_s: float          # time from 'attach' decision to first usable packet
    base_rtt_ms: float       # propagation + core latency when unloaded
    cost_per_mb: float       # relative monetary cost (wifi free, leo expensive)
    keepalive_kbps: float    # cost of merely keeping the session attached
    grace_s: float           # radio loss shorter than this keeps the session
                             # (a 2 s satellite obstruction is not a detach)


# Attach times per plan §8 (placeholders — cite before deck use):
# Wi-Fi 300 ms (re-assoc to known AP), 5G 150 ms (RRC reconfig to visible
# cell), LEO 2.5 s (Starlink session/route settle after reacquisition,
# published measurements report 1-5 s). Probe round = 100 ms.
TECH = {
    "wifi": TechParams(attach_s=0.3, base_rtt_ms=12.0, cost_per_mb=0.0,
                       keepalive_kbps=0.0, grace_s=2.0),
    "5g": TechParams(attach_s=0.15, base_rtt_ms=28.0, cost_per_mb=0.01,
                     keepalive_kbps=0.0, grace_s=5.0),
    "leo": TechParams(attach_s=2.5, base_rtt_ms=45.0, cost_per_mb=1.0,
                      keepalive_kbps=10.0, grace_s=30.0),
}

T_PROBE = 0.1    # one probe round (plan §8)
T_DETECT = 1.0   # reactive baselines' failure-detection delay (plan §8)

# nominal per-technology peak rates: scales the monitor's 0-1 quality into a
# crude bandwidth estimate for the utility (a documented simplification)
TECH_PEAK_MBPS = {"wifi": 300.0, "5g": 400.0, "leo": 60.0}


# --- Traffic classes (plan §2.1) ----------------------------------------------
@dataclass(frozen=True)
class FlowSpec:
    name: str
    cls: str                # critical | interactive | bulk
    rate_kbps: float        # offered rate (bulk = backlogged, this is a cap)
    pkt_bytes: int
    backlogged: bool = False


FLOWS = (
    # G.711 VoIP command channel: 64 kbps payload + RTP/UDP/IP overhead.
    FlowSpec("voip", "critical", 80.0, 200),
    # Telemetry/control messages.
    FlowSpec("telemetry", "critical", 8.0, 100),
    # Live video feed to operations.
    FlowSpec("video", "interactive", 2500.0, 1250),
    # Map/data sync, log upload — fills whatever is left.
    FlowSpec("bulk", "bulk", 50000.0, 1400, backlogged=True),
)

CLASSES = ("critical", "interactive", "bulk")


def leg_at(km: float) -> str:
    for leg, (a, b) in LEG_BOUNDS_KM.items():
        if a <= km <= b:
            return leg
    return "D_remote" if km > ROUTE_KM / 2 else "A_urban"


@dataclass
class Scenario:
    """One concrete run configuration."""
    speed_kmh: float = 100.0
    direction: str = "outbound"
    seed: int = 1
    route_km: float = ROUTE_KM
    # layout_seed decorrelates coverage layout between TRAIN and EVAL routes
    # (plan §7.1: disjoint routes). Same layout_seed => same AP/cell placement.
    layout_seed: int = 0

    @property
    def name(self) -> str:
        return f"v{int(self.speed_kmh)}_{self.direction}_s{self.seed}"

    def speed_profile(self, km: float) -> float:
        """Target speed (km/h) at a route position."""
        leg = leg_at(km)
        v = LEG_SPEED_KMH[leg]
        if leg == "B_highway":
            v = self.speed_kmh
        return v
