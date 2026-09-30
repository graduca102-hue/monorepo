#!/bin/bash
set +e
NS=wgp009
VH=vh009
PORT=41090
NIP=10.200.9.2

pkill -f "microsocks -i $NIP"
sleep 1
ip netns del $NS
ip link del $VH
iptables -t nat -D PREROUTING -p tcp --dport $PORT -j DNAT --to-destination ${NIP}:1080
iptables -t nat -D POSTROUTING -o $VH -j MASQUERADE
iptables -D FORWARD -d $NIP -p tcp --dport 1080 -j ACCEPT
iptables -D FORWARD -s $NIP -j ACCEPT
rm -f /etc/wireguard/wgp009.conf /etc/wireguard/wgp009.stripped /var/log/wgp009.log

echo "=== after cleanup ==="
ip netns list
ss -ltn 2>/dev/null | grep 41090 || echo "port 41090 free"
exit 0
