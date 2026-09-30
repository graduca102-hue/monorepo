#!/bin/bash
# Deploy the "Тексты и кнопки" change to /opt/botshop with auto-rollback.
# Usage: deploy_texts.sh <backup_timestamp>
set -u
TS="$1"
BK="/opt/botshop/backups/partner-texts-$TS"
cd /opt/botshop || exit 2
PY=.venv/bin/python
LOG="/tmp/deploy_texts_$TS.log"
exec > >(tee -a "$LOG") 2>&1

echo "[$(date +%T)] deploy start (backup: $BK)"

FILES_LIVE=(main.py keyboard.py miniapp.py database/data.py partner_static/app.js)
FILES_NEW=(main.py.new keyboard.py.new miniapp.py.new database/data.py.new partner_static/app.js.new)

for f in "${FILES_NEW[@]}" partner_texts.py.new; do
  [ -f "$f" ] || { echo "MISSING staged file: $f"; exit 3; }
done

# compile-check staged
$PY -m py_compile partner_texts.py.new "${FILES_NEW[@]/%.new/}" 2>/dev/null
$PY -m py_compile partner_texts.py.new main.py.new keyboard.py.new miniapp.py.new database/data.py.new || { echo "COMPILE FAIL - aborting, nothing swapped"; exit 4; }

restore() {
  echo "[$(date +%T)] ROLLBACK"
  cp -f "$BK/main.py" main.py
  cp -f "$BK/keyboard.py" keyboard.py
  cp -f "$BK/miniapp.py" miniapp.py
  cp -f "$BK/data.py" database/data.py
  cp -f "$BK/app.js" partner_static/app.js
  rm -f partner_texts.py
  systemctl restart botshop
  sleep 8
  echo "rollback restart: $(systemctl is-active botshop)"
}

# swap in
cp -f partner_texts.py.new partner_texts.py
cp -f main.py.new main.py
cp -f keyboard.py.new keyboard.py
cp -f miniapp.py.new miniapp.py
cp -f database/data.py.new database/data.py
cp -f partner_static/app.js.new partner_static/app.js
rm -f partner_texts.py.new *.new database/data.py.new partner_static/app.js.new
echo "[$(date +%T)] files swapped, restarting botshop"

SINCE="$(date '+%Y-%m-%d %H:%M:%S')"
systemctl restart botshop
sleep 45

STATE="$(systemctl is-active botshop)"
HTTP="$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8081/partner/souspartnersbot/)"
CRASH="$(journalctl -u botshop --since "$SINCE" --no-pager 2>/dev/null | grep -cE 'ModuleNotFoundError|ImportError|SyntaxError|NameError|AttributeError:.*partner_text|cannot import name')"

echo "[$(date +%T)] state=$STATE http=$HTTP crash_lines=$CRASH"

if [ "$STATE" = "active" ] && [ "$HTTP" = "200" ] && [ "$CRASH" = "0" ]; then
  # confirm the migration created the table
  TBL="$($PY -c "import sqlite3;print(sqlite3.connect('data1.db').execute(\"select count(*) from sqlite_master where name='partner_bot_texts'\").fetchone()[0])" 2>/dev/null)"
  echo "[$(date +%T)] partner_bot_texts table present: $TBL"
  if [ "$TBL" = "1" ]; then
    echo "DEPLOY OK"
    exit 0
  fi
fi

echo "DEPLOY BAD -> rolling back"
journalctl -u botshop --since "$SINCE" --no-pager 2>/dev/null | grep -E 'Error|Traceback|import' | tail -20
restore
echo "DEPLOY ROLLED BACK"
exit 1
