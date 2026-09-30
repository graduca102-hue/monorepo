#!/bin/bash
# Deploy ASF residential v3 (proxy hub shows the customer's OWN balance, not the
# shared pool; don't provision an upstream client for users with no purchase):
# handlers_user.py only.
set -u
STAMP="$1"
cd /opt/asf || exit 1
D="/opt/asf/backups/resi3-${STAMP}"
mkdir -p "$D/app"
LOG="$D/deploy.log"
exec >"$LOG" 2>&1
f="handlers_user.py"

cp -p "app/$f" "$D/app/$f" || { echo "BACKUP_FAIL"; exit 1; }
[ -f "app/$f.new_resi_${STAMP}" ] || { echo "MISSING_CANDIDATE"; exit 1; }
cp -f "app/$f.new_resi_${STAMP}" "/tmp/_r3_$f"
.venv/bin/python -m py_compile "/tmp/_r3_$f" || { echo "COMPILE_FAIL_CANDIDATE"; exit 1; }

sleep 12
cp -f "app/$f.new_resi_${STAMP}" "app/$f"
.venv/bin/python -m py_compile app/*.py && .venv/bin/python -c "import app.main" || {
  echo "FAIL_AFTER_SWAP -> restoring"; cp -f "$D/app/$f" "app/$f"; echo RESTORED_NO_RESTART; exit 1;
}

echo "[$(date -Is)] systemctl restart asf"
systemctl restart asf
sleep 12
ACT=$(systemctl is-active asf)
HP=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 http://127.0.0.1:8080/health)
TB=$(journalctl -u asf --since "-45 seconds" --no-pager | grep -c 'Traceback (most recent call last)')
echo "is-active=$ACT health=$HP tracebacks=$TB"
if [ "$ACT" = "active" ] && [ "$HP" = "200" ] && [ "$TB" = "0" ]; then
  rm -f "app/$f.new_resi_${STAMP}"; echo DEPLOY_OK
else
  echo "rollback"; cp -f "$D/app/$f" "app/$f"; systemctl restart asf; sleep 10
  echo "post-rollback is-active=$(systemctl is-active asf)"; echo ROLLED_BACK
fi
echo done
