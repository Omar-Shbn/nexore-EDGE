#!/usr/bin/env bash
# All four modes over one trace (plan §8). Run inside WSL/Linux as root:
#   sudo bash emulation/run_all_modes.sh emulation/trace_v90_out_s1.csv 245 /tmp/emu
set -uo pipefail
TRACE=${1:?trace csv}
DUR=${2:-245}
OUTBASE=${3:-/tmp/emu}
HERE=$(cd "$(dirname "$0")" && pwd)
for m in b0 b1 b2 p; do
  echo "===== MODE $m ====="
  timeout $((DUR + 60)) bash "$HERE/run_emulation.sh" "$TRACE" "$m" \
      "${OUTBASE}_${m}" "$DUR" 2>&1 | tail -60
done
