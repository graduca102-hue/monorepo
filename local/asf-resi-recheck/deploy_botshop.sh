#!/bin/bash
# Deploy residential validate-and-replace to /opt/botshop
# (vproxy_service.py + miniapp.py): back up, compile candidates, swap, restart
# botshop, verify (health + market + new /proxies/refill route + OpenAPI + no
# tracebacks), and auto-restore the backups on any failure.
set -u
STAMP="$1"
cd /opt/botshop || exit 1
D="/opt/botshop/backups/resi-recheck-${STAMP}"
mkdir -p "$D"
LOG="$D/deploy.log"
exec >"$LOG" 2>&1
FILES="vproxy_service.py miniapp.py"

for f in $FILES; do
  cp -p "/opt/botshop/$f" "$D/$f" || { echo "BACKUP_FAIL $f"; exit 1; }
  [ -f "/opt/botshop/$f.new_recheck_${STAMP}" ] || { echo "MISSING_CANDIDATE $f"; exit 1; }
done

echo "[$(date -Is)] compile candidates"
for f in $FILES; do
  .venv/bin/python -m py_compile "/opt/botshop/$f.new_recheck_${STAMP}" || { echo "COMPILE_FAIL_CANDIDATE $f"; exit 1; }
done

for f in $FILES; do cp -f "/opt/botshop/$f.new_recheck_${STAMP}" "/opt/botshop/$f"; done
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
REFILL=$(curl -s --max-time 15 -X POST http://127.0.0.1:8081/api/v1/proxy/residential/clients/probeclient/proxies/refill -H 'Content-Type: application/json' -d '{}' -o /dev/null -w '%{http_code}')
OPENAPI=$(curl -s --max-time 15 http://127.0.0.1:8081/api/v1/openapi.json | grep -c 'proxies/refill')
# Only fatal import/syntax problems in the files we touched should block the
# deploy — unrelated runtime tracebacks (e.g. "chat not found" while fulfilling a
# pending order) must not trigger a rollback.
FATAL=$(journalctl -u botshop --since "-90 seconds" --no-pager | grep -cE 'ModuleNotFoundError|ImportError|SyntaxError|NameError|AttributeError:.*vproxy_service|cannot import name')
sleep 12
ACT2=$(systemctl is-active botshop)   # crash-loop guard
echo "is-active=$ACT/$ACT2 health=$HEALTH categories=$CATS refill_no_auth=$REFILL openapi_has_refill=$OPENAPI fatal=$FATAL"

if [ "$ACT" = "active" ] && [ "$ACT2" = "active" ] && [ "$HEALTH" = "200" ] && [ "$CATS" = "200" ] && [ "$FATAL" = "0" ] && [ "$REFILL" = "401" ] && [ "$OPENAPI" -ge "1" ]; then
  for f in $FILES; do rm -f "/opt/botshop/$f.new_recheck_${STAMP}"; done
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
