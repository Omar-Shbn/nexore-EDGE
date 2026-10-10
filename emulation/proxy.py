"""Aggregation proxy (plan §5 component 6, §8): de-duplicates by
(flow_id, seq) with a sliding bitmap, echoes probes, and logs EVERY arrival
with link and timestamp — that log is the ground truth for all metrics.

Runs in the px namespace:  python3 proxy.py --log run1/recv.csv
"""
from __future__ import annotations

import argparse
import select
import socket
import struct
import time

HDR = struct.Struct("!HIQ")          # flow_id u16, seq u32, t_send_ms u64
LINK_ADDR = {"wifi": "10.77.1.2", "5g": "10.77.2.2", "leo": "10.77.3.2"}
PORT = 9000
WIN = 4096                           # dedup window (plan §8)


class Dedup:
    def __init__(self):
        self.seen = {}               # flow -> set of recent seqs (bounded)
        self.head = {}

    def first_copy(self, flow: int, seq: int) -> bool:
        s = self.seen.setdefault(flow, set())
        h = self.head.get(flow, 0)
        if seq in s:
            return False
        s.add(seq)
        if seq > h:
            self.head[flow] = seq
            # drop everything that fell out of the window
            if len(s) > 2 * WIN:
                cut = seq - WIN
                self.seen[flow] = {x for x in s if x >= cut}
        return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", required=True)
    args = ap.parse_args()

    socks = {}
    for link, addr in LINK_ADDR.items():
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind((addr, PORT))
        s.setblocking(False)
        socks[s] = link

    dd = Dedup()
    log = open(args.log, "w", buffering=1 << 16)
    log.write("t_recv,link,flow,seq,t_send_ms,first,bytes\n")
    print("proxy listening", flush=True)
    last_flush = time.monotonic()
    wd = time.monotonic()
    try:
        while True:
            r, _, _ = select.select(list(socks), [], [], 1.0)
            now = time.monotonic()
            wnow = time.monotonic()
            if wnow - wd > 1.4:      # select timeout is 1.0: flag extra stall
                print(f"WATCHDOG proxy stall {wnow - wd:.2f}s", flush=True)
            wd = wnow
            for s in r:
                link = socks[s]
                while True:
                    try:
                        data, peer = s.recvfrom(4096)
                    except BlockingIOError:
                        break
                    if len(data) < HDR.size:
                        continue
                    flow, seq, t_ms = HDR.unpack_from(data)
                    if flow == 0:            # probe: echo straight back
                        s.sendto(data, peer)
                        continue
                    first = dd.first_copy(flow, seq)
                    log.write(f"{now:.4f},{link},{flow},{seq},{t_ms},"
                              f"{int(first)},{len(data)}\n")
            if now - last_flush > 2:
                log.flush()
                last_flush = now
    except KeyboardInterrupt:
        pass
    finally:
        log.flush()


if __name__ == "__main__":
    main()
