#!/bin/bash
# Deploy the "проверить и заменить нерабочие" residential feature to /opt/asf
# (app/{clients,db,handlers_user,keyboards}.py): back up, compile candidates,
# swap, restart asf, verify (health + no tracebacks + new table created), and
# auto-restore the backups on any failure.
set -u
STAMP="$1"
cd /opt/asf || exit 1
D="/opt/asf/backups/resi-recheck-${STAMP}"
mkdir -p "$D"
LOG="$D/deploy.log"
exec >"$LOG" 2>&1
FILES="${DEPLOY_FILES:-clients.py db.py handlers_user.py keyboards.py}"

for f in $FILES; do
  cp -p "/opt/asf/app/$f" "$D/$f" || { echo "BACKUP_FAIL $f"; exit 1; }
  [ -f "/opt/asf/app/$f.new_recheck_${STAMP}" ] || { echo "MISSING_CANDIDATE $f"; exit 1; }
done

echo "[$(date -Is)] compile candidates"
for f in $FILES; do
  .venv/bin/python -m py_compile "/opt/asf/app/$f.new_recheck_${STAMP}" || { echo "COMPILE_FAIL_CANDIDATE $f"; exit 1; }
done

for f in $FILES; do cp -f "/opt/asf/app/$f.new_recheck_${STAMP}" "/opt/asf/app/$f"; done
.venv/bin/python -m py_compile app/*.py || {
  echo "COMPILE_FAIL_AFTER_SWAP -> restoring"
  for f in $FILES; do cp -f "$D/$f" "/opt/asf/app/$f"; done
  echo RESTORED_NO_RESTART; exit 1;
}

echo "[$(date -Is)] systemctl restart asf"
systemctl restart asf
sleep 22

ACT=$(systemctl is-active asf)
HEALTH=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 http://127.0.0.1:8080/health)
FATAL=$(journalctl -u asf --since "-90 seconds" --no-pager | grep -cE 'ModuleNotFoundError|ImportError|SyntaxError|NameError|cannot import name')
sleep 12
ACT2=$(systemctl is-active asf)
TABLE=$(.venv/bin/python -c "import sqlite3,glob; p=glob.glob('/opt/asf/data/shop.db')[0]; c=sqlite3.connect(p); print(c.execute(\"SELECT count(*) FROM sqlite_master WHERE type='table' AND name='residential_deliveries'\").fetchone()[0])" 2>/dev/null)
echo "is-active=$ACT/$ACT2 health=$HEALTH fatal=$FATAL residential_deliveries_table=$TABLE"

if [ "$ACT" = "active" ] && [ "$ACT2" = "active" ] && [ "$HEALTH" = "200" ] && [ "$FATAL" = "0" ] && [ "$TABLE" = "1" ]; then
  for f in $FILES; do rm -f "/opt/asf/app/$f.new_recheck_${STAMP}"; done
  echo DEPLOY_OK
else
  echo "[$(date -Is)] verification FAILED -> rollback"
  for f in $FILES; do cp -f "$D/$f" "/opt/asf/app/$f"; done
  systemctl restart asf
  sleep 18
  echo "post-rollback is-active=$(systemctl is-active asf) health=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 http://127.0.0.1:8080/health)"
  echo ROLLED_BACK
fi
echo "[$(date -Is)] done"
