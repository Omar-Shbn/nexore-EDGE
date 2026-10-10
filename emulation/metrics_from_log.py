"""Compute plan §9.2 metrics from one emulation run's logs.

  python3 metrics_from_log.py runs/p_s1    # dir holding recv.csv + sent.csv
"""
from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict


SETTLE_S = 3.0   # boot window: attach + generators starting, excluded


def main(rundir: str):
    arr = defaultdict(list)          # flow -> first-copy arrival times
    delay = defaultdict(list)
    by_link_bytes = defaultdict(int)
    dup_bytes = 0
    total_bytes = 0
    t0 = None
    with open(f"{rundir}/recv.csv") as f:
        for r in csv.DictReader(f):
            t = float(r["t_recv"])
            if t0 is None:
                t0 = t
            if t - t0 < SETTLE_S:
                continue
            flow = int(r["flow"])
            b = int(r["bytes"])
            total_bytes += b
            by_link_bytes[r["link"]] += b
            if r["first"] == "1":
                arr[flow].append(t)
                delay[flow].append(t * 1000 - float(r["t_send_ms"]))
            else:
                dup_bytes += b
    sent = defaultdict(int)          # unique packets (first copy only)
    with open(f"{rundir}/sent.csv") as f:
        for r in csv.DictReader(f):
            if float(r["t"]) < SETTLE_S:
                continue
            if r["dup"] == "0":      # dup>0 are redundant copies
                sent[int(r["flow"])] += int(r["pkts"])

    out = {}
    for flow, name in ((1, "voip"), (2, "telemetry"), (3, "video")):
        ts = sorted(arr[flow])
        if len(ts) < 2:
            out[f"{name}_int_max_s"] = None
            continue
        gaps = [b - a for a, b in zip(ts, ts[1:])]
        out[f"{name}_int_max_s"] = round(max(gaps), 3)
        out[f"{name}_int_total_s"] = round(sum(g for g in gaps if g > 0.3), 3)
        out[f"{name}_delivered"] = len(ts)
        out[f"{name}_sent"] = sent.get(flow)
        if sent.get(flow):
            out[f"{name}_loss_pct"] = round(100 * (1 - len(ts) / sent[flow]), 2)
    useful = total_bytes - dup_bytes
    out["overhead_pct"] = round(100 * dup_bytes / max(useful, 1), 2)
    out["sat_mb"] = round(by_link_bytes.get("leo", 0) / 1e6, 2)
    print(json.dumps(out, indent=2))
    json.dump(out, open(f"{rundir}/metrics.json", "w"), indent=2)


if __name__ == "__main__":
    main(sys.argv[1])
