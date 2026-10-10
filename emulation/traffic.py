"""Traffic generators (plan §2 classes): plain UDP into the tunnel's local
ports. One process runs all four flows.

  python3 traffic.py --duration 300
"""
from __future__ import annotations

import argparse
import socket
import time

FLOWS = {
    # port, packets/s, payload bytes
    "voip": (7101, 50, 186),      # G.711-like, 14B header -> 200B on wire
    "telemetry": (7102, 10, 86),
    "video": (7103, 250, 1236),   # ~2.5 Mbps
    "bulk": (7104, 450, 1386),    # ~5 Mbps offered, TCP-ish backlogged stand-in
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration", type=float, default=300.0)
    args = ap.parse_args()
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    state = {}
    for name, (port, pps, size) in FLOWS.items():
        state[name] = {"port": port, "ival": 1.0 / pps, "size": size,
                       "next": time.time()}
    t_end = time.time() + args.duration
    payloads = {n: bytes(st["size"]) for n, st in state.items()}
    while time.time() < t_end:
        now = time.time()
        nxt = t_end
        for name, st in state.items():
            if now >= st["next"]:
                s.sendto(payloads[name], ("127.0.0.1", st["port"]))
                st["next"] += st["ival"]
            nxt = min(nxt, st["next"])
        dt = nxt - time.time()
        if dt > 0:
            time.sleep(min(dt, 0.005))


if __name__ == "__main__":
    main()
