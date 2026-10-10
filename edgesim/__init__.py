"""edgesim — trace-driven simulator for predictive, class-aware selective redundancy.

ATP 2026, EDGE Group track. Implements the v2 plan: a multi-link vehicle
gateway that forecasts future link state and, only inside the predicted
failure window, duplicates critical traffic across two links, pre-switches
video, and defers bulk.

Layers (plan §5):
  traces     — synthetic route traces + adapter for the Raca 4G/5G datasets
  simulator  — ground-truth links, queues, packet accounting (the "emulator")
  monitor    — the ONLY view the decision stack gets (observability rule §5.4)
  predictor  — forecast of future link state from monitor features
  policy     — B0 / B1 / B2 baselines, proposal P, and ablations
  metrics    — interruption, loss, E-model MOS, stalls, overhead, satellite bytes
  model      — analytical interruption model that yields T_guard
  experiment — speed x direction x seed grid, CIs, result tables
"""

__version__ = "0.1.0"
