# Raca 5G production dataset (driving subset)

These CSVs are a subset of the public **5G production network dataset**
from the UCC MISL group (University College Cork), used here to calibrate
the synthetic 5G RSRP -> capacity mapping (`edgesim/traces.py`,
`raca_capacity_calibration`).

- Upstream: https://github.com/uccmisl/5Gdataset
- Subset used: `5G-production-dataset/Download/Driving/*.csv` (16 drive traces)
- Cite: D. Raca, D. Leahy, C. J. Sreenan, J. J. Quinlan, "Beyond throughput,
  the next generation: a 5G dataset with channel and context metrics",
  ACM MMSys 2020.
- License: **GNU GPL v3** (see `LICENSE` in this folder, copied verbatim from
  upstream). The files are redistributed unmodified; they are an aggregate
  with the rest of this repository, which they do not make a derivative of.

Throughput samples are demand/app-limited, so the calibration uses the p90 of
the DL bitrate per RSRP bin as the achievable-capacity proxy (README,
"Honest limitations").
