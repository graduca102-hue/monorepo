#!/bin/bash
# Deploy the StrikeProxy provider into /opt/asf.
#
# Staged files are expected in /opt/asf/_stage_sp. Every touched file is backed
# up, the new tree is byte-compiled before it goes live, and the service is
# rolled back automatically if it does not come back healthy.
set -u

APP=/opt/asf/app
STAGE=/opt/asf/_stage_sp
STAMP=$(date +%Y%m%d_%H%M%S)
BACKUP=/opt/asf/backups/$STAMP
PY=/opt/asf/.venv/bin/python

FILES="clients.py dataimpulse.py db.py handlers_admin.py handlers_user.py keyboards.py main.py states.py strikeproxy.py"

echo "== staging check"
for f in $FILES; do
  [ -f "$STAGE/$f" ] || { echo "missing stage file: $f"; exit 1; }
done
$PY -m py_compile $(for f in $FILES; do echo "$STAGE/$f"; done) || { echo "stage does not compile"; exit 1; }

echo "== backup -> $BACKUP"
mkdir -p "$BACKUP"
cp -a "$APP"/*.py "$BACKUP"/ || exit 1

echo "== install"
for f in $FILES; do cp -f "$STAGE/$f" "$APP/$f"; done
rm -rf "$APP/__pycache__"

$PY -m py_compile "$APP"/*.py || {
  echo "compile failed after install, restoring"
  cp -f "$BACKUP"/*.py "$APP"/; rm -f "$APP/strikeproxy.py"; rm -rf "$APP/__pycache__"; exit 1
}

echo "== restart"
systemctl restart asf
sleep 12

ok=1
systemctl is-active --quiet asf || ok=0
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:8080/health)
[ "$code" = "200" ] || ok=0
if journalctl -u asf --since "-1 min" --no-pager | grep -q "Traceback"; then ok=0; fi

if [ "$ok" != "1" ]; then
  echo "!! unhealthy (active=$(systemctl is-active asf) health=$code) — rolling back"
  cp -f "$BACKUP"/*.py "$APP"/
  # strikeproxy.py is new in this deploy, so the restored tree must not keep it.
  [ -f "$BACKUP/strikeproxy.py" ] || rm -f "$APP/strikeproxy.py"
  rm -rf "$APP/__pycache__"
  systemctl restart asf
  sleep 10
  echo "rollback active=$(systemctl is-active asf) health=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:8080/health)"
  journalctl -u asf --since "-2 min" --no-pager | tail -30
  exit 1
fi

echo "== OK  active=$(systemctl is-active asf) health=$code  backup=$BACKUP"
