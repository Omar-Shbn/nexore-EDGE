"""Trace replayer (plan §8): updates each link's netem every 100 ms and
emulates attachment. Runs as root in the ROOT namespace (tc into gw/px ns).

The trace is visible to this process only — components 1-4 never see it.
Two small files bridge the namespaces (same filesystem):
  /tmp/edge_radio.json   <- written here: CURRENT radio sample per link
                            (what a driver would report right now)
  /tmp/edge_attach.json  -> written by the tunnel: desired attach set
A detached link is 100% loss; attach(L) becomes usable only after
t_attach[L] (plan §8).
"""
from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import time
from collections import defaultdict

T_ATTACH = {"wifi": 0.3, "5g": 0.15, "leo": 2.5}   # plan §8
# A session rides out a radio loss shorter than this (a 2 s satellite
# obstruction is not a detach). Mirrors edgesim.scenario.TECH[...].grace_s so
# the simulator and the emulation share one link-state model. These are
# protocol timers: real seconds, deliberately NOT compressed with the trace.
GRACE = {"wifi": 2.0, "5g": 5.0, "leo": 30.0}
RADIO_F = "/tmp/edge_radio.json"
ATTACH_F = "/tmp/edge_attach.json"
DEV = {"wifi": ("vwifig", "vwifip"), "5g": ("v5gg", "v5gp"),
       "leo": ("vleog", "vleop")}


_last = {}


def tc(ns: str, dev: str, rate: float, delay: float, loss: float):
    """netem update with change suppression (skip ~identical settings)."""
    key = (ns, dev)
    cur = (round(max(rate, 0.01), 1), round(max(delay, 0.1)),
           round(min(max(loss, 0.0), 100.0), 1))
    if _last.get(key) == cur:
        return
    _last[key] = cur
    cmd = ["ip", "netns", "exec", ns, "tc", "qdisc", "change", "dev", dev,
           "root", "netem", "rate", f"{cur[0]}mbit",
           "delay", f"{cur[1]}ms", "loss", f"{cur[2]}%"]
    subprocess.run(cmd, check=False, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL)


def main(trace_csv: str):
    rows = defaultdict(dict)   # t -> link -> row
    with open(trace_csv) as f:
        for r in csv.DictReader(f):
            rows[float(r["t"])][r["link"]] = r
    ts = sorted(rows)
    print(f"replaying {len(ts)} ticks ({ts[-1]:.0f}s)", flush=True)

    state = {L: "detached" for L in DEV}     # detached|attaching|attached
    ready = {L: 0.0 for L in DEV}
    lost_since = {L: None for L in DEV}      # radio-loss grace timers
    t0 = time.monotonic()
    for t in ts:
        # pace to wall clock
        lag = t - (time.monotonic() - t0)
        if lag > 0:
            time.sleep(lag)
        # desired attach set from the tunnel
        want = set()
        try:
            want = set(json.load(open(ATTACH_F)))
        except Exception:
            pass
        radio = {}
        for L, row in rows[t].items():
            avail = row["avail"] == "1"
            # attach state machine (plan §8), same rules as the simulator's
            # Gateway.step_attach: attach needs the radio; a session survives
            # a short loss; an unwanted link is released at once.
            if L not in want:
                state[L], lost_since[L] = "detached", None
            elif state[L] == "detached":
                if avail:
                    state[L] = "attaching"
                    ready[L] = t + T_ATTACH[L]
            elif state[L] == "attaching":
                if t >= ready[L]:
                    if avail:
                        state[L] = "attached"
                    else:                      # keep retrying until radio is there
                        ready[L] = t + T_ATTACH[L]
            else:                              # attached
                if avail:
                    lost_since[L] = None
                elif lost_since[L] is None:
                    lost_since[L] = t
                elif t - lost_since[L] > GRACE[L]:
                    state[L], lost_since[L] = "detached", None
            usable = state[L] == "attached" and avail
            loss = float(row["loss_pct"]) if usable else 100.0
            for ns, dev in zip(("gw", "px"), DEV[L]):
                tc(ns, dev, float(row["rate_mbps"]),
                   float(row["delay_ms"]), loss)
            radio[L] = {"visible": avail, "signal": float(row["signal"]),
                        "state": state[L]}
        tmp = RADIO_F + ".tmp"
        json.dump({"t": t, "links": radio}, open(tmp, "w"))
        os.replace(tmp, RADIO_F)
    print("replay finished", flush=True)


if __name__ == "__main__":
    main(sys.argv[1])
