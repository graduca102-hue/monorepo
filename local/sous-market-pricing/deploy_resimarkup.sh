#!/bin/bash
# Residential traffic: switch from two absolute-price knobs to one base price +
# the existing markup knobs.
#   database/data.py - drop the two residential price keys, add residential_base_price_per_gb
#   keyboard.py      - one "базовая цена" field in the panel
#   miniapp.py       - api_residential_price_per_gb() = base * (1 + api markup)
#   main.py          - bot residential base = base * (1 + proxy SOUS MARKET markup)
set -u
STAMP="$1"
cd /opt/botshop || exit 1
D="/opt/botshop/backups/resimarkup-${STAMP}"
mkdir -p "$D" "$D/database"
LOG="$D/deploy.log"
exec >"$LOG" 2>&1

FILES=("main.py" "keyboard.py" "miniapp.py" "database/data.py")

echo "[$(date -Is)] backup"
for f in "${FILES[@]}"; do
  cp -p "/opt/botshop/$f" "$D/$f" || { echo "BACKUP_FAIL $f"; exit 1; }
done

echo "[$(date -Is)] compile candidates"
for f in "${FILES[@]}"; do
  c="/opt/botshop/$f.new_resimarkup_${STAMP}"
  [ -f "$c" ] || { echo "MISSING_CANDIDATE $f"; exit 1; }
  .venv/bin/python -m py_compile "$c" || { echo "COMPILE_FAIL_CANDIDATE $f"; exit 1; }
done

echo "[$(date -Is)] wait 20s so the operator's Telegram reply flushes"
sleep 20

for f in "${FILES[@]}"; do
  cp -f "/opt/botshop/$f.new_resimarkup_${STAMP}" "/opt/botshop/$f"
done

.venv/bin/python -m py_compile miniapp.py keyboard.py services.py main.py database/data.py || {
  echo "COMPILE_FAIL_AFTER_SWAP -> restoring"
  for f in "${FILES[@]}"; do cp -f "$D/$f" "/opt/botshop/$f"; done
  echo RESTORED_NO_RESTART; exit 1;
}

echo "[$(date -Is)] systemctl restart botshop"
systemctl restart botshop
sleep 25

ACT=$(systemctl is-active botshop)
HEALTH=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 http://127.0.0.1:8081/api/v1/health)
CATS=$(curl -s -o /dev/null -w '%{http_code}' --max-time 25 http://127.0.0.1:8081/market/api/categories)
TB=$(journalctl -u botshop --since "-55 seconds" --no-pager | grep -c 'Traceback (most recent call last)')
COL=$(.venv/bin/python -c "import sqlite3;c=sqlite3.connect('data1.db');print(int(any(r[1]=='residential_base_price_per_gb' for r in c.execute('PRAGMA table_info(main_bot_settings)'))))" 2>/dev/null)
BOT_P=$(.venv/bin/python -c "import main;print(main.get_proxy_traffic_sale_price('residential',1,None))" 2>/dev/null)
API_P=$(.venv/bin/python -c "import miniapp;print(miniapp.api_residential_price_per_gb())" 2>/dev/null)
echo "is-active=$ACT health=$HEALTH categories=$CATS tracebacks=$TB column=$COL bot_resi=$BOT_P api_resi=$API_P"

if [ "$ACT" = "active" ] && [ "$HEALTH" = "200" ] && [ "$CATS" = "200" ] && [ "$TB" = "0" ] && [ "$COL" = "1" ]; then
  for f in "${FILES[@]}"; do rm -f "/opt/botshop/$f.new_resimarkup_${STAMP}"; done
  echo DEPLOY_OK
else
  echo "[$(date -Is)] verification FAILED -> rollback"
  for f in "${FILES[@]}"; do cp -f "$D/$f" "/opt/botshop/$f"; done
  systemctl restart botshop
  sleep 20
  echo "post-rollback is-active=$(systemctl is-active botshop) health=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 http://127.0.0.1:8081/api/v1/health)"
  echo ROLLED_BACK
fi
echo "[$(date -Is)] done"
