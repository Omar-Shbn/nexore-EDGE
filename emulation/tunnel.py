"""Tunnel data plane + monitor + policy (plan §5 components 1-5, §8).

Runs in the gw namespace. Apps send plain UDP to local ports; the tunnel
adds the 14-byte header (flow_id u16, seq u32, t_send_ms u64) and puts each
packet on its assigned link socket(s): one link = steer, two = duplicate,
bulk rate-capped when deferred.

Monitor: radio feed from /tmp/edge_radio.json (what the drivers report —
the replayer writes only the CURRENT instant) + its own 10 Hz probes.
Desired attach set goes to /tmp/edge_attach.json; the replayer charges
t_attach before the link becomes usable.

Modes mirror the simulator policies: b0, b1, b2, p (p uses the explainable
slope forecaster; the learned predictor is the simulator layer's job).

  python3 tunnel.py --mode p --log run1/sent.csv --duration 300
"""
from __future__ import annotations

import argparse
import json
import socket
import select
import struct
import time
from collections import deque

HDR = struct.Struct("!HIQ")
LINKS = ("wifi", "5g", "leo")
GW_ADDR = {"wifi": "10.77.1.1", "5g": "10.77.2.1", "leo": "10.77.3.1"}
PX_ADDR = {"wifi": "10.77.1.2", "5g": "10.77.2.2", "leo": "10.77.3.2"}
PORT = 9000
APP_PORTS = {1: 7101, 2: 7102, 3: 7103, 4: 7104}   # voip, telem, video, bulk
CLS = {1: "critical", 2: "critical", 3: "interactive", 4: "bulk"}
RADIO_F = "/tmp/edge_radio.json"
ATTACH_F = "/tmp/edge_attach.json"

# shared constants (kept in sync with edgesim.policy / scenario)
Q_BOUNDS = {"wifi": (-88.0, -55.0), "5g": (-118.0, -85.0), "leo": (2.0, 12.0)}
COST = {"wifi": 0.0, "5g": 0.01, "leo": 1.0}
BASE_RTT = {"wifi": 12.0, "5g": 28.0, "leo": 45.0}
FLOOR = {"wifi": -88.0, "5g": -118.0, "leo": 2.0}
T_GUARD, T_HOLD, T_DWELL, T_DETECT = 4.0, 1.0, 5.0, 1.0
Q_LOW, Q_OK, T_MAX = 0.25, 0.40, 10.0
BULK_DEFER_BPS = 250_000 // 8        # 10% of a 2.5 Mbps bulk flow


class LinkMon:
    def __init__(self, name):
        self.name = name
        self.sig = deque(maxlen=30)
        self.ok = deque(maxlen=30)
        self.rtt = deque(maxlen=30)
        self.visible = False
        self.state = "detached"

    def q(self):
        if not self.visible or not self.sig:
            return 0.0
        lo, hi = Q_BOUNDS[self.name]
        qs = min(1.0, max(0.0, (self.sig[-1] - lo) / (hi - lo)))
        loss = 1.0 - (sum(self.ok) / len(self.ok)) if self.ok else 0.0
        return qs * (1.0 - min(1.0, 2.0 * loss))

    def loss1(self):
        xs = list(self.ok)[-10:]
        return 1.0 - (sum(xs) / len(xs)) if xs else 0.0

    def slope3(self):
        xs = [v for v in self.sig]
        n = len(xs)
        if n < 10:
            return 0.0
        tm = (n - 1) / 2
        num = sum((i - tm) * (x - sum(xs) / n) for i, x in enumerate(xs))
        den = sum((i - tm) ** 2 for i in range(n)) * 0.1
        return num / den if den else 0.0

    def t_life(self):
        """Explainable slope forecaster (plan §6 linear baseline)."""
        if not self.visible or not self.sig:
            return 0.0
        s = self.slope3()
        head = self.sig[-1] - FLOOR[self.name]
        t = T_MAX if s >= -1e-3 else min(T_MAX, max(0.0, head / -s))
        if self.state == "attached" and self.loss1() > 0.3:
            t = min(t, 1.0)
        return t


