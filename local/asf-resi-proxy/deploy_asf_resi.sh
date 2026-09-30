#!/bin/bash
# Deploy the ASF-shop residential proxy management menu
# (app/clients.py, db.py, handlers_user.py, keyboards.py, states.py):
# back up, compile candidates, wait for the operator reply to flush, swap,
# compile the whole app, restart asf, health-check, and auto-restore the
# backups if the bot does not come back clean.
set -u
STAMP="$1"
cd /opt/asf || exit 1
D="/opt/asf/backups/resi-${STAMP}"
mkdir -p "$D/app"
LOG="$D/deploy.log"
exec >"$LOG" 2>&1
FILES="clients.py db.py handlers_user.py keyboards.py states.py"

for f in $FILES; do
  cp -p "app/$f" "$D/app/$f" || { echo "BACKUP_FAIL $f"; exit 1; }
  [ -f "app/$f.new_resi_${STAMP}" ] || { echo "MISSING_CANDIDATE $f"; exit 1; }
done

echo "[$(date -Is)] compile candidates"
for f in $FILES; do
  cp -f "app/$f.new_resi_${STAMP}" "/tmp/_resi_$f"
  .venv/bin/python -m py_compile "/tmp/_resi_$f" || { echo "COMPILE_FAIL_CANDIDATE $f"; exit 1; }
done

echo "[$(date -Is)] wait 20s so the operator's Telegram reply flushes"
sleep 20

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
echo "is-active=$ACT health=$HP tracebacks=$TB"

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
