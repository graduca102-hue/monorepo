#!/bin/bash
# wgp_up.sh <id> <public_port> <socks_user> <socks_pass>
# Brings up a WG-in-netns SOCKS5 proxy.
# Reads /etc/wireguard/wgp<id>.conf (standard wg-quick client conf).
set -euo pipefail

ID="$1"
PORT="$2"
USR="$3"
PWD="$4"

NS="wgp${ID}"
VH="vh${ID}"
VN="vn${ID}"
# per-id /30 subnet: 10.200.<id>.0/30 (id must be 0..255)
OCTET=$((10#${ID}))
NET="10.200.${OCTET}"
HIP="${NET}.1"
NIP="${NET}.2"

CONF="/etc/wireguard/wgp${ID}.conf"
[ -f "$CONF" ] || { echo "no conf: $CONF"; exit 2; }

# tear down previous
ip netns del "$NS" 2>/dev/null || true
ip link del "$VH" 2>/dev/null || true
iptables -t nat -D PREROUTING -p tcp --dport "$PORT" -j DNAT --to-destination "${NIP}:1080" 2>/dev/null || true
iptables -t nat -D POSTROUTING -o "$VH" -j MASQUERADE 2>/dev/null || true

# enable ip_forward
sysctl -qw net.ipv4.ip_forward=1

# parse Address from conf
ADDR4=$(awk -F'= *' '/^Address/{print $2}' "$CONF" | tr ',' '\n' | grep -E '^ *[0-9]+\.' | head -1 | tr -d ' ')
[ -n "$ADDR4" ] || { echo "no Address in conf"; exit 3; }

# strip wg-quick-only keys for wg setconf
STRIPPED=$(mktemp)
awk 'BEGIN{p=1} /^\[Interface\]/{print;p=1;next} /^\[Peer\]/{print;p=1;next} p{ if($0 ~ /^(Address|DNS|MTU|Table|PreUp|PostUp|PreDown|PostDown|SaveConfig) *=/) next; print}' "$CONF" > "$STRIPPED"

# ns + veth
ip netns add "$NS"
ip -n "$NS" link set lo up
ip link add "$VH" type veth peer name "$VN"
ip link set "$VN" netns "$NS"
ip addr add "${HIP}/30" dev "$VH"
ip -n "$NS" addr add "${NIP}/30" dev "$VN"
ip link set "$VH" up
ip -n "$NS" link set "$VN" up

# wg iface inside ns
ip link add wg0 type wireguard
ip link set wg0 netns "$NS"
ip -n "$NS" addr add "$ADDR4" dev wg0
ip netns exec "$NS" wg setconf wg0 "$STRIPPED"
ip -n "$NS" link set wg0 up
ip -n "$NS" route add default dev wg0
rm -f "$STRIPPED"

# NAT: expose public :PORT -> ns socks; masquerade so return traffic uses veth
iptables -t nat -A PREROUTING -p tcp --dport "$PORT" -j DNAT --to-destination "${NIP}:1080"
iptables -t nat -A POSTROUTING -o "$VH" -j MASQUERADE
iptables -A FORWARD -d "$NIP" -p tcp --dport 1080 -j ACCEPT
iptables -A FORWARD -s "$NIP" -j ACCEPT

# launch microsocks in ns
pkill -f "microsocks .* -i ${NIP}" 2>/dev/null || true
ip netns exec "$NS" nohup microsocks -i "$NIP" -p 1080 -u "$USR" -P "$PWD" >/var/log/wgp${ID}.log 2>&1 &
sleep 1

# verify
echo "=== wg handshake ==="
ip netns exec "$NS" wg show wg0 latest-handshakes || true
echo "=== exit ip via ns ==="
ip netns exec "$NS" curl -s --max-time 10 https://api.ipify.org || echo "ns curl failed"
echo
echo "=== exit ip via public socks ==="
curl -s --max-time 15 --socks5 "${USR}:${PWD}@127.0.0.1:${PORT}" https://api.ipify.org || echo "socks curl failed"
echo
