#!/bin/bash
# Apply the assortment-snapshot patch to /opt/botshop/miniapp.py, restart the
# botshop bot, verify it came back, and auto-restore the backup if it did not.
set -u
STAMP="$1"
D="/opt/botshop/backups/assortment-snapshot-${STAMP}"
BAK="/opt/botshop/miniapp.py.bak_assortment_${STAMP}"
mkdir -p "$D"
LOG="$D/deploy.log"
exec >"$LOG" 2>&1
cd /opt/botshop || exit 1

echo "[$(date -Is)] applying patch"
.venv/bin/python /opt/botshop/_patch_assortment.py "$STAMP" || { echo "PATCH_FAIL"; exit 1; }

echo "[$(date -Is)] py_compile"
.venv/bin/python -m py_compile miniapp.py services.py main.py || {
  echo "COMPILE_FAIL -> restoring"
  cp -f "$BAK" /opt/botshop/miniapp.py
  echo "RESTORED_NO_RESTART"
  exit 1
}

echo "[$(date -Is)] systemctl restart botshop"
systemctl restart botshop
sleep 22

ACT=$(systemctl is-active botshop)
CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 http://127.0.0.1:8081/market/api/categories)
TB=$(journalctl -u botshop --since "-45 seconds" --no-pager | grep -c 'Traceback (most recent call last)')
echo "is-active=$ACT  categories_http=$CODE  tracebacks=$TB"

if [ "$ACT" = "active" ] && [ "$CODE" = "200" ] && [ "$TB" = "0" ]; then
  echo "[$(date -Is)] warming assortment snapshot"
  ASSORT=$(curl -s -o /dev/null -w '%{http_code} t=%{time_total}' --max-time 90 http://127.0.0.1:8081/assortment/)
  echo "assortment(first)=$ASSORT"
  ASSORT2=$(curl -s -o /dev/null -w '%{http_code} t=%{time_total}' --max-time 90 http://127.0.0.1:8081/assortment/)
  echo "assortment(cached)=$ASSORT2"
  echo "DEPLOY_OK"
else
  echo "[$(date -Is)] verification failed -> rollback"
  cp -f "$BAK" /opt/botshop/miniapp.py
  systemctl restart botshop
  sleep 18
  echo "post-rollback is-active=$(systemctl is-active botshop)  categories_http=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 http://127.0.0.1:8081/market/api/categories)"
  echo "ROLLED_BACK"
fi
