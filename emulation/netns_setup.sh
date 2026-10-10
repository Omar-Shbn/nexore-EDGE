#!/usr/bin/env bash
# Namespace testbed (plan §8): gw (apps + tunnel + monitor/policy),
# px (aggregation proxy), one veth pair per technology with netem on both
# directions. Run inside WSL/Linux as root.
#
#   sudo bash netns_setup.sh up      # create
#   sudo bash netns_setup.sh down    # destroy
set -euo pipefail

LINKS=(wifi 5g leo)
SUBNET=(1 2 3)

up() {
  ip netns add gw 2>/dev/null || true
  ip netns add px 2>/dev/null || true
  for i in "${!LINKS[@]}"; do
    L=${LINKS[$i]}; N=${SUBNET[$i]}
    ip link add "v${L}g" type veth peer name "v${L}p"
    ip link set "v${L}g" netns gw
    ip link set "v${L}p" netns px
    ip netns exec gw ip addr add "10.77.${N}.1/24" dev "v${L}g"
    ip netns exec px ip addr add "10.77.${N}.2/24" dev "v${L}p"
    ip netns exec gw ip link set "v${L}g" up
    ip netns exec px ip link set "v${L}p" up
    # netem roots; the replayer updates rate/delay/loss every 100 ms
    ip netns exec gw tc qdisc add dev "v${L}g" root netem rate 1mbit delay 20ms loss 100%
    ip netns exec px tc qdisc add dev "v${L}p" root netem rate 1mbit delay 20ms loss 100%
    # permanent neighbour entries: ARP/NUD re-validation runs THROUGH netem,
    # so a lost ARP probe stalls the veth ~1 s every ~30 s (kernel neigh
    # lifecycle) and dumps ~100 queued packets on resolution. Static ARP
    # removes that artifact entirely.
    GMAC=$(ip netns exec gw cat "/sys/class/net/v${L}g/address")
    PMAC=$(ip netns exec px cat "/sys/class/net/v${L}p/address")
    ip netns exec gw ip neigh replace "10.77.${N}.2" lladdr "$PMAC" dev "v${L}g" nud permanent
    ip netns exec px ip neigh replace "10.77.${N}.1" lladdr "$GMAC" dev "v${L}p" nud permanent
  done
  ip netns exec gw ip link set lo up
  ip netns exec px ip link set lo up
  echo "namespaces up: gw <-> px over ${LINKS[*]}"
}

down() {
  ip netns del gw 2>/dev/null || true
  ip netns del px 2>/dev/null || true
  echo "namespaces removed"
}

case "${1:-up}" in
  up) up ;;
  down) down ;;
  *) echo "usage: $0 up|down" >&2; exit 1 ;;
esac
