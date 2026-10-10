#!/usr/bin/env bash
# One emulation run (plan §8). Inside WSL/Linux, repo root, as root:
#
#   sudo bash emulation/run_emulation.sh trace.csv p runs/p_s1 180
#
# args: trace_csv mode(b0|b1|b2|p) outdir duration_s
set -euo pipefail
TRACE=${1:?trace csv}
MODE=${2:?mode}
OUT=${3:?outdir}
DUR=${4:-180}
HERE=$(cd "$(dirname "$0")" && pwd)

mkdir -p "$OUT"
rm -f /tmp/edge_radio.json /tmp/edge_attach.json
echo '[]' > /tmp/edge_attach.json

bash "$HERE/netns_setup.sh" down >/dev/null 2>&1 || true
bash "$HERE/netns_setup.sh" up

cleanup() {
  kill "${PX_PID:-0}" "${TN_PID:-0}" "${TG_PID:-0}" "${RP_PID:-0}" 2>/dev/null || true
  bash "$HERE/netns_setup.sh" down >/dev/null 2>&1 || true
}
trap cleanup EXIT

ip netns exec px python3 "$HERE/proxy.py" --log "$OUT/recv.csv" &
PX_PID=$!
sleep 0.5
ip netns exec gw python3 "$HERE/tunnel.py" --mode "$MODE" \
    --log "$OUT/sent.csv" --duration "$DUR" &
TN_PID=$!
sleep 0.5
ip netns exec gw python3 "$HERE/traffic.py" --duration "$DUR" &
TG_PID=$!
python3 "$HERE/replay.py" "$TRACE" &
RP_PID=$!

wait "$TN_PID"
kill "$TG_PID" "$RP_PID" 2>/dev/null || true
sleep 0.5
kill "$PX_PID" 2>/dev/null || true
sleep 0.2
python3 "$HERE/metrics_from_log.py" "$OUT"
echo "run complete -> $OUT"
