#!/bin/bash
# Deploy the botshop vproxy_service.py SQLite fd-leak fix:
#   `with sqlite3.connect(...) as db:` -> `with _vproxy_conn() as db:` (43 sites)
#   plus the _vproxy_conn() context manager that actually closes the handle.
# Back up, compile candidate, wait, swap, compile, restart botshop, health-check,
# auto-restore on failure.
set -u
STAMP="$1"
cd /opt/botshop || exit 1
D="/opt/botshop/backups/fdfix-${STAMP}"
mkdir -p "$D"
LOG="$D/deploy.log"
exec >"$LOG" 2>&1
FILES="vproxy_service.py"

for f in $FILES; do
  cp -p "$f" "$D/$f" || { echo "BACKUP_FAIL $f"; exit 1; }
  [ -f "$f.new_fdfix_${STAMP}" ] || { echo "MISSING_CANDIDATE $f"; exit 1; }
done

echo "[$(date -Is)] compile candidate"
for f in $FILES; do
  .venv/bin/python -m py_compile "$f.new_fdfix_${STAMP}" || { echo "COMPILE_FAIL_CANDIDATE $f"; exit 1; }
done

echo "[$(date -Is)] wait 20s so the operator's Telegram reply flushes"
sleep 20

for f in $FILES; do cp -f "$f.new_fdfix_${STAMP}" "$f"; done
.venv/bin/python -m py_compile vproxy_service.py miniapp.py main.py || {
  echo "COMPILE_FAIL_AFTER_SWAP -> restoring"
  for f in $FILES; do cp -f "$D/$f" "$f"; done
  echo RESTORED_NO_RESTART; exit 1;
}

echo "[$(date -Is)] import smoke test"
.venv/bin/python -c "import vproxy_service; print('import ok', hasattr(vproxy_service, '_vproxy_conn'))" || {
  echo "IMPORT_FAIL -> restoring"
  for f in $FILES; do cp -f "$D/$f" "$f"; done
  echo RESTORED_NO_RESTART; exit 1;
}

echo "[$(date -Is)] systemctl restart botshop"
systemctl restart botshop
sleep 12

ACT=$(systemctl is-active botshop)
HP=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 http://127.0.0.1:8081/api/v1/health)
TB=$(journalctl -u botshop --since "-45 seconds" --no-pager | grep -c 'Traceback (most recent call last)')
PID=$(systemctl show -p MainPID --value botshop)
FDS=$(ls /proc/$PID/fd 2>/dev/null | wc -l)
echo "is-active=$ACT health=$HP tracebacks=$TB fds=$FDS"

if [ "$ACT" = "active" ] && [ "$HP" = "200" ] && [ "$TB" = "0" ]; then
  for f in $FILES; do rm -f "$f.new_fdfix_${STAMP}"; done
  echo DEPLOY_OK
else
  echo "[$(date -Is)] verification FAILED -> rollback"
  for f in $FILES; do cp -f "$D/$f" "$f"; done
  systemctl restart botshop
  sleep 12
  echo "post-rollback is-active=$(systemctl is-active botshop) health=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 http://127.0.0.1:8081/api/v1/health)"
  echo ROLLED_BACK
fi
echo "[$(date -Is)] done"
