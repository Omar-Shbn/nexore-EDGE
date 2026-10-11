# Predictive, Class-Aware Selective Redundancy

ATP 2026 · EDGE Group track · prototype for **Solution v2** (see
`fullfile.md`, the governing plan).

A multi-link vehicle gateway that forecasts when its current network is
about to fail and, **for that window only**, duplicates critical traffic
across two links, pre-switches video, and defers bulk. Near-zero
interruption for voice and control at a fraction of always-on redundancy's
cost. *Bulk funds duplication.*

## Layout

```
edgesim/            simulation layer (plan §9.4) — policies, predictor, metrics
  scenario.py         route legs, technology constants, traffic classes (§2)
  datamodels.py       frozen interfaces: LinkState, Prediction, … (§5.2)
  traces.py           synthetic route traces + Raca 4G/5G adapter (§9.4)
  simulator.py        ground-truth data plane: queues, loss, attach (§8 analogue)
  monitor.py          the ONLY observable view — observability rule (§9.6)
  predictor.py        T_life forecaster: linear + GBR, winner ships (§6)
  policy.py           B0 / B1 / B2 / P + ablations, utility function (§7, §9.1)
  metrics.py          interruption, E-model MOS, stalls, overhead… (§9.2)
  model.py            analytical E[I] model -> T_guard (§9.3)
  experiment.py       speed x direction x seed grid, CIs, sweeps (§9.6)
  plots.py            all deck figures, generated from logs only (§9.5)
emulation/          emulation layer (plan §8) — Linux namespaces, WSL-ready
  netns_setup.sh      gw/px namespaces, one veth + netem per technology
  export_trace.py     RouteTrace -> replayer CSV
  replay.py           netem updates every 100 ms + attach-time emulation
  tunnel.py           userspace data plane: 14-byte header, steer/dup/throttle
  proxy.py            dedup by (flow_id, seq), logs every arrival (ground truth)
  traffic.py          voice / telemetry / video / bulk generators
  metrics_from_log.py §9.2 metrics from one run's logs
scripts/run_all.py  one-command reproduction (§9.6)
scripts/make_dashboard.py  builds results/dashboard.html (replay frontend)
scripts/make_tables.py     markdown result tables for the deck
scripts/make_demo.py       animated GIF of the B0-vs-P replay
scripts/crosscheck.py      simulation-vs-emulation table
dashboard/template.html    the replay page (data injected at build)
data/raca/          Raca et al. 5G production dataset CSVs (driving, download)
results/            everything generated: csv, figures/, predictor.pkl
docs/               guidelines (deliverable 5), deck notes, AI declaration
```

**Results:** [docs/RESULTS.md](docs/RESULTS.md) (figures with explanations) and the interactive [dashboard](docs/index.html).

## Reproduce everything

```bash
pip install -r requirements.txt
python scripts/run_all.py            # full grid, ~30-60 min on 8 cores
python scripts/run_all.py --quick    # smoke run
```

Stages: Raca calibration → predictor training (disjoint routes) →
T_guard from the analytical model → main grid (4 speeds × 2 directions ×
20 seeds × 8 policies) → jamming stress → T_guard + robustness sweeps →
summary tables → figures. Every run is seeded; every figure comes from
`results/*.csv` by script.

### Replay dashboard

`results/dashboard.html` is a single self-contained file (no server, no
network): open it by double-click. It replays 8 logged journeys (30/60/90/
120 km/h x outbound/return, seed 1) under B0/B1/B2/P: link lanes with
protection windows and audible outages, live voice delivery / MOS / video
stalls / overhead / satellite counters, the forecast-life gauge, an event
feed, and a table putting each journey beside the 160-journey fleet means.
Keys: Space play/pause, arrows +-5 s, Home restart; drag on a lane to scrub.
Deep links: `dashboard.html#s=90&d=outbound&vs=B2&t=376&theme=dark&play=1`.
Rebuild with `python scripts/make_dashboard.py` (~35 s).

### Emulation runs (WSL/Linux, root)

```bash
python emulation/export_trace.py --speed 90 --direction outbound --seed 1 \
    --out /tmp/trace.csv
sudo bash emulation/run_emulation.sh /tmp/trace.csv p runs/p_s1 180
```

Modes: `b0 b1 b2 p`. The proxy's receive log is the ground truth; metrics
print as JSON. The trace file is read by the replayer only — components
1–4 never see it (observability rule).

