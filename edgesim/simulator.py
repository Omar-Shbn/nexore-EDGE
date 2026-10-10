"""Discrete-time data-plane simulator (the "emulator" of plan §7.2's role,
in Python for the parameter-sweep layer §7.4).

The simulator owns the ground truth (traces) and executes policy decisions.
It exposes to the policy ONLY what the observability rule permits, via the
Monitor: radio scan samples + probe transactions (plan §5.4).

Data plane model, per 100 ms tick:
  * attach state machine per link (attach_s per technology)
  * per-link priority queues (critical > interactive > bulk), tail drop
  * per-packet Bernoulli loss from the trace, batch-sampled per tick
  * duplication = same packet ids on two links, dedup keeps earliest copy
  * voip/telemetry/video are packet-level; bulk is fluid (backlogged sender)
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .scenario import DT, TECHS, TECH, FLOWS, FlowSpec
from .monitor import Monitor, RadioSample
from .traces import RouteTrace, LinkTrace

LATE_MS = 400.0          # a critical packet later than this is useless (lost)
BUF_S = 0.15             # per-link buffer: 150 ms of line rate
PROBE_NOISE_MS = 2.0


def unusable_mask(lt: LinkTrace) -> np.ndarray:
    """Ground-truth 'unusable' per plan §6: loss >= 5% over a 500 ms window,
    or RTT >= 300 ms, or link down — sustained for >= 300 ms.
    Simulator side only: predictor LABELS, never features."""
    n = len(lt.avail)
    w = max(1, int(0.5 / DT))
    loss500 = np.convolve(lt.loss, np.ones(w) / w, mode="same")
    cond = (~lt.avail) | (loss500 >= 0.05) | (lt.rtt_ms >= 300.0)
    need = max(1, int(0.3 / DT))
    out = np.zeros(n, bool)
    run = 0
    for i in range(n):
        run = run + 1 if cond[i] else 0
        if run >= need:
            out[i - run + 1:i + 1] = True
    return out


def usable(lt: LinkTrace) -> np.ndarray:
    return ~unusable_mask(lt)


def probe_link(lt: LinkTrace, i: int, rng, extra_q_ms: float = 0.0):
    """One 10 Hz probe on an attached link: (rtt_ms|None, lost|None)."""
    if not lt.avail[i]:
        return None, True
    lost = rng.random() < lt.loss[i]
    if lost:
        return None, True
    rtt = lt.rtt_ms[i] + extra_q_ms + abs(rng.normal(0, PROBE_NOISE_MS))
    return rtt, False


@dataclass
class RunLog:
    """Everything metrics need, nothing more."""
    n: int
    t: np.ndarray
    voip_arrivals: list = field(default_factory=list)   # arrival times (s)
    voip_delays: list = field(default_factory=list)     # one-way ms, per delivered pkt
    voip_sent: int = 0
    telem_sent: int = 0
    telem_delivered: int = 0
    telem_arrivals: list = field(default_factory=list)  # arrival times (s)
    video_bytes: np.ndarray = None       # delivered per tick
    video_delay_ms: np.ndarray = None
    bulk_bytes: np.ndarray = None
    sent_bytes: dict = None              # tech -> bytes put on the wire
    app_bytes_delivered: float = 0.0     # unique useful bytes (all flows)
    active: list = field(default_factory=list)
    window: np.ndarray = None
    truth_usable: dict = None            # tech -> bool array (ground truth)
    events: list = field(default_factory=list)  # (t, kind, detail)
    # per-tick series for the replay dashboard (logging only)
    sent_cum: np.ndarray = None          # (n, len(TECHS)) cumulative wire bytes
    useful_cum: np.ndarray = None        # (n,) cumulative unique useful bytes
    t_life: np.ndarray = None            # (n,) forecast life of active link, s
    dup_link: np.ndarray = None          # (n,) TECHS index carrying the voice
                                         # duplicate (attached only), else -1


class Gateway:
    """Attach state machine + queues for one run."""

    def __init__(self, rt: RouteTrace, rng):
        self.rt = rt
        self.rng = rng
        self.state = {t: "detached" for t in TECHS}
        self.ready_at = {t: 0.0 for t in TECHS}
        self.lost_since = {t: None for t in TECHS}   # radio-loss grace timer
        self.backlog = {t: {"critical": 0.0, "interactive": 0.0, "bulk": 0.0}
                        for t in TECHS}

    def attached(self) -> set:
        return {t for t, s in self.state.items() if s == "attached"}

    def step_attach(self, want: set, i: int, now: float, events: list):
        for tech in TECHS:
            lt = self.rt.links[tech]
            st = self.state[tech]
            if tech in want:
                if st == "detached":
                    self.state[tech] = "attaching"
                    self.ready_at[tech] = now + TECH[tech].attach_s
                    events.append((now, "attach_start", tech))
                elif st == "attaching" and now >= self.ready_at[tech]:
                    if lt.avail[i]:
                        self.state[tech] = "attached"
                        events.append((now, "attached", tech))
                    else:  # keep retrying until the radio is actually there
                        self.ready_at[tech] = now + TECH[tech].attach_s
                elif st == "attached":
                    if not lt.avail[i]:
                        # radio gone: session rides out a short loss (a 2 s
                        # satellite obstruction is not a detach) and drops
                        # only after the technology's grace period
                        if self.lost_since[tech] is None:
                            self.lost_since[tech] = now
                        elif now - self.lost_since[tech] > TECH[tech].grace_s:
                            self.state[tech] = "detached"
                            self.lost_since[tech] = None
                            for c in self.backlog[tech]:
                                self.backlog[tech][c] = 0.0
                            events.append((now, "link_drop", tech))
                    else:
                        self.lost_since[tech] = None
            else:
                if st != "detached":
                    self.state[tech] = "detached"
                    for c in self.backlog[tech]:
                        self.backlog[tech][c] = 0.0

    def serve(self, tech: str, i: int, arrivals: dict) -> tuple[dict, dict]:
        """Add per-class arrival bytes, serve in priority order.
        Returns (served_fraction per class, queue_delay_s per class)."""
        lt = self.rt.links[tech]
        cap_Bps = lt.cap_mbps[i] * 1e6 / 8.0
        budget = cap_Bps * DT
        bl = self.backlog[tech]
        served_frac, qdelay = {}, {}
        ahead = 0.0
        for cls in ("critical", "interactive", "bulk"):
            total = bl[cls] + arrivals.get(cls, 0.0)
            served = min(total, budget)
            budget -= served
            qdelay[cls] = (bl[cls] + ahead) / cap_Bps if cap_Bps > 1 else 9.9
            served_frac[cls] = served / total if total > 0 else 1.0
            bl[cls] = total - served
            ahead += bl[cls]
        # tail-drop overflow, lowest class first
        buf = max(cap_Bps * BUF_S, 64e3)
        excess = sum(bl.values()) - buf
        for cls in ("bulk", "interactive", "critical"):
            if excess <= 0:
                break
            drop = min(bl[cls], excess)
            bl[cls] -= drop
            excess -= drop
        return served_frac, qdelay


def run(rt: RouteTrace, policy, seed: int = 0) -> RunLog:
    rng = np.random.default_rng(777 + seed)
    n = rt.n
    gw = Gateway(rt, rng)
    mon = Monitor()
    log = RunLog(
        n=n, t=rt.t,
        video_bytes=np.zeros(n), video_delay_ms=np.full(n, np.nan),
        bulk_bytes=np.zeros(n),
        sent_bytes={t: 0.0 for t in TECHS},
        window=np.zeros(n, bool),
        truth_usable={t: usable(rt.links[t]) for t in TECHS},
        sent_cum=np.zeros((n, len(TECHS))), useful_cum=np.zeros(n),
        t_life=np.full(n, np.nan), dup_link=np.full(n, -1, np.int8),
    )
    frac = {f.name: 0.0 for f in FLOWS}           # fractional packet carry
    specs = {f.name: f for f in FLOWS}
    prev_active = None

    # Warm start: the gateway does not boot mid-journey. Whatever the policy
    # wants attached at t=0 is already attached (if the radio is there) —
    # otherwise every return run pays a fake LEO cold-attach at the kerb.
    boot_samples = {
        tech: RadioSample(tech, bool(rt.links[tech].avail[0]),
                          float(rt.links[tech].signal[0]), False)
        for tech in TECHS}
    mon.push(boot_samples, {t: (None, None) for t in TECHS},
             float(rt.speed_kmh[0]))
    dec0 = policy.decide(0, mon.features(), set())
    for tech in dec0.attach:
        if rt.links[tech].avail[0]:
            gw.state[tech] = "attached"

    for i in range(n):
        now = rt.t[i]

        # ---- 1. what the device can observe --------------------------------
        samples, probes = {}, {}
        for tech in TECHS:
            lt = rt.links[tech]
            samples[tech] = RadioSample(
                tech=tech, radio_visible=bool(lt.avail[i]),
                signal=float(lt.signal[i] + rng.normal(0, 0.5)),
                attached=gw.state[tech] == "attached")
            if gw.state[tech] == "attached":
                q_ms = gw.backlog[tech]["critical"] / max(lt.cap_mbps[i] * 125.0, 1)
                probes[tech] = probe_link(lt, i, rng, extra_q_ms=q_ms)
            else:
                probes[tech] = (None, None)
        mon.push(samples, probes, float(rt.speed_kmh[i]))
        feats = mon.features()

        # ---- 2. policy ------------------------------------------------------
        dec = policy.decide(i, feats, gw.attached())
        gw.step_attach(dec.attach, i, now, log.events)
        attached = gw.attached()
        log.window[i] = dec.window_open
        log.t_life[i] = dec.t_life
        voip_links = dec.assign.get("voip", [])
        other = [l for l in voip_links if l != dec.active and l in attached]
        if len(voip_links) > 1 and other:
            log.dup_link[i] = TECHS.index(other[0])
        log.active.append(dec.active)
        if prev_active is not None and dec.active != prev_active:
            log.events.append((now, "switch", f"{prev_active}->{dec.active}"))
        prev_active = dec.active

        # ---- 3. offered load ------------------------------------------------
        # packets for packetised flows (with fractional carry across ticks)
        npkts, bulk_links = {}, []
        arrivals = {t: {"critical": 0.0, "interactive": 0.0, "bulk": 0.0}
                    for t in TECHS}
        for f in FLOWS:
            links = [l for l in dec.assign.get(f.name, []) if l in attached]
            if f.backlogged:
                bulk_links = links
                continue
            raw = f.rate_kbps * 1000 / 8 * DT / f.pkt_bytes + frac[f.name]
            k = int(raw)
            frac[f.name] = raw - k
            npkts[f.name] = (k, links)
            for l in links:
                arrivals[l][f.cls] += k * f.pkt_bytes
                log.sent_bytes[l] += k * f.pkt_bytes
        # bulk: backlogged sender fills what the link has left (CC shortcut)
        bspec = specs["bulk"]
        for l in bulk_links:
            cap_B = rt.links[l].cap_mbps[i] * 1e6 / 8 * DT
            room = max(0.0, cap_B - arrivals[l]["critical"]
                       - arrivals[l]["interactive"]) * 0.9
            add = dec.bulk_factor * min(room, bspec.rate_kbps * 125 * DT)
            arrivals[l]["bulk"] += add
            log.sent_bytes[l] += add
        # LEO keepalive is real cost even when idle (plan §4.1 cost awareness)
        for tech in attached:
            ka = TECH[tech].keepalive_kbps * 125 * DT
            log.sent_bytes[tech] += ka

        # ---- 4. queues + per-copy delivery ----------------------------------
        served, qdel = {}, {}
        for tech in TECHS:
            served[tech], qdel[tech] = gw.serve(tech, i, arrivals[tech])

        def copies(k: int, links: list, cls: str):
            """Delivery mask + per-copy delay for k packets over `links`."""
            got = np.zeros(k, bool)
            dly = np.full(k, np.inf)
            for l in links:
                lt = rt.links[l]
                if not lt.avail[i]:
                    continue
                p_ok = (1 - lt.loss[i]) * served[l][cls]
                ok = rng.random(k) < p_ok
                d_ms = lt.rtt_ms[i] / 2 + qdel[l][cls] * 1000 \
                    + abs(rng.normal(0, 2.0))
                upd = ok & (d_ms < dly)
                dly[upd] = d_ms
                got |= ok
            return got, dly

        # voip — packet arrival times drive interruption & MOS
        k, links = npkts["voip"]
        log.voip_sent += k
        if k:
            got, dly = copies(k, links, "critical")
            got &= dly < LATE_MS
            offs = (np.arange(k) + 0.5) * DT / k
            for j in np.flatnonzero(got):
                log.voip_arrivals.append(now + offs[j] + dly[j] / 1000)
                log.voip_delays.append(dly[j])
            log.app_bytes_delivered += got.sum() * specs["voip"].pkt_bytes

        # telemetry — delivery ratio + arrival times (telemetry-gap metric)
        k, links = npkts["telemetry"]
        log.telem_sent += k
        if k:
            got, dly = copies(k, links, "critical")
            got &= dly < LATE_MS
            offs = (np.arange(k) + 0.5) * DT / k
            for j in np.flatnonzero(got):
                log.telem_arrivals.append(now + offs[j] + dly[j] / 1000)
            log.telem_delivered += int(got.sum())
            log.app_bytes_delivered += got.sum() * specs["telemetry"].pkt_bytes

        # video — delivered bytes + delay feed the playout model
        k, links = npkts["video"]
        if k:
            got, dly = copies(k, links, "interactive")
            log.video_bytes[i] = got.sum() * specs["video"].pkt_bytes
            if got.any():
                log.video_delay_ms[i] = float(np.mean(dly[got]))
            log.app_bytes_delivered += log.video_bytes[i]

        # bulk — fluid goodput with dedup when duplicated
        if bulk_links:
            per_link = []
            for l in bulk_links:
                lt = rt.links[l]
                add = arrivals[l]["bulk"]
                per_link.append(add * served[l]["bulk"] * (1 - lt.loss[i])
                                * lt.avail[i])
            good = max(per_link) if len(bulk_links) > 1 else sum(per_link)
            log.bulk_bytes[i] = good
            log.app_bytes_delivered += good

        # end-of-tick snapshots: cumulative cost counters for the dashboard
        for k, tech in enumerate(TECHS):
            log.sent_cum[i, k] = log.sent_bytes[tech]
        log.useful_cum[i] = log.app_bytes_delivered

    return log
