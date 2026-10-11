# Simulation vs emulation (same trace, 90 km/h outbound, seed 1)

Emulation replays the trace 2:1 time-compressed in real Linux namespaces (veth + netem, userspace tunnel, dedup proxy); the simulator runs it tick-by-tick. Rates are per minute of journey; the claim is ordinal agreement, not decimal agreement.

| Policy | layer | interruption s/min | voice loss % | overhead % | satellite MB/min |
|---|---|---|---|---|---|
| B0 | simulation | 1.58 | 3.3 | 0.6 | 42.5 |
| B0 | emulation | 2.15 | 1.5 | 0.0 | 10.1 |
| B1 | simulation | 0.68 | 2.0 | 0.8 | 45.8 |
| B1 | emulation | 0.75 | 2.3 | 0.0 | 11.4 |
| B2 | simulation | 0.67 | 1.5 | 51.3 | 208.0 |
| B2 | emulation | 0.67 | 1.8 | 72.2 | 50.9 |
| P | simulation | 0.97 | 2.2 | 0.7 | 48.7 |
| P | emulation | 1.23 | 3.0 | 0.0 | 15.6 |

## How to read this

- **Ordinal agreement is the claim.** B0 is clearly worst on silence, B1 and B2 are best, P sits between them, and only B2 pays a large duplication overhead. Both layers agree.
- **This is a journey where P is weak.** 90 km/h outbound, seed 1 is the journey on which P trails B1/B2 in the simulation too (cost-aware LEO attach latency on a sharp 5G collapse). The emulation reproducing that is the point: the two layers measure the same mechanism, including its weakness. Fleet means (160 journeys, simulation) are the headline, not this single trace.
- **Units differ.** The emulation replays the trace 2:1 time-compressed while protocol timers (attach, grace) stay in real seconds, so its silence per wall-minute is a harsher, not directly comparable, number.
- **Satellite MB differ in absolute terms** because the simulator's bulk flow is a backlogged fluid filling capacity while the emulation's bulk is a fixed ~5 Mbps UDP stream; compare orderings (B2 far above the rest), not magnitudes.
- **Overhead is defined differently.** Simulation: all wire bytes (duplicates, probes, keepalive) over useful bytes minus 1. Emulation: duplicate-copy arrivals over unique arrivals.
- Emulation modes use the explainable slope forecaster for P (plan §6 linear baseline); the learned predictor is the simulation layer's.
