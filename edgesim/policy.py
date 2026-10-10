"""Policy engine: baselines B0/B1/B2, proposal P, and ablations (plan §7, §9.1).

A policy sees ONLY monitor features (+ its predictor, if any) and returns a
Decision. The simulator executes decisions; policies never touch traces.

  B0  single link, signal threshold + hysteresis, break-before-make
  B1  minRTT multipath: all traffic on lowest-RTT link, failover on detected loss
  B2  always duplicate every flow on the TWO best links
  P   predictive, class-aware, cost-aware selective redundancy (ours, §7)

Ablations (plan §9.1):
  P-nopred   window triggered by threshold crossing instead of forecast
  P-noclass  all flows duplicated in the window, bulk never deferred
  P-nocost   partner chosen by quality only; LEO treated like any link
  P-oracle   perfect predictor (upper bound on what prediction can buy)
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .scenario import DT, TECHS, TECH, FLOWS, TECH_PEAK_MBPS, T_DETECT
from .datamodels import PROFILES

# constants (plan §7, Proposed defaults — all reported in the deck)
T_HOLD = 1.0       # partner must probe clean this long before completion
T_DWELL = 5.0      # min time between transition triggers (ping-pong brake)
MARGIN = 0.10      # utility margin for completion & cheapest-partner rule
HYST = 1.0         # forecast hysteresis for false-alarm cancellation
R_DEFER = 0.10     # deferred bulk keeps 10% of its normal rate
Q_LOW = 0.25       # B0's "this link is in trouble"
Q_OK = 0.40        # B0's "alternative looks comfortable"
T_MAX = 10.0       # predictor horizon clip


@dataclass
class Decision:
    """What the gateway should do this tick."""
    attach: set = field(default_factory=set)     # techs that should be attached
    assign: dict = field(default_factory=dict)   # flow -> [techs] (2 = duplicate)
    bulk_factor: float = 1.0                     # R_DEFER when bulk is deferred
    window_open: bool = False                    # in a protected transition
    active: str = ""                             # the policy's primary link
    t_life: float = float("nan")                 # forecast life of `active`, s
                                                 # (logging only; NaN if no forecaster)


def _flows_on(tech: str) -> dict:
    return {f.name: [tech] for f in FLOWS}


# ------------------------------------------------------- utility (plan §7) ---
def utility(tech: str, f: dict, cls: str = "critical") -> float:
    """APSH weighted score: latency, loss, bandwidth vs the class profile,
    minus a cost term. Infeasible links score -inf.

    For unattached links the monitor has scan knowledge only: RTT falls back
    to the technology's nominal base RTT (public knowledge, not the trace)
    and loss to a signal-derived proxy — a documented simplification."""
    if f["sig"] <= -500:
        return float("-inf")
    cp = PROFILES[cls]
    probed = f["probed"] > 0.5
    rtt = f["rtt"] if probed else TECH[tech].base_rtt_ms
    loss = f["loss_rate"] if probed else 0.02 * (1.0 - f["q"])
    bw_est = f["q"] * TECH_PEAK_MBPS[tech]
    if rtt > cp.max_rtt_ms or loss > cp.max_loss or bw_est < cp.min_mbps * 0.5:
        return float("-inf")
    f_lat = max(0.0, 1.0 - rtt / cp.max_rtt_ms)
    f_loss = max(0.0, 1.0 - loss / cp.max_loss)
    f_bw = min(1.0, bw_est / max(cp.min_mbps, 0.1))
    cost = TECH[tech].cost_per_mb
    return (cp.w_latency * f_lat + cp.w_loss * f_loss
            + cp.w_bandwidth * f_bw - cp.w_cost * cost)


class Policy:
    name = "base"

    def __init__(self):
        self.active = None
        self._booted = False

    def boot(self, feats: dict):
        """First tick: start on the best visible link — the gateway was
        already connected before the journey began."""
        if not self._booted:
            self._booted = True
            vis = {t: feats[t]["q"] for t in TECHS if feats[t]["sig"] > -500}
            if vis:
                self.active = max(vis, key=vis.get)

    def decide(self, i: int, feats: dict, attached: set) -> Decision:
        raise NotImplementedError


# --------------------------------------------------------------------- B0 ----
class B0_SingleRSSI(Policy):
    """What a normal device does today: one link, threshold + hysteresis,
    break-before-make."""
    name = "B0"

    def __init__(self, hysteresis_s: float = 1.0):
        super().__init__()
        self.active = "wifi"
        self.bad_for = 0.0
        self.hyst = hysteresis_s

    def decide(self, i, feats, attached):
        self.boot(feats)
        act = self.active
        q_act = feats[act]["q"]
        self.bad_for = self.bad_for + DT if q_act < Q_LOW else 0.0
        if self.bad_for >= self.hyst:
            alts = {t: feats[t]["q"] for t in TECHS if t != act}
            best = max(alts, key=alts.get)
            if alts[best] > Q_OK or q_act <= 0.0:
                self.active = best        # break-before-make: old is gone NOW
                self.bad_for = 0.0
        return Decision(attach={self.active}, assign=_flows_on(self.active),
                        active=self.active)


# --------------------------------------------------------------------- B1 ----
class B1_MinRTT(Policy):
    """MPTCP-like: every link attached, traffic on the min-RTT live path,
    failover only after t_detect of solid probe loss (plan §8)."""
    name = "B1"

    def __init__(self):
        super().__init__()
        self.active = "wifi"
        self.dead_for = 0.0

    def decide(self, i, feats, attached):
        self.boot(feats)
        fa = feats[self.active]
        # detection: loss1 is the 1 s probe-loss window ~= t_detect = 1 s
        failing = (fa["sig"] <= -500
                   or (fa["probed"] > 0.5 and fa["loss1"] >= 0.9))
        self.dead_for = self.dead_for + DT if failing else 0.0
        live = [t for t in TECHS
                if feats[t]["sig"] > -500
                and not (feats[t]["probed"] > 0.5 and feats[t]["loss1"] >= 0.9)]
        if self.dead_for >= T_DETECT or self.active not in live:
            if live:
                self.active = min(live, key=lambda t: feats[t]["rtt"])
                self.dead_for = 0.0
        elif live:
            best = min(live, key=lambda t: feats[t]["rtt"])
            if feats[best]["rtt"] < 0.8 * fa["rtt"]:
                self.active = best
        return Decision(attach=set(TECHS), assign=_flows_on(self.active),
                        active=self.active)


# --------------------------------------------------------------------- B2 ----
class B2_Redundant(Policy):
    """Always duplicate every flow on the two best links (plan §9.1)."""
    name = "B2"

    def decide(self, i, feats, attached):
        self.boot(feats)
        vis = [t for t in TECHS if feats[t]["sig"] > -500] or ["leo"]
        ranked = sorted(vis, key=lambda t: feats[t]["q"], reverse=True)
        pair = ranked[:2] if len(ranked) >= 2 else ranked
        # sticky primary: only re-rank the logged 'active' on a clear win,
        # so the handover/ping-pong metric reflects real moves
        if self.active not in pair or \
                feats[pair[0]]["q"] > feats[self.active]["q"] + 0.10:
            self.active = pair[0]
        return Decision(attach=set(TECHS),
                        assign={f.name: list(pair) for f in FLOWS},
                        active=self.active)


# ---------------------------------------------------------------------- P ----
class P_Selective(Policy):
    """Ours — the §7 state machine verbatim:
      1. start a protected transition when t_life_hat(A) < T_guard
      2. complete it once the partner has probed clean for T_hold
      3. cancel a false alarm when A's forecast recovers
      4. hard break-before-make fallback if A dies with no partner ready
    """
    name = "P"

    def __init__(self, predictor, t_guard: float,
                 class_aware: bool = True, cost_aware: bool = True,
                 predictive: bool = True):
        super().__init__()
        self.pred = predictor
        self.t_guard = t_guard
        self.class_aware = class_aware
        self.cost_aware = cost_aware
        self.predictive = predictive
        self.active = "wifi"
        self.partner = None            # in_transition == partner is not None
        self.kind = "danger"           # "danger" | "econ" window
        self.stable_for = 0.0
        self.recovered_for = 0.0
        self.last_trigger = -1e9

    # -- choose_partner(A), plan §7 -------------------------------------------
    def _choose_partner(self, feats, fc):
        cands, feasible = [], []
        for t in TECHS:
            if t == self.active:
                continue
            u = utility(t, feats[t], "critical")
            if u == float("-inf"):
                continue
            feasible.append((t, u))
            if fc[t][0] <= self.t_guard + T_HOLD:   # must outlive the window
                continue
            cands.append((t, u))
        if not cands:
            # no candidate is forecast to outlive the whole window: a
            # shorter-lived partner still beats a break-before-make gap
            if not feasible:
                return None
            return max(feasible, key=lambda tu: fc[tu[0]][0])[0]
        best_u = max(u for _, u in cands)
        eligible = [(t, u) for t, u in cands if u >= best_u - MARGIN]
        if self.cost_aware:
            # cheapest qualifying partner; satellite only as last resort
            return min(eligible, key=lambda tu: (TECH[tu[0]].cost_per_mb, -tu[1]))[0]
        return max(eligible, key=lambda tu: tu[1])[0]

    def decide(self, i, feats, attached):
        self.boot(feats)
        now = i * DT
        fc = self.pred.forecast(feats, i)   # tech -> (t_life_hat, p_fail_h)
        A = self.active
        fa = feats[A]

        if self.predictive:
            danger = fc[A][0] < self.t_guard
        else:  # ablation P-nopred: threshold crossing, no lead time
            danger = fa["q"] < Q_LOW

        # --- 1. start a protected transition ---------------------------------
        if (self.partner is None and danger
                and now - self.last_trigger > T_DWELL):
            p = self._choose_partner(feats, fc)
            if p is not None:
                self.partner, self.kind = p, "danger"
                self.stable_for = 0.0
                self.recovered_for = 0.0
                self.last_trigger = now

        # 1b. economic transition: a clearly better AND cheaper link exists
        # (e.g. 5G reappears while riding satellite home). Same protected
        # machinery — make-before-break is free when both links are cheap.
        if (self.partner is None and not danger and self.cost_aware
                and now - self.last_trigger > T_DWELL):
            u_a = utility(A, fa)
            for t in TECHS:
                if t == A or TECH[t].cost_per_mb >= TECH[A].cost_per_mb:
                    continue
                if (feats[t]["q"] > Q_OK and fc[t][0] > self.t_guard + T_HOLD
                        and utility(t, feats[t]) > u_a + MARGIN):
                    self.partner, self.kind = t, "econ"
                    self.stable_for = 0.0
                    self.last_trigger = now
                    break

        # --- 2./3. complete or cancel ----------------------------------------
        if self.partner is not None:
            P = self.partner
            fp = feats[P]
            probed_ok = (P in attached and fp["probed"] > 0.5
                         and fp["loss1"] < 0.1)
            self.stable_for = self.stable_for + DT if probed_ok else 0.0
            a_unusable = fa["sig"] <= -500 or (fa["probed"] > 0.5
                                               and fa["loss1"] >= 0.5)
            better = (utility(P, fp) - utility(A, fa) > MARGIN
                      or (self.kind == "econ"
                          and utility(P, fp) >= utility(A, fa) - MARGIN))
            if self.stable_for >= T_HOLD and (
                    a_unusable or better
                    or utility(A, fa) == float("-inf")):
                self.active, self.partner = P, None       # complete
            elif self.kind == "danger":
                recovered = fc[A][0] > self.t_guard + HYST
                self.recovered_for = self.recovered_for + DT if recovered else 0.0
                if self.recovered_for >= T_HOLD:
                    self.partner = None                    # false alarm
            else:  # econ window: cancel if the partner stops qualifying
                if fp["probed"] > 0.5 and self.stable_for == 0.0 \
                        and utility(P, fp) < utility(A, fa):
                    self.partner = None

        # --- 4. hard fallback: A died with no partner ready -------------------
        A = self.active
        fa = feats[A]
        if fa["sig"] <= -500 or (fa["probed"] > 0.5 and fa["loss1"] >= 0.98):
            alts = [t for t in TECHS if t != A and feats[t]["sig"] > -500]
            if alts and self.partner is None:
                self.active = max(alts, key=lambda t: utility(t, feats[t]))
                # break-before-make: the gap is recorded by the metrics

        # --- attachment set (cost-aware: LEO only when needed) ----------------
        attach = {"wifi", "5g"}
        if (not self.cost_aware or self.active == "leo"
                or self.partner == "leo"):
            attach.add("leo")

        # --- per-flow assignment ----------------------------------------------
        A = self.active
        if self.partner is not None:
            P = self.partner
            fp = feats[P]
            p_ready = P in attached and fp["probed"] > 0.5 and fp["loss1"] < 0.5
            if self.class_aware:
                assign = {"voip": [A, P], "telemetry": [A, P],   # duplicate
                          "video": [P] if p_ready else [A],      # pre-switch
                          "bulk": [A]}
                bulk_factor = R_DEFER                            # defer
            else:  # ablation P-noclass
                assign = {f.name: [A, P] for f in FLOWS}
                bulk_factor = 1.0
        else:
            assign = _flows_on(A)
            bulk_factor = 1.0

        return Decision(attach=attach, assign=assign, bulk_factor=bulk_factor,
                        window_open=self.partner is not None, active=A,
                        t_life=float(fc[A][0]))


# ------------------------------------------------------------------ registry --
def make_policy(name: str, predictor=None, oracle=None, t_guard: float = 4.0):
    """Build a policy by registry name. P variants need a predictor
    (P-oracle takes the oracle forecaster instead)."""
    if name == "B0":
        return B0_SingleRSSI()
    if name == "B1":
        return B1_MinRTT()
    if name == "B2":
        return B2_Redundant()
    if name == "P":
        return P_Selective(predictor, t_guard)
    if name == "P-nopred":
        return P_Selective(predictor, t_guard, predictive=False)
    if name == "P-noclass":
        return P_Selective(predictor, t_guard, class_aware=False)
    if name == "P-nocost":
        return P_Selective(predictor, t_guard, cost_aware=False)
    if name == "P-oracle":
        return P_Selective(oracle, t_guard)
    raise KeyError(name)


POLICIES = ("B0", "B1", "B2", "P")
ABLATIONS = ("P-nopred", "P-noclass", "P-nocost", "P-oracle")
