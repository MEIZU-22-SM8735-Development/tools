#!/bin/sh
# Bring the M2582's USB gadget network interface up on the host side.
#
# The gadget exposes CDC NCM (or ECM/RNDIS, whichever it was composed with)
# with a pinned host MAC of 02:1d:6b:01:04:02, so the interface is found by MAC
# rather than by name: the kernel names it after the port it landed on, which
# changes between machines and between USB ports.
#
# Usage: net-up.sh [up|down|status|speed]
#   up      assign 192.168.42.2/24 and raise the link   (default)
#   down    remove the address and lower the link
#   status  print the interface, its addresses and counters
#   speed   run a two-way iperf3 test against the board
#
# The board side is expected at 192.168.42.1, configured by
# /sbin/m2582-usb-gadget --net-up-only, which the distro runs at boot.

set -u
HOST_MAC=02:1d:6b:01:04:02
HOST_ADDR=192.168.42.2/24
BOARD_ADDR=192.168.42.1

find_iface() {
	for d in /sys/class/net/*; do
		[ -r "$d/address" ] || continue
		[ "$(cat "$d/address")" = "$HOST_MAC" ] || continue
		basename "$d"
		return 0
	done
	return 1
}

iface=${M2582_IFACE:-}
if [ -z "$iface" ]; then
	iface=$(find_iface) || {
		echo "no interface with MAC $HOST_MAC yet." >&2
		echo "Is the board booted and in Linux mode? Try: lsusb | grep 1d6b:0104" >&2
		exit 1
	}
fi

case "${1:-up}" in
up)
	ip addr replace "$HOST_ADDR" dev "$iface"
	ip link set "$iface" up
	echo "$iface up with $HOST_ADDR"
	# The board applies its own address at boot; if it is slow, wait for it
	# rather than reporting a dead link.
	i=0
	while [ "$i" -lt 15 ]; do
		ping -c1 -W1 "$BOARD_ADDR" >/dev/null 2>&1 && break
		i=$((i + 1))
	done
	if ping -c2 -W2 "$BOARD_ADDR" >/dev/null 2>&1; then
		echo "board reachable at $BOARD_ADDR"
	else
		echo "board not answering at $BOARD_ADDR" >&2
		echo "check the board side: ssh root@$BOARD_ADDR /sbin/m2582-usb-gadget --net-up-only" >&2
	fi
	;;
down)
	ip addr del "$HOST_ADDR" dev "$iface" 2>/dev/null
	ip link set "$iface" down
	echo "$iface down"
	;;
status)
	echo "=== $iface ==="
	ip -br addr show "$iface"
	ip -s link show "$iface"
	;;
speed)
	echo "--- host to board (board receives) ---"
	iperf3 -c "$BOARD_ADDR" -t 10
	echo "--- board to host (board sends) ---"
	iperf3 -c "$BOARD_ADDR" -t 10 -R
	;;
*)
	echo "usage: $0 [up|down|status|speed]" >&2
	exit 2
	;;
esac
