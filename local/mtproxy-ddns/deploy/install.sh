#!/bin/bash
# One-shot install of mtproxy-ddns on srv2:
#  - add the aikort.lol master zone to BIND (idempotent, with checks + backup)
#  - install & start the systemd updater
# Assumes /opt/mtproxy-ddns/{app,deploy,.env} are already uploaded.
set -eu
ZONE="${MTPROXY_ZONE:-aikort.lol}"
ZONEFILE="/etc/bind/db.${ZONE}"
SRC="/opt/mtproxy-ddns/deploy/db.${ZONE}"
STAMP="$(date +%Y%m%d_%H%M%S)"
BK="/opt/mtproxy-ddns/backups/${STAMP}"
mkdir -p "$BK" /opt/mtproxy-ddns/data

echo "[1] zone file"
if [ -f "$ZONEFILE" ]; then
  cp -p "$ZONEFILE" "$BK/"
  echo "    kept existing $ZONEFILE (backed up)"
else
  install -o root -g bind -m 644 "$SRC" "$ZONEFILE"
  echo "    installed $ZONEFILE"
fi

echo "[2] named.conf.local"
if grep -q "zone \"${ZONE}\"" /etc/bind/named.conf.local; then
  echo "    zone already declared"
else
  cp -p /etc/bind/named.conf.local "$BK/"
  cat >> /etc/bind/named.conf.local <<EOF

zone "${ZONE}" {
    type master;
    file "${ZONEFILE}";
};
EOF
  echo "    added zone block"
fi

echo "[3] validate"
named-checkconf
named-checkzone "$ZONE" "$ZONEFILE"

echo "[4] reload BIND"
rndc reconfig
sleep 1
rndc reload "$ZONE" || true
DIG=$(dig +short SOA "$ZONE" @127.0.0.1)
echo "    local SOA: ${DIG:-<none>}"
[ -n "$DIG" ] || { echo "ZONE NOT SERVED — aborting"; exit 1; }

echo "[5] systemd unit"
cp -f /opt/mtproxy-ddns/deploy/mtproxy-ddns.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now mtproxy-ddns
sleep 8

echo "[6] verify"
systemctl is-active mtproxy-ddns
journalctl -u mtproxy-ddns -n 15 --no-pager | cat
echo "--- resolve mt.${ZONE} locally ---"
dig +short "mt.${ZONE}" @127.0.0.1
echo "DONE"
