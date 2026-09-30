#!/bin/bash
# Deploy the mini-app latency patch to /opt/botshop/miniapp.py:
# back up, compile, wait for the operator reply to flush, swap, restart botshop,
# health-check, and auto-restore the backup if the bot does not come back clean.
set -u
STAMP="$1"
cd /opt/botshop || exit 1
D="/opt/botshop/backups/speed-${STAMP}"
mkdir -p "$D"
LOG="$D/deploy.log"
exec >"$LOG" 2>&1
BAK="$D/miniapp.py"
NEW="/opt/botshop/miniapp.py.new_speed_${STAMP}"

echo "[$(date -Is)] backup current miniapp.py -> $BAK"
cp -p /opt/botshop/miniapp.py "$BAK" || { echo BACKUP_FAIL; exit 1; }

echo "[$(date -Is)] compile candidate"
.venv/bin/python -m py_compile "$NEW" || { echo COMPILE_FAIL_CANDIDATE; exit 1; }

echo "[$(date -Is)] wait 20s so the operator's Telegram reply flushes"
sleep 20

cp -f "$NEW" /opt/botshop/miniapp.py
.venv/bin/python -m py_compile miniapp.py services.py main.py || {
  echo "COMPILE_FAIL_AFTER_SWAP -> restoring"; cp -f "$BAK" /opt/botshop/miniapp.py; echo RESTORED_NO_RESTART; exit 1;
}

echo "[$(date -Is)] systemctl restart botshop"
systemctl restart botshop
sleep 25

ACT=$(systemctl is-active botshop)
HEALTH=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 http://127.0.0.1:8081/api/v1/health)
CATS=$(curl -s -o /dev/null -w '%{http_code}' --max-time 25 http://127.0.0.1:8081/market/api/categories)
TB=$(journalctl -u botshop --since "-55 seconds" --no-pager | grep -c 'Traceback (most recent call last)')
echo "is-active=$ACT health=$HEALTH categories=$CATS tracebacks=$TB"

if [ "$ACT" = "active" ] && [ "$HEALTH" = "200" ] && [ "$CATS" = "200" ] && [ "$TB" = "0" ]; then
  A1=$(curl -s -o /dev/null -w 'code=%{http_code} t=%{time_total}' --max-time 90 http://127.0.0.1:8081/assortment/)
  echo "assortment_warm=$A1"
  rm -f "$NEW"
  echo DEPLOY_OK
else
  echo "[$(date -Is)] verification FAILED -> rollback to $BAK"
  cp -f "$BAK" /opt/botshop/miniapp.py
  systemctl restart botshop
  sleep 20
  echo "post-rollback is-active=$(systemctl is-active botshop) health=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 http://127.0.0.1:8081/api/v1/health)"
  echo ROLLED_BACK
fi
echo "[$(date -Is)] done"
