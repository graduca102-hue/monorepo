#!/bin/bash
# Deploy the partner-cabinet UTM screen to /opt/botshop with auto-rollback.
# Usage: deploy_utm.sh <backup_timestamp>
set -u
TS="$1"
BK="/opt/botshop/backups/partner-utm-$TS"
cd /opt/botshop || exit 2
PY=.venv/bin/python
LOG="/tmp/deploy_utm_$TS.log"
exec > >(tee -a "$LOG") 2>&1

echo "[$(date +%T)] deploy start (backup: $BK)"

for f in miniapp.py.new database/data.py.new partner_static/app.js.new partner_static/styles.css.new; do
  [ -f "$f" ] || { echo "MISSING staged file: $f"; exit 3; }
done

mkdir -p "$BK"
cp -f miniapp.py "$BK/miniapp.py"
cp -f database/data.py "$BK/data.py"
cp -f partner_static/app.js "$BK/app.js"
cp -f partner_static/styles.css "$BK/styles.css"

$PY -m py_compile miniapp.py.new database/data.py.new || { echo "COMPILE FAIL - nothing swapped"; exit 4; }

restore() {
  echo "[$(date +%T)] ROLLBACK"
  cp -f "$BK/miniapp.py" miniapp.py
  cp -f "$BK/data.py" database/data.py
  cp -f "$BK/app.js" partner_static/app.js
  cp -f "$BK/styles.css" partner_static/styles.css
  systemctl restart botshop
  sleep 10
  echo "rollback restart: $(systemctl is-active botshop)"
}

cp -f miniapp.py.new miniapp.py
cp -f database/data.py.new database/data.py
cp -f partner_static/app.js.new partner_static/app.js
cp -f partner_static/styles.css.new partner_static/styles.css
rm -f miniapp.py.new database/data.py.new partner_static/app.js.new partner_static/styles.css.new
echo "[$(date +%T)] files swapped, restarting botshop"

SINCE="$(date '+%Y-%m-%d %H:%M:%S')"
systemctl restart botshop
sleep 45

STATE="$(systemctl is-active botshop)"
HTTP="$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8081/partner/souspartnersbot/)"
# The UTM endpoint needs Telegram auth: 401/403 proves the route exists, 404 means it did not register.
UTM="$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8081/partner/souspartnersbot/api/utm)"
CRASH="$(journalctl -u botshop --since "$SINCE" --no-pager 2>/dev/null | grep -cE 'ModuleNotFoundError|ImportError|SyntaxError|NameError|cannot import name')"

echo "[$(date +%T)] state=$STATE http=$HTTP utm=$UTM crash_lines=$CRASH"

if [ "$STATE" = "active" ] && [ "$HTTP" = "200" ] && [ "$UTM" != "404" ] && [ "$UTM" != "000" ] && [ "$CRASH" = "0" ]; then
  echo "DEPLOY OK"
  exit 0
fi

echo "DEPLOY BAD -> rolling back"
journalctl -u botshop --since "$SINCE" --no-pager 2>/dev/null | grep -E 'Error|Traceback|import' | tail -20
restore
echo "DEPLOY ROLLED BACK"
exit 1
