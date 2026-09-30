#!/bin/bash
# Deploy the residential pending-dust absorb:
#   db.py             - default setting market_min_price_usd=0.80 + helper
#   handlers_user.py  - read the floor from the DB instead of a constant
#   handlers_admin.py - SETTING_META entry, validation, pricing screen line
#   keyboards.py      - button in admin_pricing_menu
# Back up, compile candidates, wait for the operator reply to flush, swap,
# compile the whole app, restart asf, health-check, auto-restore on failure.
set -u
STAMP="$1"
cd /opt/asf || exit 1
D="/opt/asf/backups/pricefilter4-${STAMP}"
mkdir -p "$D/app"
LOG="$D/deploy.log"
exec >"$LOG" 2>&1
FILES="handlers_user.py"

for f in $FILES; do
  cp -p "app/$f" "$D/app/$f" || { echo "BACKUP_FAIL $f"; exit 1; }
  [ -f "app/$f.new_pf4_${STAMP}" ] || { echo "MISSING_CANDIDATE $f"; exit 1; }
done
cp -p data/shop.db "$D/shop.db.bak" 2>/dev/null && echo "db backed up"

echo "[$(date -Is)] compile candidates"
for f in $FILES; do
  .venv/bin/python -m py_compile "app/$f.new_pf4_${STAMP}" || { echo "COMPILE_FAIL_CANDIDATE $f"; exit 1; }
done

echo "[$(date -Is)] wait 20s so the operator's Telegram reply flushes"
sleep 20

for f in $FILES; do cp -f "app/$f.new_pf4_${STAMP}" "app/$f"; done
.venv/bin/python -m py_compile app/*.py || {
  echo "COMPILE_FAIL_AFTER_SWAP -> restoring"
  for f in $FILES; do cp -f "$D/app/$f" "app/$f"; done
  echo RESTORED_NO_RESTART; exit 1;
}

echo "[$(date -Is)] systemctl restart asf"
systemctl restart asf
sleep 12

ACT=$(systemctl is-active asf)
HP=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 http://127.0.0.1:8080/health)
TB=$(journalctl -u asf --since "-45 seconds" --no-pager | grep -c 'Traceback (most recent call last)')
echo "is-active=$ACT health=$HP tracebacks=$TB"

if [ "$ACT" = "active" ] && [ "$HP" = "200" ] && [ "$TB" = "0" ]; then
  for f in $FILES; do rm -f "app/$f.new_pf4_${STAMP}"; done
  echo DEPLOY_OK
else
  echo "[$(date -Is)] verification FAILED -> rollback"
  for f in $FILES; do cp -f "$D/app/$f" "app/$f"; done
  systemctl restart asf
  sleep 12
  echo "post-rollback is-active=$(systemctl is-active asf) health=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 http://127.0.0.1:8080/health)"
  echo ROLLED_BACK
fi
echo "[$(date -Is)] done"
