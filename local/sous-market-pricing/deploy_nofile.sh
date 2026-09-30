#!/bin/bash
# Raise RLIMIT_NOFILE soft limit at botshop startup (main.py) from 1024 -> 65536.
# The vproxy_users.db fd leak was already fixed separately (2026-09-06 14:52,
# _vproxy_conn); this only adds headroom so a future leak can't silently break
# broadcasts / the /schedule loop / the :8081 API by hitting the ceiling.
set -u
STAMP="$1"
cd /opt/botshop || exit 1
D="/opt/botshop/backups/nofile-${STAMP}"
mkdir -p "$D"
LOG="$D/deploy.log"
exec >"$LOG" 2>&1

f="main.py"
cp -p "/opt/botshop/$f" "$D/$f" || { echo BACKUP_FAIL; exit 1; }
c="/opt/botshop/$f.new_nofile_${STAMP}"
[ -f "$c" ] || { echo MISSING_CANDIDATE; exit 1; }
.venv/bin/python -m py_compile "$c" || { echo COMPILE_FAIL_CANDIDATE; exit 1; }

echo "[$(date -Is)] wait 20s"
sleep 20
cp -f "$c" "/opt/botshop/$f"
.venv/bin/python -m py_compile main.py miniapp.py keyboard.py services.py database/data.py || {
  echo "COMPILE_FAIL_AFTER_SWAP -> restoring"; cp -f "$D/$f" "/opt/botshop/$f"; echo RESTORED_NO_RESTART; exit 1; }

echo "[$(date -Is)] systemctl restart botshop"
systemctl restart botshop
sleep 25

ACT=$(systemctl is-active botshop)
HEALTH=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 http://127.0.0.1:8081/api/v1/health)
CATS=$(curl -s -o /dev/null -w '%{http_code}' --max-time 25 http://127.0.0.1:8081/market/api/categories)
TB=$(journalctl -u botshop --since "-55 seconds" --no-pager | grep -c 'Traceback (most recent call last)')
PID=$(systemctl show -p MainPID --value botshop)
SOFT=$(cat /proc/$PID/limits | awk '/open files/{print $4}')
echo "is-active=$ACT health=$HEALTH categories=$CATS tracebacks=$TB nofile_soft=$SOFT"

if [ "$ACT" = "active" ] && [ "$HEALTH" = "200" ] && [ "$CATS" = "200" ] && [ "$TB" = "0" ] && [ "$SOFT" = "65536" ]; then
  rm -f "$c"; echo DEPLOY_OK
else
  echo "[$(date -Is)] FAILED -> rollback"; cp -f "$D/$f" "/opt/botshop/$f"; systemctl restart botshop; sleep 20
  echo "post-rollback is-active=$(systemctl is-active botshop) health=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 http://127.0.0.1:8081/api/v1/health)"
  echo ROLLED_BACK
fi
echo "[$(date -Is)] done"
