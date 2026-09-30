#!/bin/bash
set -e
NS=wgp009
NIP=10.200.9.2
VH=vh009
PORT=41090
USR=wgp009
PW="$1"

sysctl -qw net.ipv4.ip_forward=1

iptables -t nat -C PREROUTING -p tcp --dport $PORT -j DNAT --to-destination ${NIP}:1080 2>/dev/null || \
  iptables -t nat -A PREROUTING -p tcp --dport $PORT -j DNAT --to-destination ${NIP}:1080
iptables -t nat -C POSTROUTING -o $VH -j MASQUERADE 2>/dev/null || \
  iptables -t nat -A POSTROUTING -o $VH -j MASQUERADE
iptables -C FORWARD -d $NIP -p tcp --dport 1080 -j ACCEPT 2>/dev/null || \
  iptables -A FORWARD -d $NIP -p tcp --dport 1080 -j ACCEPT
iptables -C FORWARD -s $NIP -j ACCEPT 2>/dev/null || \
  iptables -A FORWARD -s $NIP -j ACCEPT

pkill -f "microsocks -i $NIP" 2>/dev/null || true
sleep 1
ip netns exec $NS nohup microsocks -i $NIP -p 1080 -u "$USR" -P "$PW" >/var/log/wgp009.log 2>&1 &
disown || true
sleep 2

echo "=== ns listen ==="
ip netns exec $NS ss -ltnp | grep 1080 || echo "microsocks not listening"
echo "=== curl via localhost socks ==="
curl -s --max-time 20 --socks5 "$USR:$PW@127.0.0.1:$PORT" https://api.ipify.org; echo
echo "=== curl via public socks ==="
curl -s --max-time 20 --socks5 "$USR:$PW@2.27.22.150:$PORT" https://api.ipify.org; echo
