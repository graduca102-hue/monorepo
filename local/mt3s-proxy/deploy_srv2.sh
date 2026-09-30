#!/bin/bash
# Deploy the MT3S -> aikort.lol DNS sync on srv2.
#  1. add a BIND master zone for aikort.lol (mtproto A record, daemon-managed)
#  2. verify config + that sousmarketfranchize.shop still resolves -> else roll back
#  3. install + start the mt3s-dns.service poller
#
# Run on srv2. Needs $MT3S_API_TOKEN in the environment or already in
# /opt/mt3s-dns/.env. Files (mt3s_dns_sync.py, mt3s-dns.service) sit next to this.
set -u
STAMP="$1"
SRC="$(cd "$(dirname "$0")" && pwd)"
ZONE="aikort.lol"
ZFILE="/etc/bind/db.${ZONE}"
NCL="/etc/bind/named.conf.local"
BK="/opt/mt3s-dns/backups/${STAMP}"
mkdir -p /opt/mt3s-dns "$BK"
LOG="$BK/deploy.log"
exec >"$LOG" 2>&1

echo "[$(date -Is)] backup named.conf.local"
cp -p "$NCL" "$BK/named.conf.local"

# --- token / .env ---
if [ ! -f /opt/mt3s-dns/.env ]; then
  if [ -z "${MT3S_API_TOKEN:-}" ]; then echo "NO_TOKEN: pass MT3S_API_TOKEN or create /opt/mt3s-dns/.env"; exit 1; fi
  printf 'MT3S_API_TOKEN=%s\nMT3S_API_URL=%s\n' "$MT3S_API_TOKEN" "https://api.confmtbot.com/public-listener" > /opt/mt3s-dns/.env
  chmod 600 /opt/mt3s-dns/.env
fi
TOKEN="$(grep -E '^MT3S_API_TOKEN=' /opt/mt3s-dns/.env | cut -d= -f2-)"

# --- seed IP from the MT3S API ---
echo "[$(date -Is)] querying MT3S /public-listener"
RESP="$(curl --fail --silent --show-error --connect-timeout 4 --max-time 8 -H "Authorization: Bearer ${TOKEN}" https://api.confmtbot.com/public-listener || true)"
echo "response: $RESP"
IP="$(printf '%s' "$RESP" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d["ipv4"])' 2>/dev/null || true)"
if ! printf '%s' "$IP" | grep -qE '^([0-9]{1,3}\.){3}[0-9]{1,3}$'; then
  echo "BAD_SEED_IP: '$IP' — MT3S API unreachable or malformed; aborting (nothing changed)"; exit 1
fi
echo "seed ip = $IP"

SERIAL="$(date -u +%Y%m%d)01"

# --- write zone file ---
cat > "$ZFILE" <<EOF
; managed by mt3s-dns.service — do not hand-edit
\$TTL 3600
@   IN  SOA ns1.31-77-145-160.sslip.io. admin.${ZONE}. (
        ${SERIAL} ; Serial
        3600       ; Refresh
        1800       ; Retry
        1209600    ; Expire
        60 )       ; Negative Cache TTL
@       IN  NS      ns1.31-77-145-160.sslip.io.
@       IN  NS      ns2.31-77-145-160.sslip.io.
@       IN  A       31.77.145.160
mtproto	60	IN	A	${IP}
EOF

if ! named-checkzone "$ZONE" "$ZFILE"; then
  echo "ZONE_CHECK_FAIL -> removing zone file, nothing else touched"; rm -f "$ZFILE"; exit 1
fi

# --- register the zone (idempotent) ---
if ! grep -q "zone \"${ZONE}\"" "$NCL"; then
  cat >> "$NCL" <<EOF

zone "${ZONE}" {
    type master;
    file "${ZFILE}";
};
EOF
fi

if ! named-checkconf; then
  echo "NAMED_CHECKCONF_FAIL -> rolling back named.conf.local"
  cp -f "$BK/named.conf.local" "$NCL"; rm -f "$ZFILE"; exit 1
fi

rndc reconfig
sleep 1

# --- verify: existing zone still good, new zone answers ---
OLD_OK="$(dig +short SOA sousmarketfranchize.shop @127.0.0.1 | head -1)"
NEW_OK="$(dig +short A mtproto.${ZONE} @127.0.0.1 | head -1)"
echo "sousmarketfranchize.shop SOA @localhost: $OLD_OK"
echo "mtproto.${ZONE} A @localhost: $NEW_OK"
if [ -z "$OLD_OK" ] || [ "$NEW_OK" != "$IP" ]; then
  echo "VERIFY_FAIL -> rolling back"
  cp -f "$BK/named.conf.local" "$NCL"; rm -f "$ZFILE"; rndc reconfig
  exit 1
fi

# --- install the poller ---
cp -f "$SRC/mt3s_dns_sync.py" /opt/mt3s-dns/mt3s_dns_sync.py
printf '%s' "$IP" > /opt/mt3s-dns/last_ip
cp -f "$SRC/mt3s-dns.service" /etc/systemd/system/mt3s-dns.service
systemctl daemon-reload
systemctl enable --now mt3s-dns.service
sleep 3
echo "service: $(systemctl is-active mt3s-dns.service)"
journalctl -u mt3s-dns.service -n 10 --no-pager

echo "[$(date -Is)] DEPLOY_OK  mtproto.${ZONE} -> ${IP}"