## The mechanism in one paragraph

Every 100 ms the monitor samples each link (radio metrics it could really
read, plus its own probe RTT/loss). The predictor turns that into
`t_life_hat` — seconds until the link becomes *unusable* (loss ≥ 5% over
500 ms, RTT ≥ 300 ms, or down, sustained 300 ms). When the active link's
forecast drops below `T_guard`, the policy opens a protected transition:
critical flows ride **both** the active link and a partner chosen by
utility-per-cost (satellite last), video pre-switches once the partner
probes clean, bulk is throttled to 10%. The window closes when the partner
has been stable for `T_hold` (completion) or the forecast recovers (false
alarm). `T_guard = T_ho − μ + z·σ` is **derived** from the handover latency
and the predictor's held-out error, never hand-tuned.

## Baselines and ablations (§9.1)

| ID | Mechanism |
|----|-----------|
| B0 | single link, signal threshold + hysteresis, break-before-make |
| B1 | minRTT multipath, failover after 1 s detected loss |
| B2 | duplicate everything on the two best links |
| P  | ours |
| P-nopred / P-noclass / P-nocost | remove prediction / class-awareness / cost-awareness |
| P-oracle | perfect predictor — the ceiling prediction could ever buy |

## Documented deviations from plan v2

1. **Economic windows.** §7 opens a protected transition only on predicted
   failure; riding satellite home after 5G reappears would burn money, so
   the same window machinery also triggers toward a clearly better AND
   cheaper link (utility margin + dwell apply; ablation P-nocost removes
   it). This is ATSSS-style steering expressed through §7's own states.
2. **Session grace.** A radio loss shorter than a per-technology grace
   period (Wi-Fi 2 s, 5G 5 s, LEO 30 s) is not a detach: a 2-second
   satellite obstruction should not cost a 2.5 s re-attach afterwards.
3. **Avoidable vs raw interruption.** Both are reported. The route
   contains genuine coverage holes (satellite obstruction at the remote
   site with nothing else in the air); no gateway can serve them — B2
   empirically proves the floor — so the mechanism comparison headline
   uses the avoidable part and summary.csv keeps the raw totals.
4. **Terrain ridge at the fringe.** The 5G collapse at the C→D boundary is
   modelled as knife-edge diffraction (~28 dB over ~120 m) past the last
   cell, so the warning time scales with vehicle speed — the transition
   the mechanism exists for.

## Honest limitations

- The simulator's link models (log-distance Wi-Fi, Raca-calibrated 5G,
  Starlink-like LEO) are stated simplifications; the 5G capacity↔RSRP curve
  is fitted from the Raca *downlink* driving traces (p90 per RSRP bin) while
  the scenario measures uplink — a documented proxy.
- The predictor is trained on synthetic routes calibrated with cellular
  data and applied to Wi-Fi/LEO through normalised features.
- Bulk uses a fluid model (its congestion control is a rate-filling
  shortcut), and emulation-mode P uses the explainable slope forecaster.
- LEO attach time (2.5 s) is a placeholder pending a citable Starlink
  reacquisition measurement; attach times for Wi-Fi (300 ms) and 5G
  (150 ms) follow plan §8.

## References (verify before deck use — plan Q11)

1. Learning Wi-Fi Connection Loss Predictions for Seamless Vertical
   Handovers Using Multipath TCP, arXiv:1907.10493 (2019) — closest prior
   art; predicts *when* to prepare, not *what* to protect or at what cost.
2. 3GPP TS 23.501 (Rel-16+), ATSSS — standardised steering/switching/
   splitting; policy-driven, no predicted redundancy window.
3. RFC 8684 — Multipath TCP; the redundant-scheduler idea behind B2.
4. Raca et al., MMSys 2018 (4G LTE dataset) and MMSys 2020 (5G production
   dataset) — the drive traces in `data/raca/`.
5. Ahmed, Merghem-Boulahia, Gaïti — vertical-handover decision algorithms,
   IEEE Communications Surveys & Tutorials 2014.
6. ITU-T G.107 — the E-model behind the voice MOS metric (G.711, Bpl=25).

## AI use declaration (draft, §18)

We used AI assistants (Claude, ChatGPT, Manus) for literature search,
design review and drafting, and Claude Code for implementation support.
All design decisions, experiments and results were produced, checked and
are explained by the team.
