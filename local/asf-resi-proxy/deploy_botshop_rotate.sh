#!/bin/bash
# Deploy the residential rotate-password endpoint to /opt/botshop
# (vproxy_service.py + miniapp.py): back up, compile, wait for the operator
# reply to flush, swap, restart botshop, health-check, and auto-restore the
# backups if the bot does not come back clean.
set -u
STAMP="$1"
cd /opt/botshop || exit 1
D="/opt/botshop/backups/resi-rotate-${STAMP}"
mkdir -p "$D"
LOG="$D/deploy.log"
exec >"$LOG" 2>&1
FILES="vproxy_service.py miniapp.py"

for f in $FILES; do
  cp -p "/opt/botshop/$f" "$D/$f" || { echo "BACKUP_FAIL $f"; exit 1; }
  [ -f "/opt/botshop/$f.new_rotate_${STAMP}" ] || { echo "MISSING_CANDIDATE $f"; exit 1; }
done

echo "[$(date -Is)] compile candidates"
for f in $FILES; do
  .venv/bin/python -m py_compile "/opt/botshop/$f.new_rotate_${STAMP}" || { echo "COMPILE_FAIL_CANDIDATE $f"; exit 1; }
done

echo "[$(date -Is)] wait 20s so the operator's Telegram reply flushes"
sleep 20

for f in $FILES; do cp -f "/opt/botshop/$f.new_rotate_${STAMP}" "/opt/botshop/$f"; done
.venv/bin/python -m py_compile vproxy_service.py miniapp.py services.py main.py || {
  echo "COMPILE_FAIL_AFTER_SWAP -> restoring"
  for f in $FILES; do cp -f "$D/$f" "/opt/botshop/$f"; done
  echo RESTORED_NO_RESTART; exit 1;
}

echo "[$(date -Is)] systemctl restart botshop"
systemctl restart botshop
sleep 25

ACT=$(systemctl is-active botshop)
HEALTH=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 http://127.0.0.1:8081/api/v1/health)
CATS=$(curl -s -o /dev/null -w '%{http_code}' --max-time 25 http://127.0.0.1:8081/market/api/categories)
ROUTE=$(curl -s --max-time 15 -X POST http://127.0.0.1:8081/api/v1/proxy/residential/clients/probeclient/rotate-password -H 'Content-Type: application/json' -d '{}' -o /dev/null -w '%{http_code}')
OPENAPI=$(curl -s --max-time 15 http://127.0.0.1:8081/api/v1/openapi.json | grep -c 'rotate-password')
TB=$(journalctl -u botshop --since "-60 seconds" --no-pager | grep -c 'Traceback (most recent call last)')
echo "is-active=$ACT health=$HEALTH categories=$CATS route_no_auth=$ROUTE openapi_has_route=$OPENAPI tracebacks=$TB"

# route_no_auth should be 401 (auth required) — proves the route exists and the
# handler is reached without touching provider state.
if [ "$ACT" = "active" ] && [ "$HEALTH" = "200" ] && [ "$CATS" = "200" ] && [ "$TB" = "0" ] && [ "$ROUTE" = "401" ] && [ "$OPENAPI" = "1" ]; then
  for f in $FILES; do rm -f "/opt/botshop/$f.new_rotate_${STAMP}"; done
  echo DEPLOY_OK
else
  echo "[$(date -Is)] verification FAILED -> rollback"
  for f in $FILES; do cp -f "$D/$f" "/opt/botshop/$f"; done
  systemctl restart botshop
  sleep 20
  echo "post-rollback is-active=$(systemctl is-active botshop) health=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 http://127.0.0.1:8081/api/v1/health)"
  echo ROLLED_BACK
fi
echo "[$(date -Is)] done"
