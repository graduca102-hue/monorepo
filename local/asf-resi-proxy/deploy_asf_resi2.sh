#!/bin/bash
# Deploy ASF residential v2 (per-customer entitlement ledger + auto-reconcile +
# always-visible "get proxies" + botshop-style menu): db.py, handlers_user.py,
# keyboards.py. Back up, compile, wait, swap, compile+import whole app, restart
# asf, health-check, auto-restore on failure.
set -u
STAMP="$1"
cd /opt/asf || exit 1
D="/opt/asf/backups/resi2-${STAMP}"
mkdir -p "$D/app"
LOG="$D/deploy.log"
exec >"$LOG" 2>&1
FILES="db.py handlers_user.py keyboards.py"

for f in $FILES; do
  cp -p "app/$f" "$D/app/$f" || { echo "BACKUP_FAIL $f"; exit 1; }
  [ -f "app/$f.new_resi_${STAMP}" ] || { echo "MISSING_CANDIDATE $f"; exit 1; }
done

echo "[$(date -Is)] compile candidates"
for f in $FILES; do
  cp -f "app/$f.new_resi_${STAMP}" "/tmp/_r2_$f"
  .venv/bin/python -m py_compile "/tmp/_r2_$f" || { echo "COMPILE_FAIL_CANDIDATE $f"; exit 1; }
done

echo "[$(date -Is)] wait 15s"
sleep 15

for f in $FILES; do cp -f "app/$f.new_resi_${STAMP}" "app/$f"; done
.venv/bin/python -m py_compile app/*.py || {
  echo "COMPILE_FAIL_AFTER_SWAP -> restoring"
  for f in $FILES; do cp -f "$D/app/$f" "app/$f"; done
  echo RESTORED_NO_RESTART; exit 1;
}
.venv/bin/python -c "import app.main" || {
  echo "IMPORT_FAIL_AFTER_SWAP -> restoring"
  for f in $FILES; do cp -f "$D/app/$f" "app/$f"; done
  echo RESTORED_NO_RESTART; exit 1;
}

echo "[$(date -Is)] systemctl restart asf"
systemctl restart asf
sleep 12

ACT=$(systemctl is-active asf)
HP=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 http://127.0.0.1:8080/health)
TB=$(journalctl -u asf --since "-45 seconds" --no-pager | grep -c 'Traceback (most recent call last)')
ENT=$(.venv/bin/python -c "import sqlite3; c=sqlite3.connect('data/shop.db'); print(c.execute('SELECT COUNT(*), IFNULL(ROUND(SUM(gb),2),0) FROM residential_entitlements').fetchone())" 2>&1)
echo "is-active=$ACT health=$HP tracebacks=$TB entitlements=$ENT"

if [ "$ACT" = "active" ] && [ "$HP" = "200" ] && [ "$TB" = "0" ]; then
  for f in $FILES; do rm -f "app/$f.new_resi_${STAMP}"; done
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
