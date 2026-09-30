#!/bin/bash
# Deploy the sqlite WAL change to /opt/botshop/database/data.py:
# back up, compile, wait for the operator reply to flush, swap, restart botshop,
# health-check (service up, HTTP 200, DB readable in WAL mode, no traceback),
# and auto-restore the backup if the bot does not come back clean.
set -u
STAMP="$1"
cd /opt/botshop || exit 1
D="/opt/botshop/backups/db-wal-${STAMP}"
mkdir -p "$D"
LOG="$D/deploy.log"
exec >"$LOG" 2>&1
BAK="$D/data.py"
NEW="/opt/botshop/database/data.py.new_wal_${STAMP}"
TARGET="/opt/botshop/database/data.py"

echo "[$(date -Is)] backup $TARGET -> $BAK"
cp -p "$TARGET" "$BAK" || { echo BACKUP_FAIL; exit 1; }

echo "[$(date -Is)] compile candidate"
.venv/bin/python -m py_compile "$NEW" || { echo COMPILE_FAIL_CANDIDATE; exit 1; }

echo "[$(date -Is)] wait 20s so the operator's Telegram reply flushes"
sleep 20

cp -f "$NEW" "$TARGET"
.venv/bin/python -m py_compile database/data.py main.py miniapp.py services.py keyboard.py || {
  echo "COMPILE_FAIL_AFTER_SWAP -> restoring"; cp -f "$BAK" "$TARGET"; echo RESTORED_NO_RESTART; exit 1;
}

echo "[$(date -Is)] systemctl restart botshop"
systemctl restart botshop
sleep 25

ACT=$(systemctl is-active botshop)
HEALTH=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 http://127.0.0.1:8081/api/v1/health)
CATS=$(curl -s -o /dev/null -w '%{http_code}' --max-time 25 http://127.0.0.1:8081/market/api/categories)
TB=$(journalctl -u botshop --since "-55 seconds" --no-pager | grep -c 'Traceback (most recent call last)')
JMODE=$(.venv/bin/python -c "import sqlite3;print(list(sqlite3.connect('data1.db').execute('PRAGMA journal_mode'))[0][0])" 2>&1)
USERS=$(.venv/bin/python -c "import sqlite3;print(list(sqlite3.connect('data1.db').execute('SELECT count(*) FROM users'))[0][0])" 2>&1)
echo "is-active=$ACT health=$HEALTH categories=$CATS tracebacks=$TB journal_mode=$JMODE users=$USERS"

if [ "$ACT" = "active" ] && [ "$HEALTH" = "200" ] && [ "$CATS" = "200" ] && [ "$TB" = "0" ] && [ "$JMODE" = "wal" ]; then
  rm -f "$NEW"
  echo DEPLOY_OK
else
  echo "[$(date -Is)] verification FAILED -> rollback to $BAK"
  cp -f "$BAK" "$TARGET"
  systemctl restart botshop
  sleep 20
  echo "post-rollback is-active=$(systemctl is-active botshop) health=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 http://127.0.0.1:8081/api/v1/health)"
  echo ROLLED_BACK
fi
echo "[$(date -Is)] done"
