#!/bin/sh
# Make the M2582's USB gadget network come up on the host by itself.
#
# Run once as root:
#
#     sudo sh net-install.sh
#
# The gadget re-enumerates whenever the board reboots and the kernel creates
# the interface fresh, so an address added by hand does not survive that.
# NetworkManager cannot be used for this either: it claims the interface, runs
# DHCP, gets no answer (the board runs no DHCP server) and then *removes* any
# address on it. NM is therefore told to leave this interface alone and udev
# does the addressing. Both match the gadget's pinned host MAC
# 02:1d:6b:01:04:02, so no other interface can be caught.
#
# To undo: delete /etc/udev/rules.d/70-m2582-net.rules and
# /usr/local/sbin/m2582-net-up, then "udevadm control --reload".

set -eu

HOST_MAC=02:1d:6b:01:04:02
HOST_ADDR=192.168.42.2/24
RULE=/etc/udev/rules.d/70-m2582-net.rules
HELPER=/usr/local/sbin/m2582-net-up

if [ "$(id -u)" != 0 ]; then
	echo "must run as root: sudo sh $0" >&2
	exit 1
fi

cat > "$HELPER" <<'HELPEREOF'
#!/bin/sh
# Address the M2582 USB gadget link. Called by udev when the interface appears.
# $1 is the interface name. Never fails the udev event.
IF=$1
[ -n "$IF" ] || exit 0
ip addr replace 192.168.42.2/24 dev "$IF" 2>/dev/null
ip link set "$IF" up 2>/dev/null
exit 0
HELPEREOF
chmod 755 "$HELPER"

cat > "$RULE" <<RULEEOF
# M2582 USB gadget network: the host end of the CDC NCM link.
#
# Matched by the pinned host MAC, so it cannot catch anything else. The address
# is applied by the helper rather than inline to keep the rule readable.
#
# "change" is matched as well as "add" because the kernel can keep the same
# net_device across a re-enumeration, in which case only a change event fires.
ACTION=="add|change", SUBSYSTEM=="net", ATTR{address}=="$HOST_MAC", RUN+="$HELPER %k"
RULEEOF
chmod 644 "$RULE"

# Keep NetworkManager from managing the interface across reboots: "nmcli device
# set managed no" is runtime state and is forgotten on the next boot, so a
# conf.d entry is what makes it stick. Matched by MAC, so no other interface is
# affected.
if [ -d /etc/NetworkManager/conf.d ] || command -v nmcli >/dev/null 2>&1; then
	mkdir -p /etc/NetworkManager/conf.d
	cat > /etc/NetworkManager/conf.d/70-m2582-unmanaged.conf <<NMEOF
# M2582 USB gadget link, matched by the pinned host MAC so no other interface
# is affected. NetworkManager must not manage this one: there is no DHCP server
# to talk to, and NM's response to that is to remove the static address.
[keyfile]
unmanaged-devices=mac:$HOST_MAC
NMEOF
	chmod 644 /etc/NetworkManager/conf.d/70-m2582-unmanaged.conf
	echo "NetworkManager conf.d written (unmanaged by MAC)"
	for d in /sys/class/net/*; do
		[ "$(cat "$d/address" 2>/dev/null)" = "$HOST_MAC" ] || continue
		nmcli device set "$(basename "$d")" managed no 2>/dev/null || true
	done
	nmcli general reload conf 2>/dev/null || systemctl reload NetworkManager 2>/dev/null || true
fi

# Apply it to the interface that exists right now, so the link is usable
# without waiting for a re-plug.
for d in /sys/class/net/*; do
	[ "$(cat "$d/address" 2>/dev/null)" = "$HOST_MAC" ] || continue
	"$HELPER" "$(basename "$d")"
	echo "configured $(basename "$d") with $HOST_ADDR"
done

udevadm control --reload 2>/dev/null || true
udevadm trigger --subsystem-match=net 2>/dev/null || true

echo "installed:"
echo "  $RULE"
echo "  $HELPER"
echo
echo "The link now comes up on its own after every board reboot."
echo "Check it with: sh net-up.sh up"