class Policy:
    def __init__(self, mode):
        self.mode = mode
        self.active = "wifi"
        self.partner = None
        self.bad = 0.0
        self.dead = 0.0
        self.stable = 0.0
        self.last_trig = -1e9

    def tick(self, now, mons):
        m = {L: mons[L] for L in LINKS}
        act = m[self.active]
        if self.mode == "b0":
            self.bad = self.bad + 0.1 if act.q() < Q_LOW else 0.0
            if self.bad >= 1.0:
                alt = max((L for L in LINKS if L != self.active),
                          key=lambda L: m[L].q())
                if m[alt].q() > Q_OK or act.q() <= 0:
                    self.active, self.bad = alt, 0.0
            return {self.active}, {f: [self.active] for f in (1, 2, 3, 4)}, False
        if self.mode == "b1":
            failing = not act.visible or act.loss1() >= 0.9
            self.dead = self.dead + 0.1 if failing else 0.0
            live = [L for L in LINKS if m[L].visible and m[L].loss1() < 0.9]
            if (self.dead >= T_DETECT or self.active not in live) and live:
                self.active = min(live, key=lambda L: (sum(m[L].rtt) / len(m[L].rtt))
                                  if m[L].rtt else BASE_RTT[L])
                self.dead = 0.0
            return set(LINKS), {f: [self.active] for f in (1, 2, 3, 4)}, False
        if self.mode == "b2":
            vis = [L for L in LINKS if m[L].visible] or ["leo"]
            pair = sorted(vis, key=lambda L: m[L].q(), reverse=True)[:2]
            self.active = pair[0]
            return set(LINKS), {f: pair for f in (1, 2, 3, 4)}, False
        # ---- mode p ----------------------------------------------------------
        danger = act.t_life() < T_GUARD
        if self.partner is None and danger and now - self.last_trig > T_DWELL:
            cands = [L for L in LINKS if L != self.active and m[L].visible
                     and m[L].t_life() > T_GUARD + T_HOLD]
            if cands:
                best_q = max(m[L].q() for L in cands)
                ok = [L for L in cands if m[L].q() >= best_q - 0.10]
                self.partner = min(ok, key=lambda L: COST[L])
                self.stable = 0.0
                self.last_trig = now
        if self.partner:
            p = m[self.partner]
            good = p.state == "attached" and p.loss1() < 0.1
            self.stable = self.stable + 0.1 if good else 0.0
            if self.stable >= T_HOLD and (not act.visible
                                          or act.loss1() >= 0.5
                                          or p.q() > act.q() + 0.10):
                self.active, self.partner = self.partner, None
            elif act.t_life() > T_GUARD + 1.0 and self.stable >= T_HOLD:
                self.partner = None
        if not act.visible or act.loss1() >= 0.98:      # hard fallback
            alts = [L for L in LINKS if L != self.active and m[L].visible]
            if alts and self.partner is None:
                self.active = max(alts, key=lambda L: m[L].q())
        attach = {"wifi", "5g"}
        if "leo" in (self.active, self.partner):
            attach.add("leo")
        if self.partner:
            A, P = self.active, self.partner
            ready = m[P].state == "attached" and m[P].loss1() < 0.5
            return attach, {1: [A, P], 2: [A, P],
                            3: [P] if ready else [A], 4: [A]}, True
        return attach, {f: [self.active] for f in (1, 2, 3, 4)}, False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("b0", "b1", "b2", "p"), required=True)
    ap.add_argument("--log", required=True)
    ap.add_argument("--duration", type=float, default=300.0)
    args = ap.parse_args()

    link_socks = {}
    for L in LINKS:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind((GW_ADDR[L], 0))
        s.connect((PX_ADDR[L], PORT))
        s.setblocking(False)
        link_socks[L] = s
    app_socks = {}
    for flow, port in APP_PORTS.items():
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind(("127.0.0.1", port))
        s.setblocking(False)
        app_socks[s] = flow

    mons = {L: LinkMon(L) for L in LINKS}
    pol = Policy(args.mode)
    seq = {f: 0 for f in (0, 1, 2, 3, 4)}
    probe_sent = {}                       # (link, seq) -> t
    probe_last = {L: None for L in LINKS}  # last probe seq per link
    probe_echoed = set()                   # seqs whose echo came back
    assign = {f: ["wifi"] for f in (1, 2, 3, 4)}
    bulk_defer = False
    bulk_budget = 0.0
    log = open(args.log, "w", buffering=1 << 16)
    log.write("t,link,flow,pkts,bytes,dup\n")
    counts = {}

    t0 = time.monotonic()
    next_tick = t0
    print(f"tunnel up, mode={args.mode}", flush=True)
    wd = time.monotonic()
    while time.monotonic() - t0 < args.duration:
        now = time.monotonic()
        wnow = time.monotonic()
        if wnow - wd > 0.3:          # watchdog: the loop itself stalled
            print(f"WATCHDOG tunnel stall {wnow - wd:.2f}s "
                  f"at t={now - t0:.1f}", flush=True)
        wd = wnow
        if now >= next_tick:                       # ---- 100 ms control tick
            next_tick += 0.1
            try:
                radio = json.load(open(RADIO_F))["links"]
            except Exception:
                radio = {}
            for L in LINKS:
                r = radio.get(L, {})
                mons[L].visible = bool(r.get("visible"))
                mons[L].state = r.get("state", "detached")
                if mons[L].visible:
                    mons[L].sig.append(float(r.get("signal", -999)))
            attach, assign, bulk_defer = pol.tick(now - t0, mons)
            tmp = ATTACH_F + ".tmp"
            json.dump(sorted(attach), open(tmp, "w"))
            import os
            os.replace(tmp, ATTACH_F)
            bulk_budget = BULK_DEFER_BPS * 0.1 if bulk_defer else 1e9
            for L in LINKS:                        # 10 Hz probes
                # settle LAST tick's probe first (echoes can arrive in the
                # next tick on slow links — never flip the wrong slot)
                if probe_last[L] is not None:
                    mons[L].ok.append(
                        1 if probe_last[L] in probe_echoed else 0)
                    probe_echoed.discard(probe_last[L])
                    probe_sent.pop((L, probe_last[L]), None)  # drop if lost
                    probe_last[L] = None
                if mons[L].state != "attached":
                    continue
                seq[0] += 1
                pkt = HDR.pack(0, seq[0], int(now * 1000)) + L.encode()
                try:
                    link_socks[L].send(pkt)
                    probe_sent[(L, seq[0])] = now
                    probe_last[L] = seq[0]
                except OSError:
                    pass
            # flush per-tick send counters
            for (L, f, dup), (n, b) in counts.items():
                log.write(f"{now - t0:.1f},{L},{f},{n},{b},{dup}\n")
            counts = {}

        rs = select.select(list(app_socks) + list(link_socks.values()),
                           [], [], 0.02)[0]
        for s in rs:
            if s in app_socks:                     # app packet -> link(s)
                flow = app_socks[s]
                while True:
                    try:
                        data, _ = s.recvfrom(4096)
                    except BlockingIOError:
                        break
                    if flow == 4 and bulk_defer:
                        if bulk_budget < len(data):
                            continue               # throttled away
                        bulk_budget -= len(data)
                    seq[flow] += 1
                    pkt = HDR.pack(flow, seq[flow], int(time.monotonic() * 1000)) + data
                    paths = assign.get(flow, [pol.active])
                    for di, L in enumerate(paths):
                        if mons[L].state != "attached":
                            continue
                        try:
                            link_socks[L].send(pkt)
                        except OSError:
                            continue
                        k = (L, flow, di)
                        n, b = counts.get(k, (0, 0))
                        counts[k] = (n + 1, b + len(pkt))
            else:                                   # probe echo
                while True:
                    try:
                        data = s.recv(4096)
                    except (BlockingIOError, OSError):
                        break
                    if len(data) < HDR.size:
                        continue
                    flow, sq, t_ms = HDR.unpack_from(data)
                    if flow != 0:
                        continue
                    L = data[HDR.size:].decode(errors="ignore")
                    if (L, sq) in probe_sent:
                        rtt = (time.monotonic() - probe_sent.pop((L, sq))) * 1000
                        probe_echoed.add(sq)
                        mons[L].rtt.append(rtt)
    # final counter flush — otherwise the last tick's sends are lost and
    # delivered can exceed "sent"
    now = time.monotonic()
    for (L, f, dup), (n, b) in counts.items():
        log.write(f"{now - t0:.1f},{L},{f},{n},{b},{dup}\n")
    log.flush()
    print("tunnel done", flush=True)


if __name__ == "__main__":
    main()
