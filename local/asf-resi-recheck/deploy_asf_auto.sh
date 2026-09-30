#!/bin/bash
# Deploy the always-on residential auto-check to /opt/asf:
#   modified: app/db.py app/main.py
#   new file: app/residential_autocheck.py
# Back up, compile, swap, restart asf, verify (health + no tracebacks + the
# autocheck loop logged its start line), auto-restore on any failure.
set -u
STAMP="$1"
cd /opt/asf || exit 1
D="/opt/asf/backups/resi-autocheck-${STAMP}"
mkdir -p "$D"
LOG="$D/deploy.log"
exec >"$LOG" 2>&1
MOD="db.py main.py"
NEWF="residential_autocheck.py"

for f in $MOD; do
  cp -p "/opt/asf/app/$f" "$D/$f" || { echo "BACKUP_FAIL $f"; exit 1; }
  [ -f "/opt/asf/app/$f.new_auto_${STAMP}" ] || { echo "MISSING_CANDIDATE $f"; exit 1; }
done
[ -f "/opt/asf/app/${NEWF}.new_auto_${STAMP}" ] || { echo "MISSING_CANDIDATE $NEWF"; exit 1; }

echo "[$(date -Is)] compile candidates"
for f in $MOD $NEWF; do
  .venv/bin/python -m py_compile "/opt/asf/app/$f.new_auto_${STAMP}" || { echo "COMPILE_FAIL_CANDIDATE $f"; exit 1; }
done

for f in $MOD; do cp -f "/opt/asf/app/$f.new_auto_${STAMP}" "/opt/asf/app/$f"; done
cp -f "/opt/asf/app/${NEWF}.new_auto_${STAMP}" "/opt/asf/app/${NEWF}"

.venv/bin/python -m py_compile app/*.py || {
  echo "COMPILE_FAIL_AFTER_SWAP -> restoring"
  for f in $MOD; do cp -f "$D/$f" "/opt/asf/app/$f"; done
  rm -f "/opt/asf/app/${NEWF}"
  echo RESTORED_NO_RESTART; exit 1;
}

echo "[$(date -Is)] systemctl restart asf"
systemctl restart asf
sleep 22

ACT=$(systemctl is-active asf)
HEALTH=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 http://127.0.0.1:8080/health)
TB=$(journalctl -u asf --since "-60 seconds" --no-pager | grep -c 'Traceback (most recent call last)')
LOOP=$(journalctl -u asf --since "-60 seconds" --no-pager | grep -c 'residential autocheck loop')
echo "is-active=$ACT health=$HEALTH tracebacks=$TB autocheck_loop_started=$LOOP"

if [ "$ACT" = "active" ] && [ "$HEALTH" = "200" ] && [ "$TB" = "0" ] && [ "$LOOP" -ge "1" ]; then
  for f in $MOD $NEWF; do rm -f "/opt/asf/app/$f.new_auto_${STAMP}"; done
  echo DEPLOY_OK
else
  echo "[$(date -Is)] verification FAILED -> rollback"
  for f in $MOD; do cp -f "$D/$f" "/opt/asf/app/$f"; done
  rm -f "/opt/asf/app/${NEWF}"
  systemctl restart asf
  sleep 18
  echo "post-rollback is-active=$(systemctl is-active asf) health=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 http://127.0.0.1:8080/health)"
  echo ROLLED_BACK
fi
echo "[$(date -Is)] done"
