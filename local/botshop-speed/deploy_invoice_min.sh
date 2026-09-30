#!/bin/bash
# Deploy the invoice-minimum fix (services.py, keyboard.py) to /opt/botshop:
# back up, compile, wait for the operator reply to flush, swap, compile the
# whole app, restart botshop, health-check, auto-restore on failure.
set -u
STAMP="$1"
cd /opt/botshop || exit 1
D="/opt/botshop/backups/invoice-min-${STAMP}"
mkdir -p "$D"
LOG="$D/deploy.log"
exec >"$LOG" 2>&1
FILES="services.py keyboard.py"

for f in $FILES; do
  cp -p "$f" "$D/$f" || { echo "BACKUP_FAIL $f"; exit 1; }
  [ -f "$f.new_invmin_${STAMP}" ] || { echo "MISSING_CANDIDATE $f"; exit 1; }
done

echo "[$(date -Is)] compile candidates"
for f in $FILES; do
  .venv/bin/python -m py_compile "$f.new_invmin_${STAMP}" || { echo "COMPILE_FAIL_CANDIDATE $f"; exit 1; }
done

echo "[$(date -Is)] wait 20s so the operator's Telegram reply flushes"
sleep 20

for f in $FILES; do cp -f "$f.new_invmin_${STAMP}" "$f"; done
.venv/bin/python -m py_compile services.py keyboard.py miniapp.py main.py database/data.py || {
  echo "COMPILE_FAIL_AFTER_SWAP -> restoring"
  for f in $FILES; do cp -f "$D/$f" "$f"; done
  echo RESTORED_NO_RESTART; exit 1;
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
  for f in $FILES; do rm -f "$f.new_invmin_${STAMP}"; done
  echo DEPLOY_OK
else
  echo "[$(date -Is)] verification FAILED -> rollback"
  for f in $FILES; do cp -f "$D/$f" "$f"; done
  systemctl restart botshop
  sleep 20
  echo "post-rollback is-active=$(systemctl is-active botshop) health=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 http://127.0.0.1:8081/api/v1/health)"
  echo ROLLED_BACK
fi
echo "[$(date -Is)] done"
