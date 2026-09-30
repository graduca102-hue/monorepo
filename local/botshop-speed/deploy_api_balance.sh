#!/bin/bash
# Deploy the "credit API balance" admin action (keyboard.py) to /opt/botshop.
set -u
STAMP="$1"
cd /opt/botshop || exit 1
D="/opt/botshop/backups/api-balance-${STAMP}"
mkdir -p "$D"
LOG="$D/deploy.log"
exec >"$LOG" 2>&1
f="keyboard.py"
cp -p "$f" "$D/$f" || { echo BACKUP_FAIL; exit 1; }
[ -f "$f.new_apibal_${STAMP}" ] || { echo MISSING_CANDIDATE; exit 1; }
echo "[$(date -Is)] compile candidate"
.venv/bin/python -m py_compile "$f.new_apibal_${STAMP}" || { echo COMPILE_FAIL_CANDIDATE; exit 1; }
echo "[$(date -Is)] wait 20s"
sleep 20
cp -f "$f.new_apibal_${STAMP}" "$f"
.venv/bin/python -m py_compile keyboard.py services.py miniapp.py main.py database/data.py || {
  echo "COMPILE_FAIL_AFTER_SWAP -> restoring"; cp -f "$D/$f" "$f"; echo RESTORED_NO_RESTART; exit 1; }
echo "[$(date -Is)] restart botshop"
systemctl restart botshop
sleep 25
ACT=$(systemctl is-active botshop)
HEALTH=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 http://127.0.0.1:8081/api/v1/health)
CATS=$(curl -s -o /dev/null -w '%{http_code}' --max-time 25 http://127.0.0.1:8081/market/api/categories)
TB=$(journalctl -u botshop --since "-55 seconds" --no-pager | grep -c 'Traceback (most recent call last)')
echo "is-active=$ACT health=$HEALTH categories=$CATS tracebacks=$TB"
if [ "$ACT" = "active" ] && [ "$HEALTH" = "200" ] && [ "$CATS" = "200" ] && [ "$TB" = "0" ]; then
  rm -f "$f.new_apibal_${STAMP}"; echo DEPLOY_OK
else
  echo "[$(date -Is)] FAILED -> rollback"; cp -f "$D/$f" "$f"; systemctl restart botshop; sleep 20
  echo "post-rollback is-active=$(systemctl is-active botshop) health=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 http://127.0.0.1:8081/api/v1/health)"
  echo ROLLED_BACK
fi
echo "[$(date -Is)] done"
