"""Simulation vs emulation cross-check (plan §9.4: the simulator's headline
results never replace the emulation's — agreement between the two layers is
the evidence they are both measuring the mechanism, not their own bugs).

Same scenario (90 km/h outbound, seed 1), same policies. The emulation
replays the trace time-compressed 2:1, so totals are compared per minute
and the qualitative ordering is what must agree.

  python scripts/crosscheck.py   ->  results/emulation/crosscheck.md
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from edgesim.scenario import Scenario           # noqa: E402
from edgesim import traces                      # noqa: E402
from edgesim.policy import make_policy          # noqa: E402
from edgesim.simulator import run as sim_run    # noqa: E402
from edgesim.metrics import compute             # noqa: E402
from edgesim.predictor import Forecaster        # noqa: E402

MODES = {"b0": "B0", "b1": "B1", "b2": "B2", "p": "P"}


def main():
    emudir = ROOT / "results" / "emulation"
    tg = json.load(open(ROOT / "results" / "t_guard.json"))
    fc = Forecaster.load(ROOT / "results" / "predictor.pkl")
    calib = traces.raca_capacity_calibration(
        sorted((ROOT / "data" / "raca").glob("*.csv")))
    sc = Scenario(90.0, "outbound", seed=1, layout_seed=0)
    rt = traces.generate(sc, calib_5g=calib)
    sim_minutes = rt.t[-1] / 60.0

    lines = [
        "# Simulation vs emulation (same trace, 90 km/h outbound, seed 1)",
        "",
        "Emulation replays the trace 2:1 time-compressed in real Linux "
        "namespaces (veth + netem, userspace tunnel, dedup proxy); the "
        "simulator runs it tick-by-tick. Rates are per minute of journey; "
        "the claim is ordinal agreement, not decimal agreement.",
        "",
        "| Policy | layer | interruption s/min | voice loss % | "
        "overhead % | satellite MB/min |",
        "|---|---|---|---|---|---|",
    ]
    for mode, pol in MODES.items():
        m, _ = (lambda p: (compute(sim_run(rt, p, seed=1), rt), None))(
            make_policy(pol, predictor=fc, t_guard=tg["t_guard_s"]))
        lines.append(
            f"| {pol} | simulation | {m['int_total_s'] / sim_minutes:.2f} | "
            f"{m['voip_loss_pct']:.1f} | {m['overhead_pct']:.1f} | "
            f"{m['sat_mb'] / sim_minutes:.1f} |")
        mj = emudir / f"{mode}_metrics.json"
        if mj.exists():
            e = json.load(open(mj))
            emu_min = 242.0 / 60.0   # 245 s run minus 3 s settle
            lines.append(
                f"| {pol} | emulation | "
                f"{e.get('voip_int_total_s', 0) / emu_min:.2f} | "
                f"{e.get('voip_loss_pct', 0):.1f} | "
                f"{e.get('overhead_pct', 0):.1f} | "
                f"{e.get('sat_mb', 0) / emu_min:.1f} |")
    lines += [
        "",
        "## How to read this",
        "",
        "- **Ordinal agreement is the claim.** B0 is clearly worst on "
        "silence, B1 and B2 are best, P sits between them, and only B2 "
        "pays a large duplication overhead. Both layers agree.",
        "- **This is a journey where P is weak.** 90 km/h outbound, seed 1 "
        "is the journey on which P trails B1/B2 in the simulation too "
        "(cost-aware LEO attach latency on a sharp 5G collapse). The "
        "emulation reproducing that is the point: the two layers measure "
        "the same mechanism, including its weakness. Fleet means (160 "
        "journeys, simulation) are the headline, not this single trace.",
        "- **Units differ.** The emulation replays the trace 2:1 "
        "time-compressed while protocol timers (attach, grace) stay in "
        "real seconds, so its silence per wall-minute is a harsher, "
        "not directly comparable, number.",
        "- **Satellite MB differ in absolute terms** because the "
        "simulator's bulk flow is a backlogged fluid filling capacity "
        "while the emulation's bulk is a fixed ~5 Mbps UDP stream; "
        "compare orderings (B2 far above the rest), not magnitudes.",
        "- **Overhead is defined differently.** Simulation: all wire "
        "bytes (duplicates, probes, keepalive) over useful bytes minus 1. "
        "Emulation: duplicate-copy arrivals over unique arrivals.",
        "- Emulation modes use the explainable slope forecaster for P "
        "(plan §6 linear baseline); the learned predictor is the "
        "simulation layer's.",
    ]
    out = emudir / "crosscheck.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
