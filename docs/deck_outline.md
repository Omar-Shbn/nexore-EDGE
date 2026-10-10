# Deck outline — 10 pages (plan §17)

Each page makes one claim tied to a rubric criterion, backed by one figure.
Figures live in `results/figures/`; numbers in `results/summary.csv`.

| # | Page | Claim | Backing material |
|---|------|-------|------------------|
| 1 | Problem and why it matters | A command call drops exactly where coverage hands over | timeline strip from fig2 (B0 row) |
| 2 | Existing mechanisms | Today you either never duplicate (outages) or always duplicate (waste) | two-extremes diagram; ref 1 + ATSSS |
| 3 | Our mechanism | Predict, protect selectively, respect cost | timing diagram of the §7 window |
| 4 | Architecture | Six components; only the monitor's drivers change on real hardware | §5 flowchart |
| 5 | Prediction | T_life forecast: linear vs GBR, held-out winner ships | `predictor_stats.json` table |
| 6 | Method | Observability rule, disjoint traces, seeds, CIs, same code path for all policies | §9.6 bullet list |
| 7 | Headline results | P ≈ B2's continuity at a fraction of its cost | fig1 trade-off + fig3 MOS |
| 8 | Why it works | Each ingredient earns its keep; model matches simulation; degrades gracefully | fig5 ablations, fig4 model, fig6 robustness |
| 9 | Deployment and limitations | SD-WAN-style gateway + edge proxy; honest list of simplifications | §11 + README limitations |
| 10 | Guidelines + AI declaration | Ten transferable rules | docs/guidelines.md |

Video (~1:45, plan §10): problem 0:00–0:15 · idea + architecture 0:15–0:40 ·
side-by-side replay (B0 breaks, P doesn't, overhead counter) 0:40–1:25 ·
deployment 1:25–1:45. Screen-record `results/dashboard.html` at 10x (120 km/h outbound, B0 vs P; the B0 outage is at ~329 s) for the 0:40–1:25 segment; `results/demo.gif` is the lightweight fallback.
