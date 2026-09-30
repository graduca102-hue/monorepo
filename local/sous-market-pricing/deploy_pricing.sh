#!/bin/bash
# Deploy admin-editable pricing knobs to /opt/botshop:
#   database/data.py  - main_bot_settings pricing columns + get/update helpers
#   miniapp.py        - resolve markups/residential price via the new helpers
#   keyboard.py       - same helpers + "⚙️ Цены и наценки" admin panel section
# Back up, compile candidates, wait for the operator reply to flush, swap,
# recompile the whole package, restart botshop, health-check, and auto-restore
# all three files if the bot does not come back clean.
set -u
STAMP="$1"
cd /opt/botshop || exit 1
D="/opt/botshop/backups/pricing-${STAMP}"
mkdir -p "$D" "$D/database"
LOG="$D/deploy.log"
exec >"$LOG" 2>&1

FILES=("miniapp.py" "keyboard.py" "database/data.py")

echo "[$(date -Is)] backup"
for f in "${FILES[@]}"; do
  cp -p "/opt/botshop/$f" "$D/$f" || { echo "BACKUP_FAIL $f"; exit 1; }
done

echo "[$(date -Is)] compile candidates"
for f in "${FILES[@]}"; do
  c="/opt/botshop/$f.new_pricing_${STAMP}"
  [ -f "$c" ] || { echo "MISSING_CANDIDATE $f"; exit 1; }
  .venv/bin/python -m py_compile "$c" || { echo "COMPILE_FAIL_CANDIDATE $f"; exit 1; }
done

echo "[$(date -Is)] wait 20s so the operator's Telegram reply flushes"
sleep 20

for f in "${FILES[@]}"; do
  cp -f "/opt/botshop/$f.new_pricing_${STAMP}" "/opt/botshop/$f"
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
COL=$(.venv/bin/python -c "import sqlite3;c=sqlite3.connect('data1.db');print(int(any(r[1]=='api_markup_percent' for r in c.execute('PRAGMA table_info(main_bot_settings)'))))" 2>/dev/null)
echo "is-active=$ACT health=$HEALTH categories=$CATS tracebacks=$TB pricing_column=$COL"

if [ "$ACT" = "active" ] && [ "$HEALTH" = "200" ] && [ "$CATS" = "200" ] && [ "$TB" = "0" ] && [ "$COL" = "1" ]; then
  rm -f /opt/botshop/miniapp.py.new_pricing_${STAMP} /opt/botshop/keyboard.py.new_pricing_${STAMP} /opt/botshop/database/data.py.new_pricing_${STAMP}
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
